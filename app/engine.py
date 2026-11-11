"""Deterministic scheduler.

Pure Python: no network, no LLM, no ``datetime.now()`` (the clock is injected as
``now_iso``). Given the same ``(db, settings, now, chooser)`` it always produces
the same job slots. Idempotent, and it reuses a job row when its slot does not
change, so re-planning does not churn ids or report phantom moves.

Rules (docs/ARCHITECTURE.md):
  - never overlaps external events (locked)
  - done jobs never move
  - pending jobs on today's local date keep their slots (no churn)
  - candidates fill from today -> goal deadline; each candidate is scored
    (soft penalty from built-in placement wisdom + stored constraints) and
    hard-constraint-validated; the winner is ``argmin(penalty, start)``
    — with smart placement off and no chooser this degenerates to the old
    earliest-free-slot behaviour
  - after a learn job completes, revision rounds at +1d/+3d/+7d/+14d
  - pinned jobs never move and are never re-derived

Smart placement (``smart_placement=on``, the default) scores every valid
candidate: no allocating right after a tiring (fatigue≥2) event, no same-subject
work immediately after a same-subject external, interleaving is mildly
preferred. User/LLM-authored constraints from the ``constraints`` table
(obtained via NL or the API/chat) add their own soft penalties (or hard
exclusions). If *every* candidate is excluded by hard constraints, they relax
to soft and the job is placed anyway — a plan can never deadlock; the placed
job's ``placement_note`` records the relaxation.

LLM in the placement path is **injected**, never imported or called here: the
caller may pass ``chooser(job_context, candidates) -> {"choice": int,
"reason": str} | None``. The chooser picks from a shortlist of hard-valid
candidates (2 earliest + 2 lowest-penalty); an out-of-range answer or any
exception falls back to the deterministic pick. Every created job records
``placement_source`` ('engine'|'llm') and ``placement_note``.

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

M6 (better-llm-utilisation) additions:
  - annotations (``event_annotations``) and constraints (``constraints``) are
    read as *data*; both are optional and default to empty
  - placement candidates are scored; LLM selection is an injected ``chooser``
  - every created job records ``placement_source`` / ``placement_note``
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import constraints as constraints_mod
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
        " ORDER BY created_at, rowid",
        (goal_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def subjects_for_material_row(material: dict[str, Any]) -> list[str]:
    """Lowercased tag subjects for a material (used by the placement scorer)."""
    raw = material.get("tags")
    if not raw:
        return []
    try:
        tags = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(tags, list):
        return []
    out: list[str] = []
    for tag in tags:
        subject = str(tag).strip().lower()
        if subject and subject not in out:
            out.append(subject)
    return out[:5]


def replan(
    conn,
    goal_id: str,
    now_iso: str,
    chooser: Any = None,
) -> dict[str, Any]:
    """Recompute all pending future work for ``goal``.

    ``chooser`` (optional): ``chooser(job_context, candidates) -> dict | None``
    where ``candidates`` is a shortlist of hard-valid slots (2 earliest + 2
    lowest-penalty, de-duplicated, ``index``-addressable). The returned dict must
    contain ``"choice"`` (int index into the shortlist) and may contain
    ``"reason"`` (str). Any exception, out-of-range answer or falsy result falls
    back to the deterministic best-penalty pick — the chooser can only reorder
    *valid* choices, never invent invalid ones.

    Runs on the caller's connection (so a checkbox tick stays in one SQLite
    transaction). Returns ``{"moved_jobs": [job_ids], "revisions_added": [job_ids]}``
    where ``moved_jobs`` are the jobs that could no longer keep their slot.
    """
    settings = parse_settings(db.list_settings(conn))
    offset = timedelta(minutes=settings.utc_offset_minutes)
    smart = getattr(settings, "smart_placement", "on") == "on"

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
        return {"moved_jobs": [], "revisions_added": [], "unplaced": 0}

    slot_minutes = settings.slot_minutes
    capacity_minutes = int(settings.daily_capacity_hours * 60)
    intervals = settings.revision_intervals_days or REVISION_INTERVALS_DEFAULT

    # External events are read-only hard constraints (local naive), enriched
    # with their semantic annotation (subject/fatigue) when one exists.
    annotations = {row["event_id"]: row for row in db.list_annotations(conn)}
    externals: list[dict[str, Any]] = []
    for e in db.list_events(conn):
        if e["kind"] != "external" or not e["locked"]:
            continue
        es, ee = to_local(_parse(e["dtstart"])), to_local(_parse(e["dtend"]))
        if ee.date() < today or es.date() > deadline_local:
            continue
        ann = annotations.get(e["id"]) or {}
        externals.append(
            {
                "es": es,
                "ee": ee,
                "subject": constraints_mod.as_subject(ann.get("subject")),
                "fatigue": constraints_mod.as_fatigue(ann.get("fatigue")),
                "title": str(e.get("title") or ""),
            }
        )
    external_ranges = [(e["es"], e["ee"]) for e in externals]
    # Scoring context expects {"start","end","subject","fatigue"} dicts.
    scoring_externals = [
        {
            "start": e["es"],
            "end": e["ee"],
            "subject": e["subject"],
            "fatigue": e["fatigue"],
            "title": e["title"],
        }
        for e in externals
    ]
    constraints = [
        parsed
        for parsed in (
            constraints_mod.parse_constraint_row(row) for row in db.list_constraints(conn, enabled_only=True)
        )
        if parsed is not None
    ]
    material_subject_cache: dict[str, list[str]] = {}

    def subjects_of_material_id(material_id: str) -> list[str]:
        cached = material_subject_cache.get(material_id)
        if cached is None:
            row = conn.execute(
                "SELECT tags FROM materials WHERE id = ?", (material_id,)
            ).fetchone()
            cached = subjects_for_material_row(dict(row or {}))
            material_subject_cache[material_id] = cached
        return cached

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
        if any(_overlaps(js, je, es, ee) for es, ee in external_ranges):
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
    # Occupied ranges with their primary subject, for adjacency-aware scoring.
    time_entries: list[tuple[datetime, datetime, str]] = [
        (
            to_local(_parse(j["slot_start"])),
            to_local(_parse(j["slot_end"])),
            (subjects_of_material_id(j["material_id"]) or [""])[0],
        )
        for j in occupied_jobs
    ]

    # Per-day minutes already consumed by occupied jobs (counts against capacity).
    day_used: dict = defaultdict(int)
    for j in occupied_jobs:
        day_used[to_local(_parse(j["slot_start"])).date()] += int(j["duration_min"])

    revisions_added: list[str] = []

    def iter_valid_candidates(from_day):
        """Yield every hard-valid ``(slot_start, slot_end)`` in [from_day, deadline].

        Never schedules in the past. Skips days whose capacity is already
        consumed. Overlap and capacity are the only hard gates here — soft
        preferences are applied later by the scorer.
        """
        day = max(from_day, today)
        while day <= deadline_local:
            if day_used.get(day, 0) + slot_minutes > capacity_minutes:
                day += timedelta(days=1)
                continue
            for slot_start, slot_end in _study_slots(day, slot_minutes):
                if slot_start < now_local:
                    continue
                if any(_overlaps(slot_start, slot_end, es, ee) for es, ee in external_ranges):
                    continue
                if any(_overlaps(slot_start, slot_end, os_, oe) for os_, oe in occupied):
                    continue
                yield slot_start, slot_end
            day += timedelta(days=1)

    def _job_context(kind: str, subjects: list[str], effort_hours: float | None) -> dict:
        return {
            "kind": kind,
            "subjects": subjects,
            "effort_hours": effort_hours,
            "constraints": [c["label"] for c in constraints],
            "smart_placement": smart,
        }

    def find_placeable(from_day, *, subjects=None, kind="learn", effort_hours=None):
        """Best slot from ``from_day`` through the deadline inclusive.

        Returns ``(slot_start, slot_end, source, note)`` or ``None``. With no
        chooser: deterministic ``argmin(penalty, start)`` scored by
        ``constraints_mod.score_slot`` — all-zero penalties degenerate to the
        old earliest-free-slot behaviour. With a chooser: the LLM picks from a
        shortlist of ≤4 hard-valid candidates (2 earliest + 2 lowest-penalty);
        anything invalid or raising falls back to the deterministic pick.

        Relaxation rule: if every candidate is excluded by hard constraints,
        hard exclusions degrade to soft penalties for this placement (the job
        is never dropped) and the placement note records it.
        """
        subjects = subjects or []
        primary = subjects[0] if subjects else ""
        chronological = sorted(time_entries, key=lambda entry: (entry[0], entry[1]))

        def evaluate(slot_start, slot_end, relax_hard: bool):
            penalty, blocked, reasons = constraints_mod.score_slot(
                slot_start,
                slot_end,
                subjects=subjects,
                externals=scoring_externals,
                constraints=constraints,
                smart=smart,
                relax_hard=relax_hard,
            )
            preceding = [e for e in chronological if e[1] <= slot_start]
            adj_penalty, adj_reasons = constraints_mod.job_adjacency_penalty(
                slot_start,
                primary=primary,
                preceding_entries=preceding,
                constraints=constraints,
                smart=smart,
            )
            return penalty + adj_penalty, blocked, reasons + adj_reasons

        valid: list[tuple[float, datetime, datetime, list[str]]] = []
        blocked_seen = 0
        for slot_start, slot_end in iter_valid_candidates(from_day):
            penalty, blocked, reasons = evaluate(slot_start, slot_end, False)
            if blocked:
                blocked_seen += 1
                continue
            # Penalties can't be negative, so the first penalty-0 candidate is
            # the argmin — return it instead of scanning the whole horizon.
            # (The chooser needs the full candidate set, so it opts out.)
            if chooser is None and penalty == 0.0:
                return slot_start, slot_end, "engine", None
            valid.append((penalty, slot_start, slot_end, reasons))

        relaxed = False
        if not valid and blocked_seen:
            relaxed = True
            for slot_start, slot_end in iter_valid_candidates(from_day):
                penalty, blocked, reasons = evaluate(slot_start, slot_end, True)
                valid.append((penalty, slot_start, slot_end, reasons))

        if not valid:
            return None

        best = min(valid, key=lambda item: (item[0], item[1]))
        chosen = best
        source = "engine"
        note: str | None = None
        llm_failed = False
        decline_reason: str | None = None

        if chooser is not None and len(valid) > 1:
            shortlist = _shortlist(valid)
            shortlist_payload = [
                {
                    "index": index,
                    "slot_start": slot_start,
                    "slot_end": slot_end,
                    "penalty": penalty,
                    "reasons": reasons,
                }
                for index, (penalty, slot_start, slot_end, reasons) in enumerate(shortlist)
            ]
            selection = None
            try:
                selection = chooser(
                    _job_context(kind, subjects, effort_hours),
                    shortlist_payload,
                )
            except Exception:
                llm_failed = True
            index = None
            if isinstance(selection, dict):
                if selection.get("budget_exhausted"):
                    decline_reason = "LLM budget reached; deterministic pick"
                else:
                    raw = selection.get("choice", selection.get("index"))
                    try:
                        raw_number = float(raw) if raw is not None and not isinstance(raw, bool) else None
                        index = int(raw_number) if raw_number is not None and raw_number.is_integer() else None
                    except (TypeError, ValueError):
                        index = None
            if index is not None and 0 <= index < len(shortlist):
                picked = shortlist[index]
                chosen = picked
                source = "llm"
                reason = str((selection or {}).get("reason") or "LLM slot pick")[:180]
                note = reason
                if relaxed:
                    note = f"{note}; hard constraints relaxed to fit"[:250]
            else:
                llm_failed = True

        if source == "engine":
            parts: list[str] = []
            if best[0] > 0:
                parts.append(f"smart penalty {best[0]:.0f}: {'; '.join(best[3])}")
            if relaxed:
                parts.append("hard constraints relaxed to fit")
            if decline_reason:
                parts.append(decline_reason)
            elif llm_failed:
                parts.append("deterministic pick (LLM unavailable or budget reached)")
            note = "; ".join(parts)[:250] or None

        return chosen[1], chosen[2], source, note

    def _shortlist(valid):
        """≤4 candidates for the LLM: 2 earliest + 2 lowest-penalty, deduped."""
        by_start = sorted(valid, key=lambda item: item[1])
        by_penalty = sorted(valid, key=lambda item: (item[0], item[1]))
        shortlist: list[tuple[float, datetime, datetime, list[str]]] = []
        seen: set[datetime] = set()
        for item in by_start[:2] + by_penalty[:2]:
            if item[1] not in seen:
                seen.add(item[1])
                shortlist.append(item)
        shortlist.sort(key=lambda item: item[1])
        return shortlist

    def materialize(
        material_id: str,
        kind: str,
        duration_min: int,
        revision_round: int,
        slot_start: datetime,
        slot_end: datetime,
        parent_job_id: str | None = None,
        placement_source: str = "engine",
        placement_note: str | None = None,
    ) -> tuple[str, bool]:
        """Reuse the matching pool job, else create one. Returns (id, created)."""
        key = (material_id, kind, revision_round, parent_job_id)
        slot_iso = utc_str(slot_start)
        existing = pool.get(key, {}).pop(slot_iso, None)
        if existing is not None:
            # slot unchanged — reuse the row, but refresh the receipt so the note
            # reflects this run's constraint set, not the week the job was created
            if (
                existing.get("placement_source") != placement_source
                or existing.get("placement_note") != placement_note
            ):
                db.update_job(
                    conn,
                    existing["id"],
                    placement_source=placement_source,
                    placement_note=placement_note,
                )
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
            placement_source=placement_source,
            placement_note=placement_note,
        )
        return job_id, True

    def place(slot_start, slot_end, subjects: list[str]) -> None:
        occupied.append((slot_start, slot_end))
        day_used[slot_start.date()] = day_used.get(slot_start.date(), 0) + slot_minutes
        time_entries.append((slot_start, slot_end, subjects[0] if subjects else ""))

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
            rev_subjects = subjects_of_material_id(learn_job["material_id"])
            slot = find_placeable(
                target, subjects=rev_subjects, kind="revision", effort_hours=None
            )
            if slot is not None:
                slot_start, slot_end, p_source, p_note = slot
                job_id, created = materialize(
                    learn_job["material_id"],
                    "revision",
                    duration,
                    round_no,
                    slot_start,
                    slot_end,
                    parent_job_id=learn_job["id"],
                    placement_source=p_source,
                    placement_note=p_note,
                )
                place(slot_start, slot_end, rev_subjects)
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

    # --- place remaining learn work ---
    # Order: least-recently-used subject first (round-robin across subjects),
    # which interleaves subjects through the day and lets the adjacency scorer /
    # interleave constraints do their job. Material (creation) order is kept
    # within a subject round.
    materials = _materials_for_goal(conn, goal_id)
    default_effort = settings.default_effort_hours
    surviving_learn = [j for j in surviving if j["kind"] == "learn"]
    queue: list[dict[str, Any]] = []  # [{material, subjects, remaining, effort}]
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
        if needed > 0:
            queue.append(
                {
                    "material": material,
                    "subjects": subjects_for_material_row(material),
                    "remaining": needed,
                    "effort": float(effort),
                }
            )
    last_subject = ""
    unplaced = 0
    while queue:
        # pick the first queued material whose primary subject was not just placed
        pick_index = next(
            (i for i, item in enumerate(queue) if (item["subjects"] or [""])[0] != last_subject),
            None,
        )
        if pick_index is None:
            pick_index = 0
        item = queue[pick_index]
        material_subjects = item["subjects"]
        slot = find_placeable(
            today,
            subjects=material_subjects,
            kind="learn",
            effort_hours=item["effort"],
        )
        if slot is None:
            # No candidate exists anywhere before the deadline — the capacity,
            # not a constraint of preference, is exhausted. The material is
            # not dropped silently: `unplaced` counts the lost demand
            # (remaining unschedulable slots) and surfaces on the receipt.
            unplaced += item["remaining"]
            queue.pop(pick_index)
            continue
        slot_start, slot_end, p_source, p_note = slot
        materialize(
            item["material"]["id"],
            "learn",
            slot_minutes,
            0,
            slot_start,
            slot_end,
            placement_source=p_source,
            placement_note=p_note,
        )
        place(slot_start, slot_end, material_subjects)
        item["remaining"] -= 1
        last_subject = material_subjects[0] if material_subjects else ""
        if item["remaining"] <= 0:
            queue.pop(pick_index)

    # --- jobs that could not keep a slot are removed; report them as moved ---
    moved_jobs: list[str] = []
    for slots in pool.values():
        for stale in slots.values():
            moved_jobs.append(stale["id"])
            conn.execute("DELETE FROM jobs WHERE id = ?", (stale["id"],))
            db.delete_event(conn, stale["event_id"])

    return {"moved_jobs": moved_jobs, "revisions_added": revisions_added, "unplaced": unplaced}


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
    placement_source: str = "engine",
    placement_note: str | None = None,
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
        placement_source=placement_source,
        placement_note=placement_note,
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
        placement_source="engine",
        placement_note=f"no feasible slot before the goal deadline (round {round_no})",
    )
