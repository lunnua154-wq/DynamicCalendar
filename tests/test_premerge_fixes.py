"""Focused regression tests for the better-llm-utilisation pre-merge fixes.

F1: ``unplaced`` counts lost demand (``item["remaining"]``), not one per
    material popped from the queue.
F2: the strictest enabled ``interleave`` row wins in
    ``job_adjacency_penalty`` (first match is no longer used).
F3: ``as_fatigue`` / ``as_subject`` never raise on corrupt annotation rows,
    and the engine replan tolerates messy annotations.
F4: ``upsert_annotation`` never clobbers an existing ``source='user'`` row.
B1: ``as_bool`` is a strict JSON boolean helper; ``add_constraint`` rejects
    non-boolean ``hard`` (incl. the string "false") with ValidationError.
B2: the placement chooser's per-call timeout is bounded by the remaining
    wall-clock budget of the replan.
B3: ``connect()`` sets ``PRAGMA busy_timeout = 10000``.
B4: corrupt numeric settings degrade to defaults; the PUT path still rejects.
B5: constraint labels strip control characters and collapse whitespace before
    the 120-char cap.
C1: ``POST /api/events/annotate`` rejects non-boolean ``force`` with 422
    (missing -> False).
C2: the annotate endpoint does LLM/heuristic work outside the write
    transaction; only the force-delete + upserts run inside it.
C3: every replan-receipt returned to a client surfaces ``unplaced``.
C4: ``planner.replan(..., allow_llm_chooser=False)` skips the LLM chooser.
C5: startup/import backfill failures are logged, not swallowed.
C6: the offline NL constraint path goes through ``actions.add_constraint``.
C7: chat-tool replan failures roll back the constraint insert.
"""

import contextlib
import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app import actions, config, db, engine, llm as llm_mod, planner, tools
from app import constraints as constraints_mod
from app.errors import ValidationError


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


# ------------------------------------------------------------------ B1

@pytest.mark.parametrize("value", [True, False])
def test_as_bool_accepts_only_bools(value):
    assert constraints_mod.as_bool(value) is value


@pytest.mark.parametrize(
    "value",
    ["true", "false", "TRUE", "False", "yes", "no", 1, 0, 1.0, 0.0, None, "", [], {}],
)
def test_as_bool_rejects_everything_else(value):
    with pytest.raises(ValueError, match="must be a JSON boolean true/false"):
        constraints_mod.as_bool(value)


def test_add_constraint_rejects_non_bool_hard(conn):
    """'hard' is a strict JSON boolean: the string "false" is rejected."""
    base = dict(
        kind="subject_gap",
        params={"subject": "math", "gap_minutes": 60},
        source="user",
    )
    for bad in ("false", "true", "yes", 1, 0, 1.0, None, "0"):
        with pytest.raises(ValidationError):
            actions.add_constraint(conn, hard=bad, **base)
    # real booleans pass validation; distinct params so each call creates its
    # own row (no dedup short-circuit), and the replan is a no-op with no open
    # goals
    for hard in (False, True):
        out = actions.add_constraint(
            conn, hard=hard, params={"subject": f"math-{hard}", "gap_minutes": 60},
            **{k: v for k, v in base.items() if k != "params"},
        )
        assert out["constraint"]["hard"] is hard


def test_rest_endpoint_rejects_string_hard(tmp_path):
    """REST-facing action: POST /api/constraints with "hard": "false" -> 422."""
    from fastapi.testclient import TestClient

    import app.main as main_mod

    path = tmp_path / "rest.db"
    conn = db.connect(path)
    db.init_db(conn)
    conn.commit()
    conn.close()

    previous = main_mod.DEFAULT_DB_PATH
    main_mod.DEFAULT_DB_PATH = path
    try:
        client = TestClient(main_mod.app)
        try:
            r = client.post(
                "/api/constraints",
                json={
                    "kind": "subject_gap",
                    "params": {"subject": "math", "gap_minutes": 60},
                    "hard": "false",
                },
            )
            assert r.status_code == 422, r.text
            r = client.post(
                "/api/constraints",
                json={
                    "kind": "subject_gap",
                    "params": {"subject": "math", "gap_minutes": 60},
                    "hard": True,
                },
            )
            assert r.status_code == 201, r.text
        finally:
            client.close()
    finally:
        main_mod.DEFAULT_DB_PATH = previous


# ------------------------------------------------------------------ B2

def _llm_settings(llm_enabled=True):
    from app.config import Settings

    return Settings(
        daily_capacity_hours=4.0,
        slot_minutes=50,
        min_slot_minutes=30,
        revision_intervals_days=[1, 3, 7, 14],
        llm_base_url="http://localhost:11434/v1" if llm_enabled else "",
        llm_model="test-model" if llm_enabled else "",
        llm_api_key="",
        default_effort_hours=1.0,
    )


def _candidates(n=1):
    out = []
    for i in range(n):
        out.append(
            {
                "index": i,
                "slot_start": datetime(2026, 11, 3, 8 + i, 0),
                "slot_end": datetime(2026, 11, 3, 8 + i, 50),
                "penalty": float(i * 30),
                "reasons": [f"reason-{i}"] if i else [],
            }
        )
    return out


def test_choose_placement_timeout_is_forwarded():
    """choose_placement forwards an explicit timeout to the client call."""
    from app.llm import LLMClient

    c = LLMClient(_llm_settings())
    fake = MagicMock()
    fake.with_options.return_value = fake
    fake.chat.completions.create.return_value.choices[0].message.content = (
        '{"choice": 0, "reason": "ok"}'
    )
    c._client = fake

    c.choose_placement(job_context={}, candidates=_candidates(1), timeout=3.5)
    kwargs = c._client.with_options.call_args.kwargs
    assert kwargs == {"max_retries": 0, "timeout": 3.5}

    c.choose_placement(job_context={}, candidates=_candidates(1))  # default: 15 s
    kwargs = c._client.with_options.call_args.kwargs
    assert kwargs == {"max_retries": 0, "timeout": 15}


def test_planner_passes_remaining_budget_as_timeout(conn, monkeypatch):
    """The planner computes min(15, max(1, remaining)) before each chooser call."""
    from app import llm as llm_mod, planner

    db.update_setting(conn, "llm_placement_mode", json.dumps("rank"))
    db.update_setting(conn, "llm_base_url", json.dumps("http://localhost:9/v1"))
    db.update_setting(conn, "llm_model", json.dumps("test-model"))
    conn.commit()

    gid, _ = _add_goal_material(conn, effort=0.8)  # one job -> one chooser call
    seen = {}

    def fake_choose(self, *, job_context, candidates, timeout=None):
        seen["timeout"] = timeout
        return {"choice": candidates[0]["index"], "reason": "rank pick"}

    monkeypatch.setattr(llm_mod.LLMClient, "choose_placement", fake_choose)
    planner.replan(conn, gid, "2026-11-03T12:00:00Z")
    assert seen["timeout"] == 15  # plenty of budget left -> full default window


# ------------------------------------------------------------------ B3

def test_connect_sets_busy_timeout(tmp_path):
    path = tmp_path / "busy.db"
    conn = db.connect(path)
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
    finally:
        conn.close()


# ------------------------------------------------------------------ B4

@pytest.mark.parametrize(
    "key,corrupt,expected",
    [
        ("daily_capacity_hours", "not-a-number", 4.0),
        ("daily_capacity_hours", "[1, 2]", 4.0),  # valid JSON, wrong type
        ("slot_minutes", "high", 50),
        ("default_effort_hours", "x", 1.0),
        ("ocr_min_chars", "nope", 40),
        ("utc_offset_minutes", "zzz", 0),
        ("revision_intervals_days", "not json", [1, 3, 7, 14]),
        ("revision_intervals_days", "{", [1, 3, 7, 14]),  # truncated JSON
    ],
)
def test_corrupt_numeric_settings_degrade_to_defaults(conn, key, corrupt, expected):
    db.update_setting(conn, key, corrupt, "user")
    conn.commit()
    settings = config.parse_settings(db.list_settings(conn))
    assert getattr(settings, key) == expected
    # parse_settings never writes, so re-parsing the same corrupt row still
    # degrades instead of crashing
    settings = config.parse_settings(db.list_settings(conn))
    assert getattr(settings, key) == expected


def test_put_settings_still_rejects_out_of_bounds(conn):
    """PUT-path validation is untouched: out-of-bounds still raises."""
    with pytest.raises(ValidationError):
        actions.update_settings(
            conn, {"daily_capacity_hours": 999}, "2026-11-03T12:00:00Z"
        )
    with pytest.raises(ValidationError):
        actions.update_settings(
            conn, {"slot_minutes": 999}, "2026-11-03T12:00:00Z"
        )


# ------------------------------------------------------------------ B5

def test_add_constraint_label_sanitizes_control_characters(conn):
    out = actions.add_constraint(
        conn,
        kind="subject_gap",
        params={"subject": "math", "gap_minutes": 60},
        label="  math\trule \x01\x1f after\x7f class  ",
        source="user",
    )
    assert out["constraint"]["label"] == "mathrule after class"


def test_add_constraint_label_hits_120_char_cap(conn):
    out = actions.add_constraint(
        conn,
        kind="subject_gap",
        params={"subject": "math", "gap_minutes": 60},
        label="a " * 200,
        source="user",
    )
    label = out["constraint"]["label"]
    assert len(label) == 120
    assert label == "a " * 60


# ------------------------------------------------------------------ C1

@contextlib.contextmanager
def _ann_client_at(tmp_path):
    path = tmp_path / "ann.db"
    conn = db.connect(path)
    db.init_db(conn)
    db.create_event(
        conn, kind="external", title="Lecture",
        dtstart="2026-11-05T08:00:00Z", dtend="2026-11-05T12:00:00Z",
        locked=1, ics_uid="uid-ann-c1",
    )
    conn.commit()
    conn.close()
    previous = main_mod.DEFAULT_DB_PATH
    main_mod.DEFAULT_DB_PATH = path
    try:
        client = TestClient(main_mod.app)
        yield client, path
    finally:
        client.close()
        main_mod.DEFAULT_DB_PATH = previous


def test_annotate_endpoint_rejects_non_bool_force(tmp_path):
    """Missing -> False; anything but a JSON boolean -> 422."""
    with _ann_client_at(tmp_path) as (client, _path):
        for bad in ("false", "true", "TRUE", 1, 0, 1.0, 0.0, None, "", [], {}):
            r = client.post("/api/events/annotate", json={"force": bad})
            assert r.status_code == 422, (bad, r.text)
        r = client.post("/api/events/annotate", json={})
        assert r.status_code == 200, r.text
        assert r.json()["annotated"] == 1
        r = client.post("/api/events/annotate", json={"force": True})
        assert r.status_code == 200, r.text
        r = client.post("/api/events/annotate", json={"force": False})
        assert r.status_code == 200, r.text


# ------------------------------------------------------------------ C2

def test_annotate_endpoint_uses_fetch_seam(tmp_path, monkeypatch):
    """LLM/heuristic items are obtained outside the write transaction; only
    the force-delete and upserts run inside it. The fetch seam is exercised
    and its items are persisted with the response count intact."""
    path = tmp_path / "ann2.db"
    conn = db.connect(path)
    db.init_db(conn)
    eid1 = db.create_event(
        conn, kind="external", title="MATH2001 Lecture",
        dtstart="2026-11-10T08:00:00Z", dtend="2026-11-10T10:00:00Z",
        locked=1, ics_uid="uid-c2-1",
    )
    eid2 = db.create_event(
        conn, kind="external", title="CS1010 Seminar",
        dtstart="2026-11-10T10:00:00Z", dtend="2026-11-10T11:00:00Z",
        locked=1, ics_uid="uid-c2-2",
    )
    conn.commit()
    conn.close()

    captured = {}

    def fake_fetch(_conn, events, *, llm=None, time_budget_seconds=None):
        captured["ids"] = [e["id"] for e in events]
        return {
            e["id"]: {
                "event_id": e["id"], "subject": "math", "fatigue": 2,
                "source": "llm", "note": "fake-note",
            }
            for e in events
        }

    monkeypatch.setattr(main_mod.annotate, "fetch_annotation_items", fake_fetch)

    previous = main_mod.DEFAULT_DB_PATH
    main_mod.DEFAULT_DB_PATH = path
    try:
        client = TestClient(main_mod.app)
        try:
            r = client.post("/api/events/annotate", json={})
            assert r.status_code == 200, r.text
            assert r.json()["annotated"] == 2
            assert set(captured["ids"]) == {eid1, eid2}
        finally:
            client.close()
    finally:
        main_mod.DEFAULT_DB_PATH = previous

    check = db.connect(path)
    try:
        rows = {row["event_id"]: row for row in check.execute(
            "SELECT event_id, subject, fatigue, source, note FROM event_annotations"
        ).fetchall()}
    finally:
        check.close()
    assert set(rows) == {eid1, eid2}
    assert all(
        rows[eid]["subject"] == "math" and rows[eid]["fatigue"] == 2
        and rows[eid]["source"] == "llm" and rows[eid]["note"] == "fake-note"
        for eid in rows
    )


# ------------------------------------------------------------------ C3

def _c3_seed(path):
    conn = db.connect(path)
    db.init_db(conn)
    gid = db.create_goal(conn, title="G1", deadline="2026-12-18T00:00:00Z")
    db.create_material(
        conn, goal_id=gid, topic_id=None, filename="m.md",
        original_path="/x/m.md", markdown_path="/x/m.md",
        content_hash="hash-m", summary="s", tags='["math"]',
        effort_hours=1.0, status="tagged",
    )
    conn.commit()
    conn.close()
    return gid


def test_complete_job_response_includes_unplaced(tmp_path):
    path = tmp_path / "c3.db"
    gid = _c3_seed(path)
    previous = main_mod.DEFAULT_DB_PATH
    main_mod.DEFAULT_DB_PATH = path
    try:
        client = TestClient(main_mod.app)
        try:
            r = client.post(f"/api/goals/{gid}/replan")
            assert r.status_code == 200, r.text
            jobs = [j for j in client.get("/api/jobs").json() if j["goal_id"] == gid]
            assert jobs, "replan should schedule learn jobs"
            r = client.post(f"/api/jobs/{jobs[0]['id']}/complete")
            assert r.status_code == 200, r.text
            body = r.json()
            assert "unplaced" in body
            assert isinstance(body["unplaced"], int)
        finally:
            client.close()
    finally:
        main_mod.DEFAULT_DB_PATH = previous


def test_move_job_response_includes_unplaced(tmp_path):
    path = tmp_path / "c3-move.db"
    gid = _c3_seed(path)
    previous = main_mod.DEFAULT_DB_PATH
    main_mod.DEFAULT_DB_PATH = path
    try:
        client = TestClient(main_mod.app)
        try:
            r = client.post(f"/api/goals/{gid}/replan")
            assert r.status_code == 200, r.text
            jobs = [j for j in client.get("/api/jobs").json() if j["goal_id"] == gid]
            assert jobs
            job = jobs[0]
            start = datetime.fromisoformat(job["slot_start"].replace("Z", "+00:00"))
            duration = timedelta(minutes=job["duration_min"])
            new_start = (start + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            new_end = (start + duration + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            r = client.post(
                f"/api/jobs/{job['id']}/move",
                json={"slot_start": new_start, "slot_end": new_end},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert "unplaced" in body
            assert isinstance(body["unplaced"], int)
        finally:
            client.close()
    finally:
        main_mod.DEFAULT_DB_PATH = previous


def test_attach_ready_response_includes_unplaced(tmp_path):
    path = tmp_path / "c3-attach.db"
    conn = db.connect(path)
    db.init_db(conn)
    gid = db.create_goal(conn, title="G1", deadline="2026-12-18T00:00:00Z")
    for name in ("a.md", "b.md"):
        db.create_material(
            conn, goal_id=None, topic_id=None, filename=name,
            original_path=f"/x/{name}", markdown_path=f"/x/{name}",
            content_hash="hash-" + name, summary="s", tags='[]',
            effort_hours=1.0, status="ready",
        )
    conn.commit()
    conn.close()
    previous = main_mod.DEFAULT_DB_PATH
    main_mod.DEFAULT_DB_PATH = path
    try:
        client = TestClient(main_mod.app)
        try:
            r = client.post("/api/inbox/attach_ready", json={"goal_id": gid})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["attached"] == 2
            assert "unplaced" in body
            assert isinstance(body["unplaced"], int)
        finally:
            client.close()
    finally:
        main_mod.DEFAULT_DB_PATH = previous


def test_constraint_receipts_include_unplaced(conn):
    out = actions.add_constraint(
        conn, kind="subject_gap",
        params={"subject": "math", "gap_minutes": 60}, label="gap",
        source="user", now_iso="2026-11-07T12:00:00Z",
    )
    assert "unplaced" in out and isinstance(out["unplaced"], int)
    assert out["replanned_goals"] == 0  # no open goals -> no replan
    out2 = actions.remove_constraint(
        conn, out["constraint"]["id"], now_iso="2026-11-07T12:00:00Z",
    )
    assert "unplaced" in out2 and isinstance(out2["unplaced"], int)
    assert out2["deleted"] == out["constraint"]["id"]


# ------------------------------------------------------------------ C4

def test_planner_replan_skips_llm_chooser_when_disallowed(conn, monkeypatch):
    db.update_setting(conn, "llm_placement_mode", json.dumps("rank"))
    db.update_setting(conn, "llm_base_url", json.dumps("http://localhost:9/v1"))
    db.update_setting(conn, "llm_model", json.dumps("test-model"))
    conn.commit()
    gid_default, _ = _add_goal_material(conn, effort=1.6)  # two slots -> multiple candidates
    gid_disallowed, _ = _add_goal_material(
        conn, effort=1.6, deadline="2026-12-19T00:00:00Z"
    )

    seen = {}

    def fake_choose(self, *, job_context, candidates, timeout=None):
        seen["called"] = True
        return {"choice": candidates[0]["index"], "reason": "rank pick"}

    monkeypatch.setattr(llm_mod.LLMClient, "choose_placement", fake_choose)

    # default behaviour is unchanged: the chooser is injected in rank mode
    planner.replan(conn, gid_default, "2026-11-03T12:00:00Z")
    assert seen.get("called") is True

    seen.clear()
    planner.replan(conn, gid_disallowed, "2026-11-03T12:00:00Z", allow_llm_chooser=False)
    assert "called" not in seen, "chooser must not be injected when disallowed"


# ------------------------------------------------------------------ C5

def test_startup_backfill_failure_is_logged(tmp_path, caplog, monkeypatch):
    path = tmp_path / "startup.db"
    conn = db.connect(path)
    db.init_db(conn)
    conn.commit()
    conn.close()
    previous = main_mod.DEFAULT_DB_PATH
    main_mod.DEFAULT_DB_PATH = path
    monkeypatch.setattr(
        main_mod.annotate, "annotate_missing",
        lambda conn, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    try:
        caplog.set_level(logging.WARNING)
        with TestClient(main_mod.app):
            pass
    finally:
        main_mod.DEFAULT_DB_PATH = previous
    assert any(
        r.name == "app.main" and r.levelno == logging.WARNING
        and "backfill" in r.getMessage()
        for r in caplog.records
    ), caplog.messages


# ------------------------------------------------------------------ C6

def test_offline_nl_constraint_goes_through_shared_action(conn):
    """No LLM: the NL path creates the constraint via actions.add_constraint
    (validation, de-dup, rank-aware replan, single-sourced receipts) and keeps
    the '... saved the rule ...' reply + llm: 'off' shape."""
    _add_goal_material(conn, effort=1.0)  # tags: ["m"] -> subject "m" is known
    c = llm_mod.LLMClient(_offline_settings())
    out = c.chat(
        message="avoid m right after m class",
        history=[], context={}, conn=conn,
        now_iso="2026-11-07T12:00:00Z",
    )
    assert out["llm"] == "off"
    assert "saved the rule" in out["reply"].lower()
    assert "Re-planned 1 open goal(s)" in out["reply"]
    row = conn.execute("SELECT * FROM constraints").fetchone()
    assert row["kind"] == "subject_gap"
    assert row["source"] == "user"


def _offline_settings():
    from app.config import Settings
    return Settings(
        daily_capacity_hours=4.0, slot_minutes=50, min_slot_minutes=30,
        revision_intervals_days=[1, 3, 7, 14],
        llm_base_url="", llm_model="", llm_api_key="",
        default_effort_hours=1.0,
    )


# ------------------------------------------------------------------ C7

def test_chat_add_constraint_replan_failure_rolls_back(conn, monkeypatch):
    """A replan-phase failure propagates out of dispatch (not an error
    receipt) and rolls back the constraint insert. Validation failures keep
    the error-receipt behaviour."""
    _add_goal_material(conn, effort=1.0)
    conn.commit()

    def boom(conn, goal_id, now_iso):
        raise RuntimeError("planner wedged")

    monkeypatch.setattr(actions.planner, "replan", boom)

    with pytest.raises(actions.ReplanError):  # propagates out of the block -> rollback
        with conn:
            tools.dispatch(
                conn, "add_constraint",
                {"kind": "subject_gap", "params": {"subject": "math", "gap_minutes": 60}},
                "2026-11-07T12:00:00Z",
            )
    assert conn.execute("SELECT COUNT(*) AS n FROM constraints").fetchone()["n"] == 0

    result = tools.dispatch(
        conn, "add_constraint", {"kind": "yolo", "params": {}},
        "2026-11-07T12:00:00Z",
    )
    assert "error" in result
    assert conn.execute("SELECT COUNT(*) AS n FROM constraints").fetchone()["n"] == 0


def test_chat_remove_constraint_replan_failure_rolls_back(conn, monkeypatch):
    _add_goal_material(conn, effort=1.0)
    conn.commit()
    cid = db.create_constraint(
        conn, kind="interleave",
        params=constraints_mod.serialise_params({"max_consecutive": 2}),
        hard=False, source="user", label="max two",
    )
    conn.commit()

    def boom(conn, goal_id, now_iso):
        raise RuntimeError("planner wedged")

    monkeypatch.setattr(actions.planner, "replan", boom)

    with pytest.raises(actions.ReplanError):  # propagates out of the block -> rollback
        with conn:
            tools.dispatch(
                conn, "remove_constraint",
                {"constraint_id": cid}, "2026-11-07T12:00:00Z",
            )
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM constraints WHERE id = ?", (cid,)
    ).fetchone()["n"] == 1  # delete rolled back too


def test_chat_route_replan_failure_returns_error_and_rolls_back(tmp_path, monkeypatch):
    """End-to-end through POST /api/chat: 200 error reply, transaction rolled
    back (no constraint persisted)."""
    path = tmp_path / "chat.db"
    conn = db.connect(path)
    db.init_db(conn)
    gid = db.create_goal(conn, title="G1", deadline="2026-12-18T00:00:00Z")
    db.create_material(
        conn, goal_id=gid, topic_id=None, filename="m.md",
        original_path="/x/m.md", markdown_path="/x/m.md",
        content_hash="hash-m", summary="s", tags='["math"]',
        effort_hours=1.0, status="tagged",
    )
    conn.commit()
    conn.close()

    previous = main_mod.DEFAULT_DB_PATH
    main_mod.DEFAULT_DB_PATH = path
    try:
        monkeypatch.setattr(
            main_mod.actions.planner, "replan",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("wedged")),
        )

        def fake_chat(self, *, message, history, context, conn, now_iso):
            result = tools.dispatch(
                conn, "add_constraint",
                {"kind": "subject_gap", "params": {"subject": "math", "gap_minutes": 60}},
                now_iso,
            )
            return {
                "reply": "saved it", "llm": "on",
                "actions": [{"tool": "add_constraint", "args": {}, "result": result}],
            }

        monkeypatch.setattr(main_mod.llm_mod.LLMClient, "chat", fake_chat)
        client = TestClient(main_mod.app)
        try:
            r = client.post("/api/chat", json={"message": "avoid math right after math class"})
            assert r.status_code == 200, r.text
            assert "error" in r.json(), r.text
        finally:
            client.close()
    finally:
        main_mod.DEFAULT_DB_PATH = previous

    check = db.connect(path)
    try:
        assert check.execute("SELECT COUNT(*) AS n FROM constraints").fetchone()["n"] == 0
    finally:
        check.close()
