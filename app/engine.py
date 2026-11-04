"""Deterministic scheduler.

Pure Python: no network, no LLM, no ``datetime.now()`` (the clock is injected
as ``now_iso``). Given the same ``(db, settings, now)`` it always produces the
same job slots. Idempotent.

Rules (docs/ARCHITECTURE.md):
  - never overlaps external events (locked)
  - done jobs never move
  - pending jobs on today's date keep their slots (no churn)
  - free slots fill from today -> goal deadline, earliest first
  - after a learn job completes, revision rounds at +1d/+3d/+7d/+14d
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db
from .config import (
    Settings,
    STUDY_WINDOW_END_HOUR,
    STUDY_WINDOW_START_HOUR,
    parse_settings,
)

REVISION_INTERVALS_DEFAULT = [1, 3, 7, 14]


def _parse(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _parse_day(iso: str):
    iso = iso.strip()
    if "T" in iso:
        return _parse(iso).date()
    return datetime.strptime(iso, "%Y-%m-%d").date()


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def _study_slots(day, slot_minutes: int) -> list[tuple[datetime, datetime]]:
    """Slots within the study window [08:00, 22:00) for one UTC day."""
    start = datetime(
        day.year, day.month, day.day, STUDY_WINDOW_START_HOUR, tzinfo=timezone.utc
    )
    end = datetime(
        day.year, day.month, day.day, STUDY_WINDOW_END_HOUR, tzinfo=timezone.utc
    )
    slots: list[tuple[datetime, datetime]] = []
    cur = start
    step = timedelta(minutes=slot_minutes)
    while cur + step <= end:
        slots.append((cur, cur + step))
        cur += step
    return slots


def _occupied_ranges(jobs: list[dict[str, Any]]) -> list[tuple[datetime, datetime]]:
    ranges: list[tuple[datetime, datetime]] = []
    for j in jobs:
        if j["status"] in ("done", "pending"):
            ranges.append((_parse(j["slot_start"]), _parse(j["slot_end"])))
    return ranges


def _materials_for_goal(conn, goal_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM materials WHERE goal_id = ? ORDER BY created_at", (goal_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def replan(
    conn,
    goal_id: str,
    now_iso: str,
) -> dict[str, Any]:
    """Recompute all pending future work for ``goal``.

    Runs on the caller's connection (so a checkbox tick stays in one SQLite
    transaction). Returns ``{"moved_jobs": [job_ids], "revisions_added": [job_ids]}``.
    """
    now = _parse(now_iso)
    today = now.date()
    settings = parse_settings(db.list_settings(conn))
    goal = db.get_goal(conn, goal_id)
    deadline = _parse_day(goal["deadline"])
    if deadline < today:
        return {"moved_jobs": [], "revisions_added": []}

    slot_minutes = settings.slot_minutes
    capacity_minutes = int(settings.daily_capacity_hours * 60)
    intervals = settings.revision_intervals_days or [1, 3, 7, 14]

    externals: list[tuple[datetime, datetime]] = []
    for e in db.list_events(conn):
        if e["kind"] != "external" or not e["locked"]:
            continue
        es, ee = _parse(e["dtstart"]), _parse(e["dtend"])
        if ee.date() < today or es.date() > deadline:
            continue
        externals.append((es, ee))

    jobs = db.jobs_for_goal(conn, goal_id)
    done_jobs = [j for j in jobs if j["status"] == "done"]
    pending_jobs = [j for j in jobs if j["status"] == "pending"]
    done_learn = [j for j in done_jobs if j["kind"] == "learn"]

    # Occupied ranges: done jobs always; pending jobs on TODAY stay put.
    occupied = _occupied_ranges(done_jobs)
    kept_pending_ids = set()
    for j in pending_jobs:
        if _parse(j["slot_start"]).date() == today:
            occupied.append((_parse(j["slot_start"]), _parse(j["slot_end"])))
            kept_pending_ids.add(j["id"])

    # Free slots from today -> deadline (mutable cursor consumed by placement).
    free: list[tuple[datetime, datetime]] = []
    day = today
    while day <= deadline:
        day_minutes_used = 0
        for slot_start, slot_end in _study_slots(day, slot_minutes):
            if day_minutes_used + slot_minutes > capacity_minutes:
                break
            if any(
                _overlaps(slot_start, slot_end, es, ee) for es, ee in externals
            ):
                continue
            if any(
                _overlaps(slot_start, slot_end, os_, oe) for os_, oe in occupied
            ):
                continue
            free.append((slot_start, slot_end))
            day_minutes_used += slot_minutes
        day += timedelta(days=1)

    placed_slots: list[tuple[datetime, datetime]] = []

    def is_free(slot_start, slot_end) -> bool:
        if any(
            _overlaps(slot_start, slot_end, es, ee) for es, ee in externals
        ):
            return False
        if any(
            _overlaps(slot_start, slot_end, os_, oe) for os_, oe in occupied
        ):
            return False
        return not any(
            _overlaps(slot_start, slot_end, ps, pe) for ps, pe in placed_slots
        )

    moved_jobs: list[str] = []
    revisions_added: list[str] = []

    # --- remove pending work that will be re-derived ---
    for j in pending_jobs:
        if j["id"] in kept_pending_ids:
            continue
        moved_jobs.append(j["id"])
        conn.execute("DELETE FROM jobs WHERE id = ?", (j["id"],))
        db.delete_event(conn, j["event_id"])

    # --- place revisions for completed learn jobs ---
    done_by_date = sorted(
        done_learn,
        key=lambda j: (j["done_at"] or j["created_at"], j["id"]),
    )
    for learn_job in done_by_date:
        done_date = (
            _parse(learn_job["done_at"]).date()
            if learn_job["done_at"]
            else _parse(learn_job["created_at"]).date()
        )
        duration = learn_job["duration_min"]
        for round_no, interval in enumerate(intervals, start=1):
            target = max(done_date + timedelta(days=interval), today)
            placed = False
            scan_day = target
            end_scan = deadline + timedelta(days=max(intervals))
            while scan_day <= end_scan and not placed:
                if scan_day >= target:
                    for slot_start, slot_end in _study_slots(
                        scan_day, slot_minutes
                    ):
                        if is_free(slot_start, slot_end):
                            job_id = _create_job_event(
                                conn,
                                goal_id,
                                learn_job["material_id"],
                                "revision",
                                duration,
                                round_no,
                                slot_start,
                                slot_end,
                            )
                            occupied.append((slot_start, slot_end))
                            placed_slots.append((slot_start, slot_end))
                            revisions_added.append(job_id)
                            placed = True
                            break
                scan_day += timedelta(days=1)
            if not placed:
                _create_conflicted_revision(
                    conn, goal_id, learn_job, round_no, duration, done_date
                )

    # --- place remaining learn work, material order, earliest free first ---
    materials = _materials_for_goal(conn, goal_id)
    default_effort = settings.default_effort_hours
    for material in materials:
        effort = (
            material["effort_hours"]
            if material["effort_hours"] is not None
            else default_effort
        )
        total_slots = max(1, math.ceil(effort * 60 / slot_minutes))
        covered = sum(
            1
            for j in jobs
            if j["material_id"] == material["id"]
            and j["kind"] == "learn"
            and j["status"] in ("pending", "done")
        )
        needed = total_slots - covered
        for slot_start, slot_end in free:
            if needed <= 0:
                break
            if not is_free(slot_start, slot_end):
                continue
            _create_job_event(
                conn,
                goal_id,
                material["id"],
                "learn",
                slot_minutes,
                0,
                slot_start,
                slot_end,
            )
            occupied.append((slot_start, slot_end))
            placed_slots.append((slot_start, slot_end))
            needed -= 1

    return {"moved_jobs": moved_jobs, "revisions_added": revisions_added}


def _create_job_event(
    conn,
    goal_id: str,
    material_id: str,
    kind: str,
    duration_min: int,
    revision_round: int,
    start: datetime,
    end: datetime,
) -> str:
    event_id = db.create_event(
        conn,
        kind="study" if kind == "learn" else "revision",
        title="",  # enriched from material title by the API layer
        dtstart=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        dtend=end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        locked=0,
    )
    job_id = db.create_job(
        conn,
        event_id=event_id,
        goal_id=goal_id,
        material_id=material_id,
        kind=kind,
        duration_min=duration_min,
        status="pending",
        revision_round=revision_round,
    )
    return job_id


def _create_conflicted_revision(
    conn,
    goal_id: str,
    learn_job: dict[str, Any],
    round_no: int,
    duration_min: int,
    done_date,
) -> None:
    """Surface a revision that could not be placed (never silently dropped)."""
    event_id = db.create_event(
        conn,
        kind="revision",
        title="",
        dtstart=datetime(
            done_date.year, done_date.month, done_date.day, tzinfo=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        dtend=(datetime(
            done_date.year, done_date.month, done_date.day, tzinfo=timezone.utc
        ) + timedelta(minutes=duration_min)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        locked=0,
    )
    db.create_job(
        conn,
        event_id=event_id,
        goal_id=goal_id,
        material_id=learn_job["material_id"],
        kind="revision",
        duration_min=duration_min,
        status="conflicted",
        revision_round=round_no,
    )
