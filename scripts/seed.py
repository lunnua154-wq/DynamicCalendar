"""Seed a fresh database with the sample fixtures in `samples/`.

Usage:
    python scripts/seed.py

Creates (or resets) ``data/dynacalendar.db`` and drives the **real** pipeline:
import ``samples/sample_calendar.ics``, open a goal from the exam event, ingest
the four sample materials through ``app.ingest`` (conversion + tag + cluster +
topic), then schedule every open goal.

Everything is deterministic and offline (no LLM endpoint required); with an
endpoint configured in Settings the same script uses it instead.
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
SAMPLES_DIR = BASE_DIR / "samples"
DB_PATH = DATA_DIR / "dynacalendar.db"
#: Same folder the API uses for originals + converted markdown.
MATERIALS_DIR = DATA_DIR / "materials"

#: The CS lecture has no matching goal on purpose: it exercises the documented
#: "unattached material" state (docs/WALKTHROUGH.md).
UNATTACHED = {"cs_lecture08_graphs.md"}


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

    class Upload:
        name = ics_path.name

        def read(self) -> bytes:
            return ics_path.read_bytes()

    result = ics_sync.import_ics(Upload(), db_path=str(DB_PATH))
    print(f"ICS import: {result['imported']} imported, {result['skipped']} skipped")

    # 2. goal from the exam event (deadline = exam start, linked to the event)
    exams = result.get("exams") or []
    goal_id = None
    if exams:
        exam = exams[0]
        goal_id = db.create_goal(
            conn,
            title="MATH2001",
            deadline=f"{exam['dtstart'][:10]}T00:00:00Z",
            exam_event_id=exam["event_id"],
        )
        conn.commit()
        print(f"Goal: MATH2001 (deadline {exam['dtstart'][:10]}, exam {exam['title']})")
    else:
        print("No EXAM-tagged event found; materials will be unattached")

    # 3. materials through the real ingest pipeline (offline fallbacks by default)
    settings = parse_settings(db.list_settings(conn))
    client = LLMClient(settings)
    topics_before = len(db.list_topics(conn))
    for path in sorted((SAMPLES_DIR / "materials").glob("*.md")):
        attach_to = None if path.name in UNATTACHED else goal_id
        stored = ingest.store_upload(
            conn,
            raw=path.read_bytes(),
            filename=path.name,
            goal_id=attach_to,
            originals_dir=MATERIALS_DIR,
        )
        if stored["status"] == "duplicate":
            print(f"material {path.name}: duplicate (skipped)")
            continue
        material = ingest.process_material(
            conn,
            stored["material_id"],
            markdown_dir=MATERIALS_DIR,
            client=client,
            settings=settings,
            now_iso=_now(),
        )
        where = "attached" if material["goal_id"] else "unattached"
        print(
            f"material {material['filename']}: {material['status']}, "
            f"{material['effort_hours']}h, topic {material['topic_name']!r} ({where})"
        )

    topics = db.list_topics(conn)
    print(f"topics: {len(topics) - topics_before} created, {len(topics)} total")

    # 4. plan every open goal
    for goal in db.list_goals(conn):
        if goal["status"] != "open":
            continue
        plan = engine.replan(conn, goal["id"], _now())
        conn.commit()
        print(
            f"planned {goal['title']}: {len(plan['revisions_added'])} revision job(s), "
            f"{len(plan['moved_jobs'])} rescheduled"
        )

    jobs = db.list_jobs(conn)
    names = {m["id"]: m["filename"] for m in db.list_materials(conn)}
    print(f"\n{len(jobs)} job(s) scheduled:")
    for job in jobs:
        label = names.get(job["material_id"], job["material_id"])
        print(f"  {job['slot_start']}  {job['kind']:<8} {label}")

    conn.close()
    print(f"\nSeed complete -> {DB_PATH}")
    print("Run: python -m app.main  ->  http://127.0.0.1:8000")


if __name__ == "__main__":
    main()
