"""Deterministic engine tests — injected clock, temp SQLite, no network.

Covers invariants E1–E13 from docs/TESTING.md.
"""

import shutil
import uuid
from pathlib import Path

import pytest

from app import db, engine

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def conn():
    base = _REPO_ROOT / "data" / "_test" / uuid.uuid4().hex
    base.mkdir(parents=True, exist_ok=True)
    path = base / "test.db"
    c = db.connect(path)
    db.init_db(c)
    try:
        yield c
    finally:
        c.close()
        shutil.rmtree(base, ignore_errors=True)


def _add_externals(conn):
    """Insert sample external events directly (UTC), avoiding the ICS path."""
    externals = [
        # Tue 3 Nov lecture 09:00-10:30 SGT = 01:00-02:30 UTC
        ("e1", "MATH2001 Lecture", "2026-11-03T01:00:00Z", "2026-11-03T02:30:00Z"),
        # Thu 5 Nov lecture 09:00-10:30 SGT
        ("e2", "MATH2001 Lecture", "2026-11-05T01:00:00Z", "2026-11-05T02:30:00Z"),
        # Fri 6 Nov lab 14:00-16:00 SGT = 06:00-08:00 UTC
        ("e3", "MATH2001 Lab", "2026-11-06T06:00:00Z", "2026-11-06T08:00:00Z"),
        # exam Fri 18 Dec 09:30-12:00 SGT = 01:30-04:00 UTC
        ("e4", "MATH2001 Final Exam", "2026-12-18T01:30:00Z", "2026-12-18T04:00:00Z"),
    ]
    for eid, title, ds, de in externals:
        db.create_event(
            conn, kind="external", title=title, dtstart=ds, dtend=de,
            locked=1, ics_uid=eid,
        )
    conn.commit()


def _add_goal_material(conn, deadline="2026-12-18T00:00:00Z", effort=3.0):
    gid = db.create_goal(conn, title="G1", deadline=deadline)
    mid = db.create_material(
        conn, goal_id=gid, topic_id=None, filename="limits.md",
        original_path="/x/limits.md", markdown_path="/x/limits.md",
        content_hash="abc", summary="s", tags='["m"]', effort_hours=effort,
        status="tagged",
    )
    conn.commit()
    return gid, mid


def _replan(conn, gid, now="2026-11-03T12:00:00Z"):
    return engine.replan(conn, gid, now)


def test_e1_no_job_overlaps_external(conn):
    _add_externals(conn)
    gid, mid = _add_goal_material(conn, effort=8.0)
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    jobs = db.jobs_for_goal(conn, gid)
    for j in jobs:
        js, je = j["slot_start"], j["slot_end"]
        for e in db.list_events(conn):
            if e["kind"] != "external":
                continue
            assert not (js < e["dtend"] and e["dtstart"] < je), (
                f"job {js}-{je} overlaps external {e['dtstart']}-{e['dtend']}"
            )


def test_e2_done_jobs_never_move(conn):
    _add_externals(conn)
    gid, mid = _add_goal_material(conn, effort=3.0)
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    jobs = db.jobs_for_goal(conn, gid)
    first = jobs[0]
    db.update_job(conn, first["id"], status="done", done_at="2026-11-03T12:00:00Z")
    conn.commit()
    slot_before = (first["slot_start"], first["slot_end"])
    for _ in range(5):
        _replan(conn, gid, now="2026-11-03T12:00:00Z")
    j_after = [j for j in db.jobs_for_goal(conn, gid) if j["id"] == first["id"]][0]
    assert (j_after["slot_start"], j_after["slot_end"]) == slot_before


def test_e3_idempotent(conn):
    _add_externals(conn)
    gid, mid = _add_goal_material(conn, effort=3.0)
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    slots1 = [(j["slot_start"], j["slot_end"]) for j in db.jobs_for_goal(conn, gid)]
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    slots2 = [(j["slot_start"], j["slot_end"]) for j in db.jobs_for_goal(conn, gid)]
    assert slots1 == slots2


def test_e4_todays_pending_keep_slots(conn):
    _add_externals(conn)
    gid, mid = _add_goal_material(conn, effort=2.0)
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    jobs = db.jobs_for_goal(conn, gid)
    todays = [j for j in jobs if j["slot_start"].startswith("2026-11-03")]
    assert todays, "expected a job today"
    today_slot = todays[0]["slot_start"]
    future = [j for j in jobs if not j["slot_start"].startswith("2026-11-03")]
    if future:
        db.update_job(conn, future[0]["id"], status="done", done_at="2026-11-03T12:00:00Z")
        conn.commit()
        _replan(conn, gid, now="2026-11-03T12:00:00Z")
    j = [j for j in db.jobs_for_goal(conn, gid) if j["slot_start"] == today_slot][0]
    assert j["slot_start"] == today_slot


def test_e5_capacity_cap_respected(conn):
    _add_externals(conn)
    gid, mid = _add_goal_material(conn, effort=20.0)
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    jobs = db.jobs_for_goal(conn, gid)
    from collections import Counter
    per_day = Counter(
        j["slot_start"][:10] for j in jobs if j["status"] in ("pending", "done")
    )
    for day, count in per_day.items():
        assert count <= 4, f"day {day} has {count} slots > cap 4"


def test_e6_revision_after_lab(conn):
    _add_externals(conn)
    gid, mid = _add_goal_material(conn, effort=1.0)
    _replan(conn, gid, now="2026-11-05T12:00:00Z")  # Thu
    jobs = db.jobs_for_goal(conn, gid)
    learn = [j for j in jobs if j["kind"] == "learn" and j["status"] == "pending"][0]
    db.update_job(conn, learn["id"], status="done", done_at="2026-11-05T14:00:00Z")
    conn.commit()
    _replan(conn, gid, now="2026-11-05T14:00:00Z")
    revisions = [j for j in db.jobs_for_goal(conn, gid) if j["kind"] == "revision"]
    r1 = [j for j in revisions if j["revision_round"] == 1][0]
    # R1 (+1d = Fri 6 Nov); lab 06:00-08:00 UTC -> R1 lands AFTER 08:00
    assert r1["slot_start"] >= "2026-11-06T08:00:00Z", r1


def test_e7_revisions_conflicted_not_dropped(conn):
    _add_externals(conn)
    gid, mid = _add_goal_material(conn, deadline="2026-11-04T00:00:00Z", effort=1.0)
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    jobs = db.jobs_for_goal(conn, gid)
    learn = [j for j in jobs if j["kind"] == "learn"][0]
    # block every revision candidate day (Nov 4..18) so no revision fits
    db.create_event(
        conn, kind="external", title="blocker",
        dtstart="2026-11-04T00:00:00Z", dtend="2026-11-19T00:00:00Z",
        locked=1, ics_uid="block",
    )
    conn.commit()
    db.update_job(conn, learn["id"], status="done", done_at="2026-11-03T12:00:00Z")
    conn.commit()
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    conflicted = [j for j in db.jobs_for_goal(conn, gid) if j["status"] == "conflicted"]
    assert conflicted, "expected a conflicted revision, none found"


def test_e8_deadline_respected(conn):
    _add_externals(conn)
    gid, mid = _add_goal_material(conn, deadline="2026-11-03T00:00:00Z", effort=50.0)
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    jobs = db.jobs_for_goal(conn, gid)
    for j in jobs:
        assert j["slot_start"] <= "2026-11-03T23:59:59Z", j


def test_e9_pull_forward(conn):
    _add_externals(conn)
    gid, mid = _add_goal_material(conn, effort=0.5)
    mid2 = db.create_material(
        conn, goal_id=gid, topic_id=None, filename="ps3.md",
        original_path="/x/ps3.md", markdown_path="/x/ps3.md",
        content_hash="def", summary="s2", tags='["m2"]', effort_hours=0.5,
        status="tagged",
    )
    conn.commit()
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    jobs = db.jobs_for_goal(conn, gid)
    learn_today = [
        j for j in jobs if j["kind"] == "learn" and j["slot_start"].startswith("2026-11-03")
    ]
    assert len(learn_today) >= 2, f"expected pull-forward, got {learn_today}"


# --- M3 deterministic scheduler / idempotency tests ---


def _complete_learn_job(conn, gid, job_id, done_at):
    db.update_job(conn, job_id, status="done", done_at=done_at)
    conn.commit()


def test_replan_twice_does_not_duplicate_revisions(conn):
    """(a) Replan twice from the same state: revision count stays N, no duplicates,
    same slot set (IDs may change for re-derived jobs)."""
    _add_externals(conn)
    gid, mid = _add_goal_material(conn, effort=1.0)
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    jobs = db.jobs_for_goal(conn, gid)
    learn = [j for j in jobs if j["kind"] == "learn"][0]
    _complete_learn_job(conn, gid, learn["id"], "2026-11-03T12:00:00Z")

    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    rev1 = [j for j in db.jobs_for_goal(conn, gid) if j["kind"] == "revision"]
    assert len(rev1) == 4, f"expected 4 revisions, got {len(rev1)}"
    slots1 = {(j["slot_start"], j["slot_end"]) for j in rev1}

    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    rev2 = [j for j in db.jobs_for_goal(conn, gid) if j["kind"] == "revision"]
    assert len(rev2) == 4, f"expected 4 revisions after 2nd replan, got {len(rev2)}"
    slots2 = {(j["slot_start"], j["slot_end"]) for j in rev2}
    assert slots1 == slots2, "revision slots must be identical across replans"


def test_completing_revision_does_not_respawn_round(conn):
    """(b) Marking revision round 1 done must not respawn round 1 on the next replan."""
    _add_externals(conn)
    gid, mid = _add_goal_material(conn, effort=1.0)
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    jobs = db.jobs_for_goal(conn, gid)
    learn = [j for j in jobs if j["kind"] == "learn"][0]
    _complete_learn_job(conn, gid, learn["id"], "2026-11-03T12:00:00Z")
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    revisions = [j for j in db.jobs_for_goal(conn, gid) if j["kind"] == "revision"]
    r1 = [j for j in revisions if j["revision_round"] == 1][0]
    r1_id = r1["id"]

    _complete_learn_job(conn, gid, r1_id, r1["slot_start"])
    _replan(conn, gid, now="2026-11-04T08:00:00Z")

    revisions2 = [j for j in db.jobs_for_goal(conn, gid) if j["kind"] == "revision"]
    round1 = [j for j in revisions2 if j["revision_round"] == 1]
    assert len(round1) == 1, f"round 1 must not respawn, got {round1}"
    assert round1[0]["id"] == r1_id
    assert round1[0]["status"] == "done"


def test_pinned_future_job_survives_replan(conn):
    """(c) A pinned future job survives replan at the exact same slot."""
    _add_externals(conn)
    gid, mid = _add_goal_material(conn, effort=0.5)
    event_id = db.create_event(
        conn, kind="study", title="pinned",
        dtstart="2026-11-10T14:00:00Z", dtend="2026-11-10T14:50:00Z", locked=0,
    )
    job_id = db.create_job(
        conn, event_id=event_id, goal_id=gid, material_id=mid,
        kind="learn", duration_min=50, status="pending", pinned=1,
    )
    conn.commit()

    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    j = db.get_job(conn, job_id)
    assert j["pinned"] == 1
    assert j["slot_start"] == "2026-11-10T14:00:00Z"
    assert j["slot_end"] == "2026-11-10T14:50:00Z"


def test_revisions_respect_daily_capacity(conn):
    """(d) With 1 h/day capacity, revisions never exceed one slot per day."""
    _add_externals(conn)
    db.update_setting(conn, "daily_capacity_hours", "1.0", updated_by="user")
    conn.commit()
    gid, mid = _add_goal_material(conn, effort=3.0)
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    jobs = db.jobs_for_goal(conn, gid)
    for j in jobs:
        if j["kind"] == "learn":
            _complete_learn_job(conn, gid, j["id"], j["slot_start"])
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    from collections import Counter
    per_day = Counter(
        j["slot_start"][:10]
        for j in db.jobs_for_goal(conn, gid)
        if j["kind"] == "revision" and j["status"] != "conflicted"
    )
    for day, count in per_day.items():
        assert count <= 1, f"day {day} has {count} revision slots > 1 h capacity"


def test_no_revision_after_goal_deadline(conn):
    """(e) No revision is placed after the goal deadline; overflow rounds become
    conflicted instead of being silently dropped."""
    _add_externals(conn)
    gid, mid = _add_goal_material(conn, deadline="2026-11-04T00:00:00Z", effort=1.0)
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    jobs = db.jobs_for_goal(conn, gid)
    learn = [j for j in jobs if j["kind"] == "learn"][0]
    _complete_learn_job(conn, gid, learn["id"], "2026-11-03T12:00:00Z")
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    revisions = [j for j in db.jobs_for_goal(conn, gid) if j["kind"] == "revision"]
    for j in revisions:
        if j["status"] != "conflicted":
            assert j["slot_start"] <= "2026-11-04T23:59:59Z", (
                f"revision {j['id']} placed after deadline: {j['slot_start']}"
            )
    conflicted = [j for j in revisions if j["status"] == "conflicted"]
    assert conflicted, "expected conflicted revisions for rounds past the deadline"


def test_revisions_two_learn_jobs_same_material_do_not_collide(conn):
    """(f) Revisions of two different learn jobs for the same material never
    collide/overwrite each other."""
    _add_externals(conn)
    gid, mid = _add_goal_material(conn, effort=1.0)
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    jobs = db.jobs_for_goal(conn, gid)
    learns = [j for j in jobs if j["kind"] == "learn"]
    assert len(learns) == 2, f"expected 2 learn jobs, got {len(learns)}"
    _complete_learn_job(conn, gid, learns[0]["id"], learns[0]["slot_start"])
    _complete_learn_job(conn, gid, learns[1]["id"], learns[1]["slot_start"])
    conn.commit()
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    revisions = [
        j for j in db.jobs_for_goal(conn, gid)
        if j["kind"] == "revision" and j["status"] != "conflicted"
    ]
    assert len(revisions) == 8, f"expected 8 revisions, got {len(revisions)}"
    for i in range(len(revisions)):
        for k in range(i + 1, len(revisions)):
            a, b = revisions[i], revisions[k]
            assert not (a["slot_start"] < b["slot_end"] and b["slot_start"] < a["slot_end"]), (
                f"revisions collide: {a['slot_start']}-{a['slot_end']} vs "
                f"{b['slot_start']}-{b['slot_end']}"
            )


def test_no_job_scheduled_in_the_past(conn):
    """(1) A job placed at now=2026-11-03T12:00:00Z never has slot_start < now."""
    _add_externals(conn)
    gid, mid = _add_goal_material(conn, effort=8.0)
    now = "2026-11-03T12:00:00Z"
    _replan(conn, gid, now=now)
    jobs = db.jobs_for_goal(conn, gid)
    assert jobs, "expected jobs to be placed"
    for j in jobs:
        assert j["slot_start"] >= now, (
            f"job {j['id']} starts in the past: {j['slot_start']} < {now}"
        )


def test_pinned_job_clobbered_by_external_is_moved(conn):
    """(2) A pinned future job whose slot overlaps an external event is deleted
    and re-placed unpinned so no job overlaps the external event (E1 invariant)."""
    _add_externals(conn)
    gid, mid = _add_goal_material(conn, effort=0.5)
    # External blocker on a future day, overlapping the pinned job's slot.
    blocker_id = db.create_event(
        conn, kind="external", title="blocker",
        dtstart="2026-11-10T14:00:00Z", dtend="2026-11-10T15:00:00Z",
        locked=1, ics_uid="blocker",
    )
    # Pinned job whose slot overlaps the blocker.
    event_id = db.create_event(
        conn, kind="study", title="pinned",
        dtstart="2026-11-10T14:00:00Z", dtend="2026-11-10T14:50:00Z", locked=0,
    )
    job_id = db.create_job(
        conn, event_id=event_id, goal_id=gid, material_id=mid,
        kind="learn", duration_min=50, status="pending", pinned=1,
    )
    conn.commit()

    _replan(conn, gid, now="2026-11-03T12:00:00Z")

    jobs = db.jobs_for_goal(conn, gid)
    # The clobbered pinned job must be deleted (re-derived, new id).
    assert not any(j["id"] == job_id for j in jobs), (
        "clobbered pinned job must be deleted and re-derived"
    )

    # No goal job may overlap the external blocker.
    blocker = db.get_event(conn, blocker_id)
    bs, be = blocker["dtstart"], blocker["dtend"]
    for j in jobs:
        assert not (j["slot_start"] < be and bs < j["slot_end"]), (
            f"job {j['id']} {j['slot_start']}-{j['slot_end']} overlaps external "
            f"blocker {bs}-{be}"
        )

    # The replacement learn job must be unpinned and present (effort still covered).
    replacements = [
        j for j in jobs
        if j["material_id"] == mid and j["kind"] == "learn"
    ]
    assert replacements, "expected a replacement learn job"
    assert all(j["pinned"] == 0 for j in replacements), (
        "replacement must be re-placed unpinned"
    )


# ---------------------------------------------------------------------------
# M5 regression: review findings B1/M3/M5
# ---------------------------------------------------------------------------

def _seed_done_learn(conn, goal_id, material_id, done_at, duration=50):
    event_id = db.create_event(
        conn,
        kind="study",
        title="old",
        dtstart="2026-10-31T08:00:00Z",
        dtend="2026-10-31T08:50:00Z",
        locked=1,
    )
    job_id = db.create_job(
        conn,
        event_id=event_id,
        goal_id=goal_id,
        material_id=material_id,
        kind="learn",
        duration_min=duration,
        status="done",
    )
    db.update_job(conn, job_id, done_at=done_at)
    return job_id


def test_revision_round_not_duplicated_for_today_kept_jobs(conn):
    """A surviving pending revision scheduled for today blocks re-creation."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    goal_id = db.create_goal(conn, title="G", deadline="2027-06-30T00:00:00Z")
    mat_id = db.new_id()
    db.create_material(
        conn,
        material_id=mat_id,
        goal_id=goal_id,
        topic_id=None,
        filename="x.md",
        original_path="/x",
        markdown_path="/x",
        content_hash="hx",
        summary=None,
        tags=None,
        effort_hours=1.0,
        status="ready",
    )
    learn_id = _seed_done_learn(conn, goal_id, mat_id, "2026-10-31T09:00:00Z")
    rev_event = db.create_event(
        conn,
        kind="revision",
        title="",
        dtstart=f"{today}T20:00:00Z",
        dtend=f"{today}T20:50:00Z",
        locked=1,
    )
    db.create_job(
        conn,
        event_id=rev_event,
        goal_id=goal_id,
        material_id=mat_id,
        kind="revision",
        duration_min=50,
        status="pending",
        revision_round=1,
        parent_job_id=learn_id,
    )
    now_iso = f"{today}T06:00:00Z"
    engine.replan(conn, goal_id, now_iso)
    rounds = [
        j for j in db.jobs_for_goal(conn, goal_id)
        if j["kind"] == "revision" and j["revision_round"] == 1
    ]
    assert len(rounds) == 1


def test_duplicate_and_error_materials_are_not_planned(conn):
    """Duplicate/superseded/error/processing materials get no jobs."""
    goal_id = db.create_goal(conn, title="G", deadline="2027-06-30T00:00:00Z")
    ready_id = db.new_id()
    for suffix, status in [("a", "ready"), ("b", "duplicate"), ("c", "error"),
                           ("d", "superseded"), ("e", "processing")]:
        db.create_material(
            conn,
            material_id=ready_id + suffix,
            goal_id=goal_id,
            topic_id=None,
            filename=f"{suffix}.md",
            original_path="/x",
            markdown_path="/x",
            content_hash="h" + suffix,
            summary=None,
            tags=None,
            effort_hours=0.5,
            status=status,
        )
    engine.replan(conn, goal_id, "2026-11-07T06:00:00Z")
    planned = {
        j["material_id"] for j in db.jobs_for_goal(conn, goal_id)
    }
    assert planned == {ready_id + "a"}


def test_revision_spacing_matches_intervals(conn):
    """Revision rounds land on done + revision_intervals_days (+1/+3/+7/+14)."""
    _add_externals(conn)
    gid, mid = _add_goal_material(conn, effort=1.0)
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    learn = [j for j in db.jobs_for_goal(conn, gid) if j["kind"] == "learn"][0]
    _complete_learn_job(conn, gid, learn["id"], "2026-11-03T12:00:00Z")
    _replan(conn, gid, now="2026-11-03T12:00:00Z")
    revs = {
        j["revision_round"]: j["slot_start"][:10]
        for j in db.jobs_for_goal(conn, gid)
        if j["kind"] == "revision"
    }
    assert revs == {1: "2026-11-04", 2: "2026-11-06", 3: "2026-11-10", 4: "2026-11-17"}
