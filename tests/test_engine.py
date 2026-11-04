"""Deterministic engine tests — injected clock, temp SQLite, no network.

Covers invariants E1–E9 from docs/TESTING.md.
"""

import os
import tempfile

import pytest

from app import db, engine


@pytest.fixture
def conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = db.connect(path)
    db.init_db(c)
    try:
        yield c
    finally:
        c.close()
        os.unlink(path)


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
