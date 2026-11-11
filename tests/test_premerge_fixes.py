"""Focused regression tests for the better-llm-utilisation pre-merge fixes.

F1: ``unplaced`` counts lost demand (``item["remaining"]``), not one per
    material popped from the queue.
F2: the strictest enabled ``interleave`` row wins in
    ``job_adjacency_penalty`` (first match is no longer used).
F3: ``as_fatigue`` / ``as_subject`` never raise on corrupt annotation rows,
    and the engine replan tolerates messy annotations.
F4: ``upsert_annotation`` never clobbers an existing ``source='user'`` row.
"""

from datetime import datetime

import pytest

from app import db, engine
from app import constraints as constraints_mod


def _add_goal_material(conn, deadline="2026-12-18T00:00:00Z", effort=1.0):
    gid = db.create_goal(conn, title="G1", deadline=deadline)
    mid = db.create_material(
        conn,
        goal_id=gid,
        topic_id=None,
        filename="limits.md",
        original_path="/x/limits.md",
        markdown_path="/x/limits.md",
        content_hash="abc",
        summary="s",
        tags='["m"]',
        effort_hours=effort,
        status="tagged",
    )
    conn.commit()
    return gid, mid


def _replan(conn, gid, now="2026-11-03T12:00:00Z"):
    return engine.replan(conn, gid, now)


# ------------------------------------------------------------------ F1

def test_unplaced_counts_remaining_demand(conn):
    """3 unschedulable slots for one material -> unplaced == 3, not 1."""
    gid, _mid = _add_goal_material(conn, effort=2.0)  # ceil(2h / 50min) = 3 slots
    db.update_setting(conn, "daily_capacity_hours", "0.5")  # 30min < one 50min slot
    conn.commit()
    result = _replan(conn, gid)
    assert result["unplaced"] == 3
    learn = [j for j in db.jobs_for_goal(conn, gid) if j["kind"] == "learn"]
    assert learn == [], "nothing fits, so nothing is placed"


def test_unplaced_zero_when_slots_fit(conn):
    gid, _mid = _add_goal_material(conn, effort=1.0)  # 2 slots, plenty of capacity
    result = _replan(conn, gid)
    assert result["unplaced"] == 0
    assert len([j for j in db.jobs_for_goal(conn, gid) if j["kind"] == "learn"]) == 2


# ------------------------------------------------------------------ F2

def test_interleave_strictest_enabled_row_wins():
    from app.constraints import BUILTIN_INTERLEAVE_PENALTY, INTERLEAVE_PENALTY

    slot = datetime(2026, 11, 3, 10, 0)
    # Two same-subject jobs end exactly at the candidate (run of 2).
    entries = [
        (datetime(2026, 11, 3, 9, 0), datetime(2026, 11, 3, 9, 50), "m"),
        (datetime(2026, 11, 3, 9, 50), datetime(2026, 11, 3, 10, 0), "m"),
    ]
    loose = {"kind": "interleave", "params": {"max_consecutive": 4}}
    strict = {"kind": "interleave", "params": {"max_consecutive": 2}}

    # Listed loose-first so a first-match-wins implementation would use 4;
    # the strictest (2) must bind instead.
    penalty, reasons = constraints_mod.job_adjacency_penalty(
        slot, primary="m", preceding_entries=entries, constraints=[loose, strict], smart=True
    )
    assert penalty == BUILTIN_INTERLEAVE_PENALTY + INTERLEAVE_PENALTY
    assert any("more than 2 consecutive" in r for r in reasons)

    # Loose row alone: run of 2 does not reach max_consecutive=4.
    penalty, _ = constraints_mod.job_adjacency_penalty(
        slot, primary="m", preceding_entries=entries, constraints=[loose], smart=True
    )
    assert penalty == BUILTIN_INTERLEAVE_PENALTY

    # No interleave rows at all: no interleave penalty.
    penalty, _ = constraints_mod.job_adjacency_penalty(
        slot, primary="m", preceding_entries=entries, constraints=[], smart=True
    )
    assert penalty == BUILTIN_INTERLEAVE_PENALTY


# ------------------------------------------------------------------ F3

@pytest.mark.parametrize(
    "value,expected",
    [
        (0, 0), (1, 1), (2, 2), (3, 3),
        (5, 3), (-1, 0),  # clamped to 0..3
        (2.0, 2), (3.0, 3),  # integral floats
        (2.9, 0), (1.5, 0),  # non-integral floats -> 0
        (True, 0), (False, 0),  # bools -> 0
        ("2", 2), ("3", 3), ("2.0", 2),  # parseable strings
        ("2.9", 0), ("high", 0), ("", 0),  # unparseable / empty strings -> 0
        (None, 0),
    ],
)
def test_as_fatigue_matrix(value, expected):
    assert constraints_mod.as_fatigue(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Math", "math"),
        ("  MATH  ", "math"),
        ("a   b", "a b"),
        ("Algebra II", "algebra ii"),
        ("", "general"),
        ("   ", "general"),
        (None, "general"),
        (123, "123"),
    ],
)
def test_as_subject_matrix(value, expected):
    assert constraints_mod.as_subject(value) == expected


def test_replan_tolerates_messy_annotation_rows(conn):
    """Corrupt annotation rows are defaulted, never abort the replan."""
    gid, _mid = _add_goal_material(conn, effort=1.0)
    eid = db.create_event(
        conn,
        kind="external",
        title="Messy lecture",
        dtstart="2026-11-03T13:00:00Z",
        dtend="2026-11-03T14:00:00Z",
        locked=1,
    )
    # Hand-corrupted row: numeric subject, non-integral fatigue string.
    # CHECK constraints block such rows on a healthy DB, but the engine must
    # tolerate them (e.g. from an older schema or a hand-edit).
    conn.execute("PRAGMA ignore_check_constraints = 1")
    conn.execute(
        "INSERT INTO event_annotations(event_id, subject, fatigue, source, note, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (eid, 123, "high", "heuristic", "bad", "2026-11-03T12:00:00Z"),
    )
    conn.execute("PRAGMA ignore_check_constraints = 0")
    conn.commit()
    result = _replan(conn, gid)
    assert "unplaced" in result
    assert result["unplaced"] == 0
    assert len([j for j in db.jobs_for_goal(conn, gid) if j["kind"] == "learn"]) == 2


# ------------------------------------------------------------------ F4

def _annotation_row(conn, event_id):
    row = conn.execute(
        "SELECT subject, fatigue, source, note FROM event_annotations WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    return None if row is None else dict(row)


def _make_event(conn):
    return db.create_event(
        conn,
        kind="external",
        title="Lecture",
        dtstart="2026-11-03T13:00:00Z",
        dtend="2026-11-03T14:00:00Z",
        locked=1,
    )


def test_upsert_annotation_blocks_non_user_over_user(conn):
    eid = _make_event(conn)
    db.upsert_annotation(conn, eid, subject="math", fatigue=2, source="user", note="mine")
    conn.commit()
    db.upsert_annotation(conn, eid, subject="llm", fatigue=3, source="llm", note="theirs")
    conn.commit()
    assert _annotation_row(conn, eid) == {
        "subject": "math", "fatigue": 2, "source": "user", "note": "mine",
    }


def test_upsert_annotation_user_overwrites_own_row(conn):
    eid = _make_event(conn)
    db.upsert_annotation(conn, eid, subject="math", fatigue=2, source="user", note="mine")
    conn.commit()
    db.upsert_annotation(conn, eid, subject="physics", fatigue=1, source="user", note="updated")
    conn.commit()
    assert _annotation_row(conn, eid) == {
        "subject": "physics", "fatigue": 1, "source": "user", "note": "updated",
    }


def test_upsert_annotation_creates_missing_row(conn):
    eid = _make_event(conn)
    assert _annotation_row(conn, eid) is None
    db.upsert_annotation(conn, eid, subject="math", fatigue=2, source="llm", note="n")
    conn.commit()
    assert _annotation_row(conn, eid) == {
        "subject": "math", "fatigue": 2, "source": "llm", "note": "n",
    }


def test_upsert_annotation_non_user_overwrites_non_user(conn):
    eid = _make_event(conn)
    db.upsert_annotation(conn, eid, subject="math", fatigue=2, source="heuristic", note="h")
    conn.commit()
    db.upsert_annotation(conn, eid, subject="llm", fatigue=3, source="llm", note="l")
    conn.commit()
    assert _annotation_row(conn, eid) == {
        "subject": "llm", "fatigue": 3, "source": "llm", "note": "l",
    }
