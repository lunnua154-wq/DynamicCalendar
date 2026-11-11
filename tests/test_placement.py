"""Engine placement tests — smart scoring, hard-constraint relaxation, chooser
injection and annotation heuristics. Injected clock, temp SQLite, no network."""

import json
import shutil
import uuid
from pathlib import Path

import pytest

from app import annotate, db, engine
from app.constraints import serialise_params

_REPO_ROOT = Path(__file__).resolve().parent.parent


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


def _add_goal_material(conn, deadline="2026-12-18T00:00:00Z", effort=0.8, tags='["math"]'):
    gid = db.create_goal(conn, title="G1", deadline=deadline)
    mid = db.create_material(
        conn, goal_id=gid, topic_id=None, filename="limits.md",
        original_path="/x/limits.md", markdown_path="/x/limits.md",
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
    db.upsert_annotation(
        conn, eid, subject=subject, fatigue=fatigue, source="heuristic"
    )
    conn.commit()
    return eid


def _job_slots(conn, gid):
    return [(j["slot_start"], j["slot_end"], j["placement_source"], j["placement_note"])
            for j in db.jobs_for_goal(conn, gid)]


NOW = "2026-11-03T07:00:00Z"  # Tue 03 Nov 07:00 UTC


# ------------------------------------------------------------ smart placement

def test_smart_placement_avoids_right_after_tiring_lecture(conn):
    # 08:00-12:00 very tiring lecture blocks the early window
    _add_external(conn, "MATH2001 Lecture", "2026-11-03T08:00:00Z", "2026-11-03T12:00:00Z",
                  subject="math", fatigue=3)
    gid, _ = _add_goal_material(conn, effort=0.8)
    engine.replan(conn, gid, NOW)
    jobs = _job_slots(conn, gid)
    assert len(jobs) == 1
    slot_start = jobs[0][0]
    # 12:10 and 13:00 would both sit within 60 min of a fatigue-3 event
    # (penalised); the planner jumps to the first zero-penalty slot instead
    assert slot_start >= "2026-11-03T13:00:00Z"


def test_smart_placement_off_restores_earliest_free_slot(conn):
    _add_external(conn, "MATH2001 Lecture", "2026-11-03T08:00:00Z", "2026-11-03T12:00:00Z",
                  subject="math", fatigue=3)
    db.update_setting(conn, "smart_placement", json.dumps("off"))
    conn.commit()
    gid, _ = _add_goal_material(conn, effort=0.8)
    engine.replan(conn, gid, NOW)
    jobs = _job_slots(conn, gid)
    # study grid starts at 08:00 on a 50-minute lattice: first free slot after
    # the 12:00 event end is 12:10
    assert jobs[0][0] == "2026-11-03T12:10:00Z"


def test_subject_gap_constraint_pushes_subject_work(conn):
    # Math class 08:30-10:00 (annotated), material tagged math
    _add_external(conn, "MATH2001 Lecture", "2026-11-03T08:30:00Z", "2026-11-03T10:00:00Z",
                  subject="math", fatigue=1)
    db.create_constraint(
        conn, kind="subject_gap",
        params=serialise_params({"subject": "math", "gap_minutes": 60}),
        hard=False, source="user", label="No math within 60 min of math events",
    )
    conn.commit()
    gid, _ = _add_goal_material(conn, effort=0.8, tags='["math"]')
    engine.replan(conn, gid, NOW)
    jobs = _job_slots(conn, gid)
    assert jobs[0][0] >= "2026-11-03T11:00:00Z"


def test_subject_gap_hard_constraint_excludes_window(conn):
    _add_external(conn, "MATH2001 Lecture", "2026-11-03T08:30:00Z", "2026-11-03T10:00:00Z",
                  subject="math", fatigue=1)
    db.create_constraint(
        conn, kind="subject_gap",
        params=serialise_params({"subject": "math", "gap_minutes": 240}),
        hard=True, source="user", label="No math within 4h of math events",
    )
    conn.commit()
    gid, _ = _add_goal_material(conn, effort=0.8, tags='["math"]')
    engine.replan(conn, gid, NOW)
    jobs = _job_slots(conn, gid)
    assert jobs[0][0] >= "2026-11-03T14:00:00Z"


def test_smart_placement_avoids_back_to_back_same_subject(conn):
    """Two math materials: the second must not start where the first ends."""
    gid = db.create_goal(conn, title="G1", deadline="2026-12-18T00:00:00Z")
    for i, name in enumerate(("a.md", "b.md")):
        db.create_material(
            conn, goal_id=gid, topic_id=None, filename=name,
            original_path="/x", markdown_path="/x", content_hash=f"adj{i}",
            summary="s", tags='["math"]', effort_hours=0.8, status="tagged",
        )
    conn.commit()
    engine.replan(conn, gid, NOW)
    jobs = sorted(db.jobs_for_goal(conn, gid), key=lambda j: j["slot_start"])
    assert len(jobs) == 2
    first_end = jobs[0]["slot_end"]
    assert jobs[1]["slot_start"] != first_end  # not touching: a free slot stays between them


def test_interleave_constraint_prefers_other_subject(conn):
    # deadline = today forces the two touching slots to be used (no tomorrow
    # escape hatch), so the interleave penalty fires and is recorded
    _add_external(conn, "Team Meeting", "2026-11-03T09:40:00Z", "2026-11-03T22:00:00Z",
                  subject="cs", fatigue=0)
    db.create_constraint(
        conn, kind="interleave",
        params=serialise_params({"max_consecutive": 1}),
        hard=False, source="user", label="max 1 consecutive same-subject job",
    )
    conn.commit()
    gid = db.create_goal(conn, title="G1", deadline="2026-11-03T23:59:59Z")
    # two math materials -> two jobs to place
    for i, name in enumerate(("a.md", "b.md")):
        db.create_material(
            conn, goal_id=gid, topic_id=None, filename=name,
            original_path="/x", markdown_path="/x", content_hash=f"h{i}",
            summary="s", tags='["math"]', effort_hours=0.8, status="tagged",
        )
    conn.commit()
    engine.replan(conn, gid, NOW)
    jobs = sorted(db.jobs_for_goal(conn, gid), key=lambda j: j["slot_start"])
    assert len(jobs) == 2
    # the unavoidably back-to-back placement accrues the interleave penalty
    assert any(j["placement_note"] and "consecutive" in j["placement_note"] for j in jobs)


def test_hard_constraints_relax_instead_of_dropping_jobs(conn):
    db.create_constraint(
        conn, kind="blocked_day_part",
        params=serialise_params({"start_hour": 8, "end_hour": 22}),
        hard=True, source="user", label="No study 08:00-22:00",
    )
    conn.commit()
    gid, _ = _add_goal_material(conn, effort=0.8)
    engine.replan(conn, gid, NOW)
    jobs = _job_slots(conn, gid)
    assert len(jobs) == 1  # job was NOT dropped
    assert jobs[0][3] and "relaxed" in jobs[0][3].lower()


# --------------------------------------------------------------- annotations

def test_annotate_missing_all_day_event_has_no_fatigue(conn):
    db.create_event(
        conn, kind="external", title="Library books due",
        dtstart="2026-12-15T00:00:00Z", dtend="2026-12-16T00:00:00Z",
        locked=1, ics_uid="uid-ann-allday", all_day=1,
    )
    conn.commit()
    annotate.annotate_missing(conn, llm=None)
    rows = db.list_annotations(conn)
    assert rows[0]["fatigue"] == 0


def test_annotate_missing_recognises_course_codes(conn):
    db.create_event(
        conn, kind="external", title="CS1010 Lecture — Recursion",
        dtstart="2026-11-04T02:00:00Z", dtend="2026-11-04T04:00:00Z",
        locked=1, ics_uid="uid-ann-cs",
    )
    conn.commit()
    annotate.annotate_missing(conn, llm=None)
    rows = db.list_annotations(conn)
    assert rows[0]["subject"] == "cs"


def test_annotate_budget_zero_skips_llm_entirely(conn):
    db.create_event(
        conn, kind="external", title="MATH2001 Lecture",
        dtstart="2026-11-03T08:00:00Z", dtend="2026-11-03T12:00:00Z",
        locked=1, ics_uid="uid-budget",
    )
    conn.commit()

    class ExplodingLLM:
        def annotate_events(self, *, events, time_budget_seconds=45.0):
            raise AssertionError("LLM must not be called with budget=0")

    n = annotate.annotate_missing(conn, llm=ExplodingLLM(), time_budget_seconds=0.0)
    assert n == 1
    assert db.list_annotations(conn)[0]["source"] == "heuristic"


def test_annotate_missing_heuristic_scores_long_lecture(conn):
    db.create_event(
        conn, kind="external", title="MATH2001 Lecture",
        dtstart="2026-11-03T08:00:00Z", dtend="2026-11-03T12:00:00Z",
        locked=1, ics_uid="uid-ann-1",
    )
    conn.commit()
    n = annotate.annotate_missing(conn, llm=None)
    assert n == 1
    rows = db.list_annotations(conn)
    assert rows[0]["fatigue"] == 3
    assert rows[0]["subject"] == "math"
    assert rows[0]["source"] == "heuristic"
    # second pass is a no-op
    assert annotate.annotate_missing(conn, llm=None) == 0


def test_annotate_missing_llm_overrides_and_fallback_works(conn):
    db.create_event(
        conn, kind="external", title="MATH2001 Problem Session",
        dtstart="2026-11-03T08:00:00Z", dtend="2026-11-03T11:00:00Z",
        locked=1, ics_uid="uid-ann-llm",
    )
    conn.commit()

    class FakeLLM:
        def annotate_events(self, *, events, time_budget_seconds=45.0):
            return [
                {"event_id": events[0]["event_id"], "subject": "math",
                 "fatigue": 3, "note": "very demanding"}
            ]

    assert annotate.annotate_missing(conn, llm=FakeLLM()) == 1
    rows = db.list_annotations(conn)
    assert rows[0]["source"] == "llm"
    assert rows[0]["fatigue"] == 3

    class BrokenLLM:
        def annotate_events(self, *, events, time_budget_seconds=45.0):
            raise RuntimeError("endpoint down")

    conn.execute("DELETE FROM event_annotations")
    conn.commit()
    assert annotate.annotate_missing(conn, llm=BrokenLLM()) == 1
    rows = db.list_annotations(conn)
    assert rows[0]["source"] == "heuristic"


# ------------------------------------------------------------- chooser injection

def _chooser_pick_last(job_context, candidates):
    return {"choice": candidates[-1]["index"], "reason": "late evening suits me"}


def test_chooser_llm_pick_is_respected(conn):
    gid, _ = _add_goal_material(conn, effort=0.8)
    result = engine.replan(conn, gid, NOW, chooser=_chooser_pick_last)
    jobs = _job_slots(conn, gid)
    assert jobs[0][2] == "llm"
    assert jobs[0][3] == "late evening suits me"


def test_chooser_out_of_range_falls_back_to_deterministic(conn):
    gid, _ = _add_goal_material(conn, effort=0.8)

    def bad_chooser(job_context, candidates):
        return {"choice": 99, "reason": "nope"}

    engine.replan(conn, gid, NOW, chooser=bad_chooser)
    jobs = _job_slots(conn, gid)
    assert jobs[0][2] == "engine"
    assert jobs[0][3] and "deterministic pick (LLM unavailable or budget reached)" in jobs[0][3]


def test_chooser_exception_falls_back_to_deterministic(conn):
    gid, _ = _add_goal_material(conn, effort=0.8)

    def exploding_chooser(job_context, candidates):
        raise RuntimeError("model exploded")

    engine.replan(conn, gid, NOW, chooser=exploding_chooser)
    jobs = _job_slots(conn, gid)
    assert jobs[0][2] == "engine"
    assert jobs[0][3] and "deterministic pick (LLM unavailable or budget reached)" in jobs[0][3]


def test_placement_note_records_penalty_reasons(conn):
    _add_external(conn, "MATH2001 Lecture", "2026-11-03T08:00:00Z", "2026-11-03T12:00:00Z",
                  subject="math", fatigue=3)
    gid, _ = _add_goal_material(conn, effort=0.8)
    engine.replan(conn, gid, NOW)
    jobs = _job_slots(conn, gid)
    assert jobs[0][2] == "engine"
    # job placed later than earliest (13:40) so a penalty or a jump is recorded
    if jobs[0][3]:
        assert "penalty" in jobs[0][3]


# --------------------------------------------------- rank mode (planner wiring)

def _enable_rank_mode(conn):
    db.update_setting(conn, "llm_placement_mode", json.dumps("rank"))
    db.update_setting(conn, "llm_base_url", json.dumps("http://localhost:9/v1"))
    db.update_setting(conn, "llm_model", json.dumps("test-model"))
    conn.commit()


def test_rank_mode_uses_llm_choice(conn, monkeypatch):
    from app import llm as llm_mod, planner

    _enable_rank_mode(conn)
    gid, _ = _add_goal_material(conn, effort=0.8)

    def fake_choose(self, *, job_context, candidates, timeout=None):
        return {"choice": candidates[-1]["index"], "reason": "rank pick"}

    monkeypatch.setattr(llm_mod.LLMClient, "choose_placement", fake_choose)
    planner.replan(conn, gid, NOW)
    jobs = _job_slots(conn, gid)
    assert jobs[0][2] == "llm"
    assert jobs[0][3] == "rank pick"


def test_rank_mode_llm_failure_falls_back(conn, monkeypatch):
    from app import llm as llm_mod, planner

    _enable_rank_mode(conn)
    gid, _ = _add_goal_material(conn, effort=0.8)

    def exploding_choose(self, *, job_context, candidates, timeout=None):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(llm_mod.LLMClient, "choose_placement", exploding_choose)
    planner.replan(conn, gid, NOW)
    jobs = _job_slots(conn, gid)
    assert jobs[0][2] == "engine"
    assert jobs[0][3] and "deterministic pick (LLM unavailable or budget reached)" in jobs[0][3]
