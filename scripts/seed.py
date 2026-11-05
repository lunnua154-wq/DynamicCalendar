"""Seed a fresh database with the sample fixtures in ``samples/``.

Usage::

    python scripts/seed.py

Creates (or resets) ``data/dynacalendar.db``, imports
``samples/sample_calendar.ics``, opens a goal from the exam event and runs the
four sample materials through the ingest pipeline (offline LLM fallbacks).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app import db, engine, ics_sync, ingest  # noqa: E402
from app.config import parse_settings  # noqa: E402
from app.llm import LLMClient  # noqa: E402

DATA_DIR = BASE_DIR / "data"
MATERIALS_DIR = DATA_DIR / "materials"
SAMPLES_DIR = BASE_DIR / "samples"
DB_PATH = DATA_DIR / "dynacalendar.db"

#: Sample materials that belong to the MATH2001 goal (the CS lecture stays
#: unattached on purpose — it exercises the "unattached material" state).
GOAL_MATERIALS = {
    "math_chapter3_limits.md",
    "math_chapter3_limits_v2.md",
    "problem_set_3.md",
}


def _now() -> str:
    return db.now_utc()


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if MATERIALS_DIR.exists():
        shutil.rmtree(MATERIALS_DIR)
    MATERIALS_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = db.connect(DB_PATH)
    db.init_db(conn)

    # 1. external calendar (read-only)
    ics_path = SAMPLES_DIR / "sample_calendar.ics"

    class File:
        name = ics_path.name

        def read(self) -> bytes:
            return ics_path.read_bytes()

    result = ics_sync.import_ics(File(), db_path=str(DB_PATH))
    print(f"ICS import: {result['imported']} imported, {result['skipped']} skipped")

    # 2. goal from the exam event
    exam = result["exam_events"][0] if result["exam_events"] else None
    goal_id = None
    if exam:
        goal_id = db.create_goal(
            conn, title="MATH2001", deadline=exam["dtstart"], exam_event_id=exam["id"]
        )
        conn.commit()
        print(f"Goal: MATH2001 (deadline {exam['dtstart']}, exam {exam['title']})")

    # 3. materials through the real ingest pipeline (offline fallbacks)
    settings = parse_settings(db.list_settings(conn))
    client = LLMClient(settings)
    for path in sorted((SAMPLES_DIR / "materials").glob("*.md")):
        target_goal = goal_id if path.name in GOAL_MATERIALS else None
        stored = ingest.store_upload(
            conn,
            raw=path.read_bytes(),
            filename=path.name,
            goal_id=target_goal,
            originals_dir=MATERIALS_DIR / "originals",
        )
        conn.commit()
        if stored["status"] == "processing":
            material = ingest.process_material(
                conn,
                stored["material_id"],
                markdown_dir=MATERIALS_DIR / "markdown",
                client=client,
                settings=settings,
                now_iso=_now(),
            )
            conn.commit()
            attachment = "attached" if material["goal_id"] else "unattached"
            print(
                f"material {material['filename']}: {material['status']}, "
                f"{material['effort_hours']}h, topic {material['topic_name']} ({attachment})"
            )
        else:
            print(f"material {path.name}: {stored['status']}")

    # 4. plan
    for goal in db.list_goals(conn, status="open"):
        plan = engine.replan(conn, goal["id"], _now())
        conn.commit()
        print(f"planned goal {goal['title']}: {len(plan['revisions_added'])} revision job(s)")

    jobs = db.list_jobs(conn)
    names = {m["id"]: m["filename"] for m in db.list_materials(conn)}
    print(f"\n{len(jobs)} job(s) scheduled:")
    for job in jobs[:20]:
        label = names.get(job["material_id"], job["material_id"])
        print(f"  {job['slot_start']}  {job['kind']:<8} {label}")

    conn.close()
    print(f"\nSeed complete -> {DB_PATH}")
    print("Run: python -m app.main  ->  http://127.0.0.1:8000")


if __name__ == "__main__":
    main()
