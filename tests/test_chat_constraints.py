"""Constraint chat-flow tests — LLM tool path (mocked endpoint) and the
offline NL path (no LLM configured)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from app import db
from app.llm import LLMClient


def _settings(llm_enabled=False, **overrides):
    from app.config import Settings

    base = dict(
        daily_capacity_hours=4.0,
        slot_minutes=50,
        min_slot_minutes=30,
        revision_intervals_days=[1, 3, 7, 14],
        llm_base_url="http://localhost:11434/v1" if llm_enabled else "",
        llm_model="test-model" if llm_enabled else "",
        llm_api_key="",
        default_effort_hours=1.0,
    )
    base.update(overrides)
    return Settings(**base)


def _tool_message(content="", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _response(content="", tool_calls=None):
    msg = _tool_message(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _tool_call(name, arguments, call_id="tc-1"):
    fn = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(id=call_id, type="function", function=fn)


def _scripted(*responses):
    fake = MagicMock()
    fake.chat.completions.create.side_effect = list(responses)
    return fake


def _seed_goal_material(conn):
    gid = db.create_goal(conn, title="Limits", deadline="2026-12-18T00:00:00Z")
    mid = db.create_material(
        conn, goal_id=gid, topic_id=None, filename="limits.md",
        original_path="/x/limits.md", markdown_path="/x/limits.md",
        content_hash="hash-limits", summary="Limits summary.",
        tags='["math"]', effort_hours=1.0, status="tagged",
    )
    conn.commit()
    return gid, mid


def test_chat_tool_add_constraint_llm(conn):
    _seed_goal_material(conn)
    fake = _scripted(
        _response(
            tool_calls=[
                _tool_call(
                    "add_constraint",
                    '{"kind": "subject_gap", "params": {"subject": "math", '
                    '"gap_minutes": 90}, "label": "No math right after math"}',
                )
            ]
        ),
        _response("Saved: no math within 90 min of math events."),
    )
    c = LLMClient(_settings(llm_enabled=True))
    c._client = fake
    out = c.chat(
        message="avoid math right after math class",
        history=[],
        context={},
        conn=conn,
        now_iso="2026-11-07T12:00:00Z",
    )
    assert out["llm"] == "on"
    constraint = conn.execute("SELECT * FROM constraints").fetchone()
    assert constraint is not None
    assert constraint["kind"] == "subject_gap"
    assert constraint["source"] == "llm"
    assert constraint["label"] == "No math right after math"
    assert out["actions"][0]["tool"] == "add_constraint"
    # the constraint writes through a replan, so the goal now has jobs
    meta = conn.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE kind = 'learn'"
    ).fetchone()
    assert meta["n"] >= 1


def test_chat_tool_list_and_remove_constraint(conn):
    _seed_goal_material(conn)
    from app.constraints import serialise_params

    cid = db.create_constraint(
        conn, kind="interleave",
        params=serialise_params({"max_consecutive": 2}),
        hard=False, source="user", label="Max 2 in a row",
    )
    fake = _scripted(
        _response(tool_calls=[_tool_call("list_constraints", "{}")]),
        _response(tool_calls=[_tool_call("remove_constraint", f'{{"constraint_id": "{cid}"}}')]),
        _response("Removed that rule."),
    )
    c = LLMClient(_settings(llm_enabled=True))
    c._client = fake
    out = c.chat(
        message="drop the interleave rule",
        history=[],
        context={},
        conn=conn,
        now_iso="2026-11-07T12:00:00Z",
    )
    assert out["llm"] == "on"
    assert conn.execute("SELECT COUNT(*) AS n FROM constraints").fetchone()["n"] == 0
    assert [a["tool"] for a in out["actions"]] == ["list_constraints", "remove_constraint"]


def test_offline_unknown_subject_creates_no_rule(conn):
    """A constraint that can never fire must not be saved; user gets guidance."""
    _seed_goal_material(conn)  # tags: math
    c = LLMClient(_settings(llm_enabled=False))
    out = c.chat(
        message="avoid quantum mechanics right after quantum mechanics class",
        history=[],
        context={},
        conn=conn,
        now_iso="2026-11-07T12:00:00Z",
    )
    assert out["llm"] == "off"
    assert "no material/event" in out["reply"].lower() or "known subjects" in out["reply"].lower()
    assert conn.execute("SELECT COUNT(*) AS n FROM constraints").fetchone()["n"] == 0


def test_offline_nl_constraint_creation(conn):
    """No LLM configured: the NL parser compiles the rule deterministically."""
    _seed_goal_material(conn)
    c = LLMClient(_settings(llm_enabled=False))
    out = c.chat(
        message="avoid math right after math class",
        history=[],
        context={},
        conn=conn,
        now_iso="2026-11-07T12:00:00Z",
    )
    assert out["llm"] == "off"
    assert "saved the rule" in out["reply"].lower()
    constraint = conn.execute("SELECT * FROM constraints").fetchone()
    assert constraint["kind"] == "subject_gap"
    assert constraint["source"] == "user"


def test_offline_unrelated_message_still_works(conn):
    c = LLMClient(_settings(llm_enabled=False))
    out = c.chat(message="hello there", history=[], context={}, conn=conn)
    assert out["llm"] == "off"
    assert out["reply"]  # non-empty, no exception


def test_add_constraint_invalid_kind_returns_error_receipt(conn):
    _seed_goal_material(conn)
    from app import tools

    result = tools.dispatch(
        conn,
        "add_constraint",
        {"kind": "yolo", "params": {}},
        "2026-11-07T12:00:00Z",
    )
    assert "error" in result


def test_add_constraint_bad_param_returns_error_receipt(conn):
    _seed_goal_material(conn)
    from app import tools

    result = tools.dispatch(
        conn,
        "add_constraint",
        {"kind": "subject_gap", "params": {"subject": "math", "gap_minutes": 9999}},
        "2026-11-07T12:00:00Z",
    )
    assert "error" in result
    assert conn.execute("SELECT COUNT(*) AS n FROM constraints").fetchone()["n"] == 0
