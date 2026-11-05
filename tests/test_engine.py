"""Deterministic engine tests — injected clock, temp SQLite, no network.

Covers invariants E1–E9 from docs/TESTING.md plus the re-derive guarantees that
checkbox ticks depend on (future work survives a replan, manual moves stick).
"""

import pytest

from app import db, engine

NOW = "2026-11-03T12:00:00Z"


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
        status="ready",
    )
    conn.commit()
    return gid, mid


def _replan(conn, gid, now=NOW):
    return engine.replan(conn, gid, now)


def _slots(conn, gid):
    return [(j["slot_start"], j["slot_end"]) for j in db.jobs_for_goal(conn, gid)]


def test_e1_no_job_overlaps_external(conn):
    _add_externals(conn)
    gid, _ = _add_goal_material(conn, effort=8.0)
    _replan(conn, gid)
    jobs = db.jobs_for_goal(conn, gid)
    assert len(jobs) > 4, "expected work to spill over several days"
    for job in jobs:
        for event in db.list_events(conn):
            if event["kind"] != "external":
                continue
            assert not (job["slot_start"] < event["dtend"] and event["dtstart"] < job["slot_end"]), (
                f"job {job['slot_start']}-{job['slot_end']} overlaps "
                f"external {event['dtstart']}-{event['dtend']}"
            )


def test_e2_done_jobs_never_move(conn):
    _add_externals(conn)
    gid, _ = _add_goal_material(conn, effort=3.0)
    _replan(conn, gid)
    first = db.jobs_for_goal(conn, gid)[0]
    db.update_job(conn, first["id"], status="done", done_at=NOW)
    conn.commit()
    before = (first["slot_start"], first["slot_end"])
    for _ in range(5):
        _replan(conn, gid)
    after = [j for j in db.jobs_for_goal(conn, gid) if j["id"] == first["id"]][0]
    assert (after["slot_start"], after["slot_end"]) == before


def test_e3_idempotent(conn):
    _add_externals(conn)
    gid, _ = _add_goal_material(conn, effort=8.0)
    _replan(conn, gid)
    first = _slots(conn, gid)
    _replan(conn, gid)
    assert _slots(conn, gid) == first
    _replan(conn, gid)
    assert _slots(conn, gid) == first


def test_e4_todays_pending_keep_slots(conn):
    _add_externals(conn)
    gid, _ = _add_goal_material(conn, effort=2.0)
    _replan(conn, gid)
    jobs = db.jobs_for_goal(conn, gid)
    todays = [j for j in jobs if j["slot_start"].startswith("2026-11-03")]
    assert todays, "expected a job today"
    today_slot = todays[0]["slot_start"]
    future = [j for j in jobs if not j["slot_start"].startswith("2026-11-03")]
    if future:
        db.update_job(conn, future[0]["id"], status="done", done_at=NOW)
        conn.commit()
        _replan(conn, gid)
    still_there = [j for j in db.jobs_for_goal(conn, gid) if j["slot_start"] == today_slot]
    assert still_there, "today's job lost its slot"


def test_e5_capacity_cap_respected(conn):
    _add_externals(conn)
    gid, _ = _add_goal_material(conn, effort=20.0)
    _replan(conn, gid)
    from collections import Counter

    per_day = Counter(
        j["slot_start"][:10]
        for j in db.jobs_for_goal(conn, gid)
        if j["status"] in ("pending", "done")
    )
    assert per_day, "expected jobs to be placed"
    for day, count in per_day.items():
        assert count <= 4, f"day {day} has {count} slots > cap 4"


def test_e6_revision_after_lab(conn):
    _add_externals(conn)
    gid, _ = _add_goal_material(conn, effort=1.0)
    _replan(conn, gid, now="2026-11-05T12:00:00Z")  # Thursday
    learn = [j for j in db.jobs_for_goal(conn, gid) if j["kind"] == "learn"][0]
    db.update_job(conn, learn["id"], status="done", done_at="2026-11-05T14:00:00Z")
    conn.commit()
    _replan(conn, gid, now="2026-11-05T14:00:00Z")
    revisions = [j for j in db.jobs_for_goal(conn, gid) if j["kind"] == "revision"]
    r1 = [j for j in revisions if j["revision_round"] == 1][0]
    # R1 target is Fri 6 Nov; the lab occupies 06:00-08:00 UTC -> R1 lands after it
    assert r1["slot_start"] >= "2026-11-06T08:00:00Z", r1


def test_e7_revisions_conflicted_not_dropped(conn):
    _add_externals(conn)
    gid, _ = _add_goal_material(conn, deadline="2026-11-04T00:00:00Z", effort=1.0)
    _replan(conn, gid)
    learn = [j for j in db.jobs_for_goal(conn, gid) if j["kind"] == "learn"][0]
    # block every revision candidate day (Nov 4 .. Nov 18)
    db.create_event(
        conn, kind="external", title="blocker",
        dtstart="2026-11-04T00:00:00Z", dtend="2026-11-19T00:00:00Z",
        locked=1, ics_uid="block",
    )
    conn.commit()
    db.update_job(conn, learn["id"], status="done", done_at=NOW)
    conn.commit()
    _replan(conn, gid)
    conflicted = [j for j in db.jobs_for_goal(conn, gid) if j["status"] == "conflicted"]
    assert conflicted, "expected a conflicted revision, none found"


def test_e8_deadline_respected(conn):
    _add_externals(conn)
    gid, _ = _add_goal_material(conn, deadline="2026-11-03T00:00:00Z", effort=50.0)
    _replan(conn, gid)
    jobs = db.jobs_for_goal(conn, gid)
    assert jobs
    for job in jobs:
        assert job["slot_start"] <= "2026-11-03T23:59:59Z", job


def test_e9_pull_forward(conn):
    _add_externals(conn)
    gid, _ = _add_goal_material(conn, effort=0.5)
    db.create_material(
        conn, goal_id=gid, topic_id=None, filename="ps3.md",
        original_path="/x/ps3.md", markdown_path="/x/ps3.md",
        content_hash="def", summary="s2", tags='["m2"]', effort_hours=0.5,
        status="ready",
    )
    conn.commit()
    _replan(conn, gid)
    learn_today = [
        j for j in db.jobs_for_goal(conn, gid)
        if j["kind"] == "learn" and j["slot_start"].startswith("2026-11-03")
    ]
    assert len(learn_today) >= 2, f"expected pull-forward, got {learn_today}"


def test_future_work_survives_replan(conn):
    """Regression: re-derived future jobs must be re-created, not deleted."""
    _add_externals(conn)
    gid, _ = _add_goal_material(conn, effort=8.0)
    _replan(conn, gid)
    first = _slots(conn, gid)
    assert len(first) == 10, first
    _replan(conn, gid)
    assert len(db.jobs_for_goal(conn, gid)) == 10
    _replan(conn, gid)
    assert _slots(conn, gid) == first


def test_pinned_job_keeps_its_slot(conn):
    """A job the user dragged by hand is not re-derived away."""
    _add_externals(conn)
    gid, _ = _add_goal_material(conn, effort=8.0)
    _replan(conn, gid)
    future = [j for j in db.jobs_for_goal(conn, gid) if not j["slot_start"].startswith("2026-11-03")]
    target = future[0]
    db.update_event(
        conn, target["event_id"],
        dtstart="2026-11-10T09:00:00Z", dtend="2026-11-10T09:50:00Z",
    )
    db.update_job(conn, target["id"], pinned=1, duration_min=50)
    conn.commit()
    _replan(conn, gid)
    after = [j for j in db.jobs_for_goal(conn, gid) if j["id"] == target["id"]]
    assert after and after[0]["slot_start"] == "2026-11-10T09:00:00Z"


def test_conflicted_revisions_are_not_duplicated(conn):
    _add_externals(conn)
    gid, _ = _add_goal_material(conn, deadline="2026-11-04T00:00:00Z", effort=1.0)
    _replan(conn, gid)
    learn = [j for j in db.jobs_for_goal(conn, gid) if j["kind"] == "learn"][0]
    db.create_event(
        conn, kind="external", title="blocker",
        dtstart="2026-11-04T00:00:00Z", dtend="2026-11-19T00:00:00Z",
        locked=1, ics_uid="block",
    )
    conn.commit()
    db.update_job(conn, learn["id"], status="done", done_at=NOW)
    conn.commit()
    _replan(conn, gid)
    count_one = len([j for j in db.jobs_for_goal(conn, gid) if j["status"] == "conflicted"])
    _replan(conn, gid)
    count_two = len([j for j in db.jobs_for_goal(conn, gid) if j["status"] == "conflicted"])
    assert count_one == count_two == 4, (count_one, count_two)


def test_replan_does_not_churn_job_ids(conn):
    """A replan that changes nothing must not delete/recreate jobs."""
    _add_externals(conn)
    gid, _ = _add_goal_material(conn, effort=8.0)
    _replan(conn, gid)
    ids_before = {j["id"] for j in db.jobs_for_goal(conn, gid)}
    result = _replan(conn, gid)
    assert result["moved_jobs"] == []
    assert {j["id"] for j in db.jobs_for_goal(conn, gid)} == ids_before


def test_repeated_replan_keeps_revision_jobs(conn):
    _add_externals(conn)
    gid, _ = _add_goal_material(conn, effort=1.0)
    _replan(conn, gid)
    learn = [j for j in db.jobs_for_goal(conn, gid) if j["kind"] == "learn"][0]
    db.update_job(conn, learn["id"], status="done", done_at=NOW)
    conn.commit()
    _replan(conn, gid)
    revisions = {j["id"] for j in db.jobs_for_goal(conn, gid) if j["kind"] == "revision"}
    assert len(revisions) == 4
    again = _replan(conn, gid)
    assert again["revisions_added"] == []
    assert {j["id"] for j in db.jobs_for_goal(conn, gid) if j["kind"] == "revision"} == revisions


def test_local_offset_shifts_slot_window(conn):
    """With a +8 h local clock the first slot starts at 00:00 UTC."""
    _add_externals(conn)
    db.update_setting(conn, "utc_offset_minutes", "480")
    conn.commit()
    gid, _ = _add_goal_material(conn, effort=1.0)
    _replan(conn, gid, now="2026-11-03T20:00:00Z")  # 04:00 local, Wed
    first = db.jobs_for_goal(conn, gid)[0]
    # local 08:00 Wed 4 Nov == 00:00 UTC Wed 4 Nov
    assert first["slot_start"] == "2026-11-04T00:00:00Z", first
