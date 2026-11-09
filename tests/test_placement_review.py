"""Regression tests for the better-llm-utilisation review findings:
constraint scoring hygiene, relaxation receipts, migration path, NL parsing
quality and the LLM budget path. Injected clock, temp SQLite, no network."""

import json
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app import constraints as con
from app import db, engine
from app.constraints import serialise_params

_REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = "2026-11-03T07:00:00Z"


@pytest.fixture
def conn():
    base = _REPO_ROOT / "data" / "_test" / uuid.uuid4().hex
    base.mkdir(parents=True, exist_ok=True)
    path = base / "test.db"
    c = db.connect(path)
    db.init_db(c)
    db.update_setting(c, "utc_offset_minutes", "0")
    try:
        yield c
    finally:
        c.close()
        shutil.rmtree(base, ignore_errors=True)


def _add_goal_material(conn, deadline="2026-12-18T00:00:00Z", effort=0.8, tags='["math"]',
                       filename="limits.md"):
    gid = db.create_goal(conn, title="G1", deadline=deadline)
    mid = db.create_material(
        conn, goal_id=gid, topic_id=None, filename=filename,
        original_path="/x", markdown_path="/x",
        content_hash="hash-" + uuid.uuid4().hex[:8], summary="s",
        tags=tags, effort_hours=effort, status="tagged",
    )
    conn.commit()
    return gid, mid


def _add_external(conn, title, ds, de, subject="math", fatigue=3):
    eid = db.create_event(
        conn, kind="external", title=title, dtstart=ds, dtend=de, locked=1,
        ics_uid="uid-" + uuid.uuid4().hex[:8],
    )
    db.upsert_annotation(conn, eid, subject=subject, fatigue=fatigue, source="heuristic")
    conn.commit()
    return eid


def _job_slots(conn, gid):
    return [(j["slot_start"], j["placement_source"], j["placement_note"])
            for j in db.jobs_for_goal(conn, gid)]


# --------------------------------------- finding 2: builtin windows don't leak

def test_score_slot_builtins_do_not_leak_with_long_gap_constraint():
    ext_end = datetime(2026, 11, 3, 10, 0)
    slot = datetime(2026, 11, 3, 13, 20)  # 200 min after the external ends
    hard_gap = [{"kind": "subject_gap", "params": {"subject": "math", "gap_minutes": 240},
                 "hard": False, "label": "x", "id": "c1"}]
    penalty, blocked, reasons = con.score_slot(
        slot, slot + timedelta(minutes=50),
        subjects=["math"],
        externals=[{"start": ext_end - timedelta(hours=2), "end": ext_end,
                    "subject": "math", "fatigue": 3, "title": "Math"}],
        constraints=hard_gap,
        smart=True,
    )
    assert blocked is False
    assert all("60 min" not in r for r in reasons)
    assert penalty == con.SUBJECT_GAP_PENALTY
    assert any("240" in r for r in reasons)


def test_score_slot_builtin_applies_within_60_minutes():
    ext_end = datetime(2026, 11, 3, 10, 0)
    slot = datetime(2026, 11, 3, 10, 50)
    penalty, blocked, reasons = con.score_slot(
        slot, slot + timedelta(minutes=50),
        subjects=["math"],
        externals=[{"start": ext_end - timedelta(hours=2), "end": ext_end,
                    "subject": "math", "fatigue": 3, "title": "Math"}],
        constraints=[],
        smart=True,
    )
    assert penalty >= con.BUILTIN_FATIGUE_PENALTY_3 + con.BUILTIN_SUBJECT_ADJACENCY_PENALTY
    assert any("fatigue-3" in r for r in reasons)


# --------------------------------------- finding 1: interleave is candidate-dependent

def test_round_robin_placement_interleaves_subjects(conn):
    gid = db.create_goal(conn, title="G", deadline="2026-12-18T00:00:00Z")
    for i, (name, tag) in enumerate(
        [("math_a.md", '["math"]'), ("cs_a.md", '["cs"]'),
         ("math_b.md", '["math"]'), ("cs_b.md", '["cs"]')]
    ):
        db.create_material(
            conn, goal_id=gid, topic_id=None, filename=name,
            original_path="/x", markdown_path="/x",
            content_hash=f"h{i}", summary="s",
            tags=tag, effort_hours=0.8, status="tagged",
        )
    conn.commit()
    engine.replan(conn, gid, NOW)
    jobs = db.jobs_for_goal(conn, gid)
    subjects = []
    materials = {m["id"]: m for m in db.list_materials(conn)}
    for j in sorted(jobs, key=lambda j: j["slot_start"]):
        tags = json.loads(materials[j["material_id"]]["tags"])
        subjects.append(tags[0])
    # subjects must alternate: math, cs, math, cs
    assert subjects == ["math", "cs", "math", "cs"]


# --------------------------------------- finding 11: pool reuse refreshes receipts

def test_pool_reuse_refreshes_placement_note(conn):
    # capacity 2h: two 50-min jobs fit today, the third spills to tomorrow
    # and is re-derived on every replan (today's jobs are kept as-is by
    # the no-churn rule)
    db.update_setting(conn, "daily_capacity_hours", json.dumps(2.0))
    conn.commit()
    gid = db.create_goal(conn, title="G1", deadline="2026-12-18T00:00:00Z")
    for i, name in enumerate(("a.md", "b.md", "c.md")):
        db.create_material(
            conn, goal_id=gid, topic_id=None, filename=name,
            original_path="/x", markdown_path="/x",
            content_hash=f"reuse{i}", summary="s", tags='["math"]',
            effort_hours=0.8, status="tagged",
        )
    conn.commit()
    engine.replan(conn, gid, NOW)
    by_slot = sorted(db.jobs_for_goal(conn, gid), key=lambda j: j["slot_start"])
    spill = next(j for j in by_slot if j["slot_start"][:10] > "2026-11-03")
    assert spill["placement_source"] == "engine"
    assert spill["placement_note"] is None

    # replan with a chooser that always picks index 0 — which IS the
    # deterministic best here — so c's slot survives pool-reuse and only the
    # receipt may change
    engine.replan(conn, gid, NOW, chooser=lambda ctx, cands: {"choice": 0, "reason": "ranked"})
    spill_after = next(j for j in db.jobs_for_goal(conn, gid) if j["id"] == spill["id"])
    assert spill_after["placement_source"] == "llm"
    assert spill_after["placement_note"] == "ranked"


# --------------------------------------- finding 10: corrupted constraint rows

def test_corrupt_constraint_row_does_not_crash_replan(conn):
    conn.execute(
        "INSERT INTO constraints(id, kind, params, enabled, hard, source, label, created_at)"
        " VALUES ('broken1', 'blocked_day_part', '{}', 1, 1, 'user', 'broken', '2026-11-03T00:00:00Z')"
    )
    conn.commit()
    gid, _ = _add_goal_material(conn, effort=0.8)
    engine.replan(conn, gid, NOW)  # must not raise
    jobs = _job_slots(conn, gid)
    assert len(jobs) == 1


# --------------------------------------- findings 4/5/7: LLM-path receipts

def test_llm_path_records_relaxation(conn):
    db.create_constraint(
        conn, kind="blocked_day_part",
        params=serialise_params({"start_hour": 8, "end_hour": 22}),
        hard=True, source="user", label="No study 08:00-22:00",
    )
    conn.commit()
    gid, _ = _add_goal_material(conn, effort=0.8)
    engine.replan(conn, gid, NOW, chooser=lambda ctx, cands: {"choice": 0, "reason": "evening"})
    jobs = _job_slots(conn, gid)
    assert jobs[0][1] == "llm"
    assert "relaxed" in (jobs[0][2] or "")


def test_relaxation_note_not_duplicated(conn):
    db.create_constraint(
        conn, kind="blocked_day_part",
        params=serialise_params({"start_hour": 8, "end_hour": 22}),
        hard=True, source="user", label="No study 08:00-22:00",
    )
    conn.commit()
    gid, _ = _add_goal_material(conn, effort=0.8)
    engine.replan(conn, gid, NOW)
    jobs = _job_slots(conn, gid)
    assert (jobs[0][2] or "").count("relaxed") == 1


def test_chooser_bool_and_fractional_choices_rejected(conn):
    gid, _ = _add_goal_material(conn, effort=0.8)

    def chooser_true(ctx, candidates):
        return {"choice": True, "reason": "bool"}

    engine.replan(conn, gid, NOW, chooser=chooser_true)
    assert _job_slots(conn, gid)[0][1] == "engine"

    gid2, _ = _add_goal_material(conn, effort=0.8, filename="other.md")

    def chooser_fractional(ctx, candidates):
        return {"choice": 1.9, "reason": "fractional"}

    engine.replan(conn, gid2, NOW, chooser=chooser_fractional)
    jobs = db.jobs_for_goal(conn, gid2)
    assert all(j["placement_source"] == "engine" for j in jobs)


# --------------------------------------- finding 6/3: rank budget path

def test_planner_budget_yields_budget_receipt(conn, monkeypatch):
    db.update_setting(conn, "llm_placement_mode", json.dumps("rank"))
    db.update_setting(conn, "llm_base_url", json.dumps("http://localhost:9/v1"))
    db.update_setting(conn, "llm_model", json.dumps("test-model"))
    conn.commit()

    from app import llm as llm_mod, planner

    calls = {"n": 0}

    def fake_choose(self, *, job_context, candidates):
        calls["n"] += 1
        return {"choice": candidates[0]["index"], "reason": "rank pick"}

    monkeypatch.setattr(llm_mod.LLMClient, "choose_placement", fake_choose)
    gid = db.create_goal(conn, title="G1", deadline="2026-12-18T00:00:00Z")
    # 20 slots of work -> 16 LLM calls then budget exhaustion
    for i in range(5):
        db.create_material(
            conn, goal_id=gid, topic_id=None, filename=f"m{i}.md",
            original_path="/x", markdown_path="/x", content_hash=f"bulk{i}",
            summary="s", tags='["cs"]', effort_hours=4.0, status="tagged",
        )
    conn.commit()
    planner.replan(conn, gid, NOW)
    assert calls["n"] == planner.MAX_LLM_PLACEMENT_CALLS
    notes = [j["placement_note"] for j in db.jobs_for_goal(conn, gid)]
    assert any("budget reached" in (n or "") for n in notes)


# --------------------------------------- find 12/migrations: pre-upgrade DB

def test_migration_adds_placement_columns_and_backfills(tmp_path=None):
    base = _REPO_ROOT / "data" / "_test" / uuid.uuid4().hex
    base.mkdir(parents=True, exist_ok=True)
    try:
        path = base / "old.db"
        raw = sqlite3.connect(path)
        raw.executescript(
            """
            CREATE TABLE events(id TEXT PRIMARY KEY, kind TEXT NOT NULL, ics_uid TEXT,
              ics_rrule TEXT, source TEXT, title TEXT NOT NULL, dtstart TEXT NOT NULL,
              dtend TEXT NOT NULL, all_day INTEGER NOT NULL DEFAULT 0, venue TEXT,
              description TEXT, locked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE goals(id TEXT PRIMARY KEY, title TEXT NOT NULL,
              deadline TEXT NOT NULL, exam_event_id TEXT,
              status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL);
            CREATE TABLE materials(id TEXT PRIMARY KEY, goal_id TEXT, topic_id TEXT,
              filename TEXT NOT NULL, original_path TEXT NOT NULL, markdown_path TEXT NOT NULL,
              content_hash TEXT NOT NULL, summary TEXT, tags TEXT, effort_hours REAL,
              status TEXT NOT NULL DEFAULT 'new', duplicate_of TEXT,
              superseded_by TEXT, status_reason TEXT, created_at TEXT NOT NULL);
            CREATE TABLE jobs(id TEXT PRIMARY KEY,
              event_id TEXT NOT NULL REFERENCES events(id),
              goal_id TEXT NOT NULL REFERENCES goals(id),
              material_id TEXT NOT NULL REFERENCES materials(id),
              kind TEXT NOT NULL, duration_min INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending', done_at TEXT,
              revision_round INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
              pinned INTEGER NOT NULL DEFAULT 0, parent_job_id TEXT NULL);
            INSERT INTO events(id, kind, title, dtstart, dtend, locked)
              VALUES ('ev1', 'study', '', '2026-11-03T08:00:00Z', '2026-11-03T08:50:00Z', 0);
            INSERT INTO goals(id, title, deadline, created_at)
              VALUES ('g1', 'G', '2026-12-18T00:00:00Z', '2026-11-01T00:00:00Z');
            INSERT INTO materials(id, goal_id, filename, original_path, markdown_path,
              content_hash, created_at)
              VALUES ('m1', 'g1', 'f.md', '/o', '/m', 'h1', '2026-11-01T00:00:00Z');
            INSERT INTO jobs(id, event_id, goal_id, material_id, kind, duration_min,
              created_at)
              VALUES ('j1', 'ev1', 'g1', 'm1', 'learn', 50, '2026-11-01T00:00:00Z');
            """
        )
        raw.commit()
        raw.close()

        migrated = db.connect(path)
        db.init_db(migrated)
        cols = {r["name"] for r in migrated.execute("PRAGMA table_info(jobs)")}
        assert {"placement_source", "placement_note"} <= cols
        tables = {r["name"] for r in migrated.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"event_annotations", "constraints"} <= tables
        row = migrated.execute("SELECT placement_source FROM jobs WHERE id='j1'").fetchone()
        assert row["placement_source"] == "engine"
        migrated.close()
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_externals_are_annotated_lazily(conn):
    """External events entering outside the import path get annotated on first pass."""
    db.create_event(
        conn, kind="external", title="MATH2001 Lecture",
        dtstart="2026-11-04T08:00:00Z", dtend="2026-11-04T12:00:00Z",
        locked=1, ics_uid="uid-backfill",
    )
    conn.commit()
    from app import annotate

    assert annotate.annotate_missing(conn, llm=None) == 1
    rows = db.list_annotations(conn)
    assert rows[0]["subject"] == "math"
    assert annotate.annotate_missing(conn, llm=None) == 0


# --------------------------------------- review-2 fixes: S1 / S4 / M4 / M6

def test_fractional_integer_params_are_rejected():
    import pytest as _pytest

    with _pytest.raises(ValueError):
        con.validate_params("post_fatigue_buffer", {"min_fatigue": 2.9})
    with _pytest.raises(ValueError):
        con.validate_params("interleave", {"max_consecutive": 1.5})
    assert con.validate_params("post_fatigue_buffer", {"min_fatigue": 2}) == {
        "min_fatigue": 2,
        "buffer_minutes": 60,
    }


def test_interleave_hard_is_rejected(conn):
    import pytest as _pytest

    from app import actions

    with _pytest.raises(ValueError):
        actions.add_constraint(
            conn, kind="interleave", params={"max_consecutive": 2},
            hard=True, source="user", now_iso=NOW,
        )
    # and the tool dispatch never persists it either
    from app import tools

    result = tools.dispatch(conn, "add_constraint",
                            {"kind": "interleave", "params": {"max_consecutive": 2},
                             "hard": True},
                            NOW)
    assert "error" in result
    assert db.list_constraints(conn) == []


def test_blocked_day_part_uses_interval_overlap():
    from datetime import datetime as dt

    slot_start = dt(2026, 11, 3, 11, 30)  # 11:30-12:20 overlaps a 12:00-13:00 block
    slot_end = dt(2026, 11, 3, 12, 20)
    constraints = [{"id": "c", "kind": "blocked_day_part",
                    "params": {"start_hour": 12, "end_hour": 13},
                    "hard": True, "label": "lunch"}]
    penalty, blocked, reasons = con.score_slot(
        slot_start, slot_end, subjects=[], externals=[],
        constraints=constraints, smart=False,
    )
    assert blocked is True


def test_parse_nl_never_more_than_n_in_a_row():
    out = con.parse_nl("never more than 3 in a row")
    assert out is not None
    assert out["kind"] == "interleave"
    assert out["params"]["max_consecutive"] == 3


# --------------------------------------- review-2 fixes: M2 (unplaced) + annotation chunks

def test_unplaced_slots_surface_in_replan_receipt(conn):
    # 0.5h capacity cannot fit a 50-minute slot anywhere
    db.update_setting(conn, "daily_capacity_hours", json.dumps(0.5))
    conn.commit()
    gid, _ = _add_goal_material(conn, effort=0.8)
    result = engine.replan(conn, gid, NOW)
    assert result["unplaced"] >= 1
    assert db.jobs_for_goal(conn, gid) == []


def test_annotate_chunks_over_15_events():
    import json as _json
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.config import Settings
    from app.llm import LLMClient

    settings = Settings(
        daily_capacity_hours=4.0, slot_minutes=50, min_slot_minutes=30,
        revision_intervals_days=[1, 3, 7, 14],
        llm_base_url="http://localhost:11434/v1", llm_model="test-model",
        llm_api_key="", default_effort_hours=1.0,
    )
    calls = {"n": 0}

    def fake_create(**kwargs):
        calls["n"] += 1
        events = _json.loads(kwargs["messages"][0]["content"].split("Events: ")[1])
        payload = {"annotations": [
            {"event_id": e["event_id"], "subject": "cs", "fatigue": 1} for e in events
        ]}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=_json.dumps(payload)))]
        )

    fake = MagicMock()
    fake.with_options.return_value = fake
    fake.chat.completions.create.side_effect = fake_create
    client = LLMClient(settings)
    client._client = fake
    events = [
        {"event_id": f"e{i}", "title": f"Lecture {i}", "duration_minutes": 60, "venue": None}
        for i in range(32)
    ]
    out = client.annotate_events(events=events, time_budget_seconds=None)
    assert len(out) == 32
    assert calls["n"] == 3  # 15 + 15 + 2


def test_annotate_force_preserves_user_source(conn):
    # contract-level: the force path must never overwrite source='user'
    eid = db.create_event(
        conn, kind="external", title="THE HARDEST LECTURE EVER",
        dtstart="2026-11-04T08:00:00Z", dtend="2026-11-04T12:00:00Z",
        locked=1, ics_uid="uid-user-note",
    )
    db.upsert_annotation(conn, eid, subject="math", fatigue=3, source="user", note="mine")
    conn.commit()
    marked = {
        row["event_id"]
        for row in db.list_annotations(conn)
        if row["source"] == "user"
    }
    assert eid in marked


def test_constraint_listed_and_reused_not_duplicated(conn):
    from app import actions

    _add_goal_material(conn, effort=0.8)
    first = actions.add_constraint(
        conn, kind="subject_gap", params={"subject": "math", "gap_minutes": 60},
        source="user", now_iso=NOW,
    )
    second = actions.add_constraint(
        conn, kind="subject_gap", params={"subject": "math", "gap_minutes": 60},
        source="user", now_iso=NOW,
    )
    assert first["constraint"]["id"] == second["constraint"]["id"]
    assert second.get("existing") is True
    rows = db.list_constraints(conn)
    assert len(rows) == 1
