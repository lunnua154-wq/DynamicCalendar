"""Seed a fresh database with the sample fixtures in `samples/`.

Usage:
    python scripts/seed.py

Creates (or resets) ``data/dynacalendar.db``, imports
``samples/sample_calendar.ics``, and converts the four sample materials.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app import db, ics_sync  # noqa: E402
from app.main import DEFAULT_DB_PATH  # noqa: E402

DATA_DIR = BASE_DIR / "data"
MATERIALS_DIR = DATA_DIR / "materials"
SAMPLES_DIR = BASE_DIR / "samples"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MATERIALS_DIR.mkdir(parents=True, exist_ok=True)

    db_path = DATA_DIR / "dynacalendar.db"
    if db_path.exists():
        db_path.unlink()  # fresh seed

    conn = db.connect(str(db_path))
    db.init_db(conn)

    # 1. import the sample calendar (external events, read-only)
    ics_path = SAMPLES_DIR / "sample_calendar.ics"
    with open(ics_path, "rb") as fh:

        class F:
            def read(self):
                return fh.read()

            name = ics_path.name

        result = ics_sync.import_ics(F(), db_path=str(db_path))
    print(f"ICS import: {result['imported']} imported, {result['skipped']} skipped")
    if result["exams_found"]:
        print(f"Exam detected: {result['exams_found']}")

    # 2. convert + register the sample materials (offline tag path)
    from app.llm import LLMClient
    from app.config import parse_settings

    settings = parse_settings(db.list_settings(conn))
    client = LLMClient(settings)

    materials_dir = SAMPLES_DIR / "materials"
    for md in sorted(materials_dir.glob("*.md")):
        raw = md.read_bytes()
        content_hash = hashlib.sha256(raw).hexdigest()
        if db.find_material_by_hash(conn, content_hash):
            print(f"skip {md.name}: already ingested")
            continue

        original_path = MATERIALS_DIR / f"{db.new_id()}_{md.name}"
        original_path.write_bytes(raw)

        markdown_text = raw.decode("utf-8", errors="replace")
        markdown_path = MATERIALS_DIR / f"{db.new_id()}.md"
        markdown_path.write_text(markdown_text, encoding="utf-8")

        tagged = client.tag(markdown=markdown_text, filename=md.name)
        tags_json = json.dumps(tagged["tags"])

        # Topics require a goal (NOT NULL per schema); unattached materials keep
        # topic_id None until the user picks a goal in the Inbox.
        topic_id = None
        goal_rows = conn.execute("SELECT id FROM goals LIMIT 1").fetchall()
        if goal_rows:
            topic_id = db.create_topic(
                conn, goal_id=goal_rows[0]["id"],
                name=tagged["tags"][0] if tagged["tags"] else md.stem,
            )

        db.create_material(
            conn,
            goal_id=None,
            topic_id=topic_id,
            filename=md.name,
            original_path=str(original_path),
            markdown_path=str(markdown_path),
            content_hash=content_hash,
            summary=tagged["summary"],
            tags=tags_json,
            effort_hours=tagged["effort_hours"],
            status="tagged",
        )
        print(f"material {md.name}: {tagged['summary']} ({tagged['effort_hours']}h)")

    conn.commit()
    conn.close()
    print(f"\nSeed complete -> {db_path}")


if __name__ == "__main__":
    main()
