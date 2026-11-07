#!/usr/bin/env python3
"""M5 offline acceptance smoke test — one runnable end-to-end check.

Usage:
    python scripts/smoke_e2e.py

Creates a throwaway SQLite database, then drives the real FastAPI app through
the offline demo in docs/DELIVERY.md:

  import .ics -> create goal from exam -> upload samples -> jobs appear
  -> tick a job -> revisions appear -> replan twice (no duplicates)
  -> move/pin a future job -> replan (slot kept) -> chat -> stats

Exit code 0 means the offline M5 demo passes. No network and no LLM endpoint
are required (offline fallbacks are used).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Must be set before app.main is imported (DEFAULT_DB_PATH is bound at import).
_FD, _DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_FD)
os.environ["DYNACALENDAR_DB"] = _DB_PATH

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)

CHECKS: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}")
        raise SystemExit(1)
    CHECKS.append(message)
    print(f"ok  {message}")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def free_slot(events: list[dict], jobs: list[dict], days_ahead: int = 10) -> tuple[str, str]:
    """Find a 50-minute slot in the next days that overlaps no event/job."""
    now = datetime.now(timezone.utc)
    duration = timedelta(minutes=50)
    busy = []
    for item in events:
        busy.append((parse_iso(item["dtstart"]), parse_iso(item["dtend"])))
    for item in jobs:
        busy.append((parse_iso(item["slot_start"]), parse_iso(item["slot_end"])))
    for day_offset in range(1, days_ahead + 1):
        day = (now + timedelta(days=day_offset)).date()
        for hour in range(8, 21):
            start = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
            end = start + duration
            if not any(overlaps(start, end, bs, be) for bs, be in busy):
                return (
                    start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
    raise SystemExit("FAIL: could not find a free test slot")


def main() -> None:
    print(f"temp db: {_DB_PATH}")

    # 1. health
    check(client.get("/api/health").json()["status"] == "ok", "health endpoint")

    # 2. import the sample calendar
    with open(BASE_DIR / "samples" / "sample_calendar.ics", "rb") as fh:
        imported = client.post(
            "/api/ics/import", files={"file": ("sample.ics", fh, "text/calendar")}
        )
    check(imported.status_code == 200, "ICS import returns 200")
    imp = imported.json()
    check(imp["imported"] == 10, "ICS import reports 10 events")
    check("MATH2001 Final Exam" in imp["exams_found"], "exam event surfaced")
    exams = imp.get("exams") or []
    check(len(exams) == 1 and exams[0].get("event_id"), "exam carries its event id")

    # 3. goal from the exam (deadline derived server-side)
    goal_res = client.post(
        "/api/goals",
        json={"title": "MATH2001", "exam_event_id": exams[0]["event_id"]},
    )
    check(goal_res.status_code == 200, "goal created from exam event (JSON body)")
    goal = goal_res.json()
    check(goal["deadline"].startswith("2026-12-18"), "goal deadline derived from exam")
    check(goal.get("exam_event_id") == exams[0]["event_id"], "goal links the exam event")

    # 4. drop all four sample materials, attached to the goal
    files = []
    for path in sorted((BASE_DIR / "samples" / "materials").glob("*.md")):
        files.append(("files", (path.name, path.read_bytes(), "text/markdown")))
    up = client.post(
        "/api/inbox/upload", files=files, data={"goal_id": goal["id"]}
    )
    check(up.status_code == 200, "sample upload returns 200")
    rows = up.json()
    check(len(rows) == 4 and all(r["status"] == "ready" for r in rows), "4 materials ready")
    check(all(r.get("topic_id") for r in rows), "materials have topics")

    # 5. upload replanned immediately (no manual replan)
    jobs_res = client.get("/api/jobs")
    jobs = jobs_res.json()
    check(len(jobs) >= 4, "jobs scheduled immediately after upload")
    events = client.get("/api/events").json()
    external = [e for e in events if e["kind"] == "external"]
    check(bool(external), "external events present")
    for job in jobs:
        js, je = parse_iso(job["slot_start"]), parse_iso(job["slot_end"])
        for ev in external:
            check(
                not overlaps(js, je, parse_iso(ev["dtstart"]), parse_iso(ev["dtend"])),
                f"job {job['id'][:6]} avoids external {ev['title'][:20]}",
            )

    # 6. atomic tick: done + revisions + replan in one call
    learn = [j for j in jobs if j["kind"] == "learn"][0]
    tick = client.post(f"/api/jobs/{learn['id']}/complete")
    check(tick.status_code == 200, "checkbox tick returns 200")
    tick_body = tick.json()
    check(len(tick_body["revisions_added"]) == 4, "four revision rounds added")
    revisions = [j for j in client.get("/api/jobs").json() if j["kind"] == "revision"]
    check(len(revisions) == 4, "four revisions visible")
    check(
        all(j["status"] == "pending" for j in revisions),
        "revisions are pending (none silently dropped)",
    )

    # 7. replan is idempotent and never duplicates revisions
    for _ in range(2):
        client.post(f"/api/goals/{goal['id']}/replan")
    after = client.get("/api/jobs").json()
    check(
        len([j for j in after if j["kind"] == "revision"]) == 4,
        "replan twice does not duplicate revisions",
    )
    done = [j for j in after if j["id"] == learn["id"]]
    check(done and done[0]["status"] == "done", "completed job stays done")

    # 8. move/pin a future learn job; replan keeps the new slot
    future = [
        j for j in after
        if j["kind"] == "learn" and j["status"] == "pending"
        and parse_iso(j["slot_start"]).date() > datetime.now(timezone.utc).date()
    ]
    check(bool(future), "a future learn job exists to move")
    target = future[0]
    start, end = free_slot(events, after)
    moved = client.post(
        f"/api/jobs/{target['id']}/move", json={"slot_start": start, "slot_end": end}
    )
    check(moved.status_code == 200, "move accepts JSON + ISO timestamps")
    check(moved.json()["job"]["pinned"] == 1, "moved job is pinned")
    client.post(f"/api/goals/{goal['id']}/replan")
    pinned = [j for j in client.get("/api/jobs").json() if j["id"] == target["id"]]
    check(
        pinned and pinned[0]["slot_start"] == start,
        "pinned slot survives replan",
    )

    # 9. moving onto an external event is rejected
    exam = next(e for e in external if "Exam" in e["title"])
    clash = client.post(
        f"/api/jobs/{target['id']}/move",
        json={"slot_start": exam["dtstart"], "slot_end": exam["dtend"]},
    )
    check(clash.status_code == 409, "move onto external event returns 409")

    # 10. offline chat is grounded in the calendar
    chat = client.post("/api/chat", json={"message": "what's due this week?"})
    check(chat.status_code == 200, "chat returns 200 offline")
    check(chat.json()["llm"] == "off", "chat reports offline mode")
    check(len(chat.json()["reply"]) > 20, "offline chat gives a real answer")
    history = client.get("/api/chat/messages").json()
    check(len(history) >= 2, "chat history persists user + assistant")

    # 11. stats
    stats = client.get(f"/api/stats?goal_id={goal['id']}").json()
    check(stats["totals"]["planned_min"] > 0, "stats report planned minutes")
    check(stats["totals"]["done_min"] > 0, "stats report done minutes")
    check(0 <= stats["totals"]["progress"] <= 1, "stats progress is a ratio")

    # 12. re-import is idempotent
    with open(BASE_DIR / "samples" / "sample_calendar.ics", "rb") as fh:
        again = client.post(
            "/api/ics/import", files={"file": ("sample.ics", fh, "text/calendar")}
        )
    check(again.json()["imported"] == 0, "re-import imports 0 new events")

    print(f"\nPASS — {len(CHECKS)} checks (offline M5 demo)")


if __name__ == "__main__":
    try:
        main()
    finally:
        if os.path.exists(_DB_PATH):
            os.unlink(_DB_PATH)
