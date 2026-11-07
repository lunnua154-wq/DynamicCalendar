"""Deterministic scheduler.

Pure Python: no network, no LLM, no ``datetime.now()`` (the clock is injected as
``now_iso``). Given the same ``(db, settings, now)`` it always produces the same
job slots. Idempotent, and it reuses a job row when its slot does not change, so
re-planning does not churn ids or report phantom moves.

Rules (docs/ARCHITECTURE.md):
  - never overlaps external events (locked)
  - done jobs never move
  - pending jobs on today's local date keep their slots (no churn)
  - free slots fill from today -> goal deadline, earliest first
  - after a learn job completes, revision rounds at +1d/+3d/+7d/+14d
  - pinned jobs never move and are never re-derived

Time model: storage is UTC. ``settings.utc_offset_minutes`` is the user's local
clock; day windows and the "today" boundary are computed in local time and
converted back to UTC for writes, so study jobs land in waking hours while the
engine stays a pure function of its inputs.

M3 additions:
  - ``jobs.pinned`` / ``jobs.parent_job_id`` schema columns (see app/db.py)
  - re-derivable jobs are ``status IN ('pending','conflicted')`` AND ``pinned = 0``
  - occupied slots = done jobs + pinned pending jobs (+ today-kept pending jobs)
  - ``daily_capacity_hours`` is respected for *everything* placed
  - revision rounds are de-duplicated against existing done/skipped/pinned
    revisions for the same learn job (parent_job_id) and round
  - revisions that cannot be placed by the goal deadline become ``conflicted``
    (never silently dropped)
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import db
from .config import (
    STUDY_WINDOW_END_HOUR,
    STUDY_WINDOW_START_HOUR,
    parse_settings,
)

REVISION_INTERVALS_DEFAULT = [1, 3, 7, 14]


def _parse(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _parse_deadline(iso: str) -> datetime:
    """Parse a goal deadline (UTC timestamp, ms, or bare date) as aware UTC."""
    text = iso.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "T" in text:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    day = datetime.strptime(text[:10], "%Y-%m-%d")
    return day.replace(tzinfo=timezone.utc)


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def _study_slots(day: date, slot_minutes: int) -> list[tuple[datetime, datetime]]:
    """Slots within the local study window [08:00, 22:00) for one local day."""
    start = datetime(day.year, day.month, day.day, STUDY_WINDOW_START_HOUR)
    end = datetime(day.year, day.month, day.day, STUDY_WINDOW_END_HOUR)
    slots: list[tuple[datetime, datetime]] = []
    step = timedelta(minutes=slot_minutes)
    cur = start
    while cur + step <= end:
        slots.append((cur, cur + step))
        cur += step
    return slots


def _occupied_ranges(jobs: list[dict[str, Any]], to_local) -> list[tuple[datetime, datetime]]:
    """Immutable time ranges: done jobs and pinned/kept pending jobs."""
    return [(to_local(_parse(j["slot_start"])), to_local(_parse(j["slot_end"]))) for j in jobs]


def _job_key(job: dict[str, Any]) -> tuple:
    return (
        job["material_id"],
        job["kind"],
        int(job["revision_round"]),
        job.get("parent_job_id"),
    )


def _materials_for_goal(conn, goal_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM materials WHERE goal_id = ? AND status IN ('ready', 'tagged', 'new')"
        " ORDER BY created_at, id",
        (goal_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def replan(
    conn,
    goal_id: str,
    now_iso: str,
) -> dict[str, Any]:
    """Recompute all pending future work for ``goal``.

    Runs on the caller's connection (so a checkbox tick stays in one SQLite
    transaction). Returns ``{"moved_jobs": [job_ids], "revisions_added": [job_ids]}``
    where ``moved_jobs`` are the jobs that could no longer keep their slot.
    """
    settings = parse_settings(db.list_settings(conn))
    offset = timedelta(minutes=settings.utc_offset_minutes)

    def to_local(dt: datetime) -> datetime:
        return (dt + offset).replace(tzinfo=None)

    def to_utc(dt: datetime) -> datetime:
        return (dt - offset).replace(tzinfo=timezone.utc)

    def utc_str(dt: datetime) -> str:
        return to_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")

    now_utc = _parse(now_iso)
    now_local = to_local(now_utc)
    today = now_local.date()
    goal = db.get_goal(conn, goal_id)
    deadline_local = to_local(_parse_deadline(goal["deadline"])).date()
    if deadline_local < today:
        return {"moved_jobs": [], "revisions_added": []}

    slot_minutes = settings.slot_minutes
    capacity_minutes = int(settings.daily_capacity_hours * 60)
    intervals = settings.revision_intervals_days or REVISION_INTERVALS_DEFAULT

    # External events are read-only hard constraints (local naive).
    externals: list[tuple[datetime, datetime]] = []
    for e in db.list_events(conn):
        if e["kind"] != "external" or not e["locked"]:
            continue
        es, ee = to_local(_parse(e["dtstart"])), to_local(_parse(e["dtend"]))
        if ee.date() < today or es.date() > deadline_local:
            continue
        externals.append((es, ee))

    jobs = db.jobs_for_goal(conn, goal_id)
    done_jobs = [j for j in jobs if j["status"] == "done"]
    pending_jobs = [j for j in jobs if j["status"] == "pending"]

    # Pinned pending jobs that overlap an external event cannot survive a
    # re-import: external events are read-only hard constraints, so those pinned
    # jobs are deleted and re-placed unpinned. Pinned jobs that do not overlap
    # externals stay occupied and untouched.
    safe_pinned_jobs = []
    clobbered_pinned_ids: set[str] = set()
    for j in pending_jobs:
        if j["pinned"] != 1:
            continue
        js, je = to_local(_parse(j["slot_start"])), to_local(_parse(j["slot_end"]))
        if any(_overlaps(js, je, es, ee) for es, ee in externals):
            clobbered_pinned_ids.add(j["id"])
        else:
            safe_pinned_jobs.append(j)

    # Today's pending jobs keep their slots this run (no churn, no DB mutation).
    kept_pending = [
        j
        for j in pending_jobs
        if to_local(_parse(j["slot_start"])).date() == today
        and j["id"] not in clobbered_pinned_ids
    ]
    kept_ids = {j["id"] for j in kept_pending}

    # Re-derivable: pending or conflicted, unpinned, not kept today,
    # plus pinned jobs whose slot is clobbered by an external event.
    derivable = [
        j
        for j in jobs
        if j["status"] in ("pending", "conflicted")
        and j["pinned"] == 0
        and j["id"] not in kept_ids
        and j["id"] not in clobbered_pinned_ids
    ] + [j for j in pending_jobs if j["id"] in clobbered_pinned_ids]

    derivable_ids = {j["id"] for j in derivable}
    surviving = [j for j in jobs if j["id"] not in derivable_ids]

    # Reuse pool: a re-derived job keeps its row when the recomputed slot is
    # identical, so re-planning does not churn ids nor report phantom moves.
    pool: dict[tuple, dict[str, dict[str, Any]]] = {}
    for j in sorted(derivable, key=lambda j: (j["slot_start"], j["id"])):
        pool.setdefault(_job_key(j), {})[j["slot_start"]] = j

    # Occupied slots = done + safe pinned pending + today-kept pending
    # (immutable). A pinned job that is also kept today must be counted once.
    occupied_jobs = (
        done_jobs
        + [j for j in safe_pinned_jobs if j["id"] not in kept_ids]
        + kept_pending
    )
    occupied = _occupied_ranges(occupied_jobs, to_local)

    # Per-day minutes already consumed by occupied jobs (counts against capacity).
    day_used: dict = defaultdict(int)
    for j in occupied_jobs:
        day_used[to_local(_parse(j["slot_start"])).date()] += int(j["duration_min"])

    revisions_added: list[str] = []

    def is_placeable(slot_start, slot_end) -> bool:
        d = slot_start.date()
        if day_used.get(d, 0) + slot_minutes > capacity_minutes:
            return False
        if any(_overlaps(slot_start, slot_end, es, ee) for es, ee in externals):
            return False
        if any(_overlaps(slot_start, slot_end, os_, oe) for os_, oe in occupied):
            return False
        return True

    def find_placeable(from_day):
        """Earliest free slot from ``from_day`` through the deadline inclusive.

        Never schedules in the past: any slot that starts before ``now`` is
        skipped (only relevant for today).
        """
        day = max(from_day, today)
        while day <= deadline_local:
            for slot_start, slot_end in _study_slots(day, slot_minutes):
                if slot_start < now_local:
                    continue
                d = slot_start.date()
                if day_used.get(d, 0) + slot_minutes > capacity_minutes:
                    break  # slots are chronological; the rest of the day won't fit either
                if is_placeable(slot_start, slot_end):
                    return slot_start, slot_end
            day += timedelta(days=1)
        return None

    def materialize(
        material_id: str,
        kind: str,
        duration_min: int,
        revision_round: int,
        slot_start: datetime,
        slot_end: datetime,
        parent_job_id: str | None = None,
    ) -> tuple[str, bool]:
        """Reuse the matching pool job, else create one. Returns (id, created)."""
        key = (material_id, kind, revision_round, parent_job_id)
        slot_iso = utc_str(slot_start)
        existing = pool.get(key, {}).pop(slot_iso, None)
        if existing is not None:
            return existing["id"], False
        job_id = _create_job_event(
            conn,
            goal_id,
            material_id,
            kind,
            duration_min,
            revision_round,
            to_utc(slot_start),
            to_utc(slot_end),
            parent_job_id=parent_job_id,
        )
        return job_id, True

    def place(slot_start, slot_end) -> None:
        occupied.append((slot_start, slot_end))
        day_used[slot_start.date()] = day_used.get(slot_start.date(), 0) + slot_minutes

    # --- revisions for completed learn jobs ---
    done_learn = sorted(
        (j for j in done_jobs if j["kind"] == "learn"),
        key=lambda j: (j["done_at"] or j["created_at"], j["id"]),
    )
    # Any surviving revision of the same round blocks re-creation — including
    # unpinned pending revisions kept for today (they survive a replan
    # unchanged). Pool jobs are re-derived, so they are not counted here.
    existing_revisions = [j for j in surviving if j["kind"] == "revision"]

    def revision_exists(learn_job_id: str, round_no: int) -> bool:
        return any(
            j["parent_job_id"] == learn_job_id and j["revision_round"] == round_no
            for j in existing_revisions
        )

    for learn_job in done_learn:
        done_date = (
            to_local(_parse(learn_job["done_at"])).date()
            if learn_job["done_at"]
            else to_local(_parse(learn_job["created_at"])).date()
        )
        duration = int(learn_job["duration_min"])
        for round_no, interval in enumerate(intervals, start=1):
            if revision_exists(learn_job["id"], round_no):
                continue
            target = max(done_date + timedelta(days=interval), today)
            slot = find_placeable(target)
            if slot is not None:
                slot_start, slot_end = slot
                job_id, created = materialize(
                    learn_job["material_id"],
                    "revision",
                    duration,
                    round_no,
                    slot_start,
                    slot_end,
                    parent_job_id=learn_job["id"],
                )
                place(slot_start, slot_end)
                if created:
                    revisions_added.append(job_id)
            else:
                _create_conflicted_revision(
                    conn,
                    goal_id,
                    learn_job,
                    round_no,
                    duration,
                    done_date,
                    parent_job_id=learn_job["id"],
                    offset=offset,
                )

    # --- place remaining learn work, material order, earliest free first ---
    materials = _materials_for_goal(conn, goal_id)
    default_effort = settings.default_effort_hours
    surviving_learn = [j for j in surviving if j["kind"] == "learn"]
    for material in materials:
        effort = material["effort_hours"]
        if effort is None:
            effort = default_effort
        total_slots = max(1, math.ceil(effort * 60 / slot_minutes))
        # A skipped learn job is a deliberate user decision: count it as
        # covered so the planner does not re-create it.
        covered = sum(
            1
            for j in surviving_learn
            if j["material_id"] == material["id"]
            and j["status"] in ("done", "pending", "skipped")
        )
        needed = total_slots - covered
        for _ in range(needed):
            slot = find_placeable(today)
            if slot is None:
                break
            slot_start, slot_end = slot
            materialize(
                material["id"], "learn", slot_minutes, 0, slot_start, slot_end
            )
            place(slot_start, slot_end)

    # --- jobs that could not keep a slot are removed; report them as moved ---
    moved_jobs: list[str] = []
    for slots in pool.values():
        for stale in slots.values():
            moved_jobs.append(stale["id"])
            conn.execute("DELETE FROM jobs WHERE id = ?", (stale["id"],))
            db.delete_event(conn, stale["event_id"])

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
    parent_job_id: str | None = None,
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
        parent_job_id=parent_job_id,
    )
    return job_id


def _create_conflicted_revision(
    conn,
    goal_id: str,
    learn_job: dict[str, Any],
    round_no: int,
    duration_min: int,
    done_date: date,
    parent_job_id: str | None = None,
    offset: timedelta = timedelta(0),
) -> None:
    """Surface a revision that could not be placed (never silently dropped).

    The event sits at the start of the local day the learn job was completed.
    """
    start_local = datetime(done_date.year, done_date.month, done_date.day)
    start = (start_local - offset).replace(tzinfo=timezone.utc)
    event_id = db.create_event(
        conn,
        kind="revision",
        title="",
        dtstart=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        dtend=(start + timedelta(minutes=duration_min)).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
        parent_job_id=parent_job_id,
    )
