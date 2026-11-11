"""LLM skill tests — mocked LLM happy path, garbage output -> fallback,
and unconfigured client -> offline fallback (no network)."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import db
from app.config import Settings
from app.llm import LLMClient, extract_json


def _settings(llm_enabled=False, **overrides):
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


def test_local_endpoint_without_api_key_still_builds_client():
    """llama.cpp/Ollama need no key; the SDK still requires a non-empty one."""
    c = LLMClient(_settings(llm_enabled=True, llm_api_key=""))
    assert c.client is not None


def test_client_requests_are_bounded():
    """Regression: SDK defaults retried twice, so an unreachable endpoint made
    chat wait ~15 s before the offline fallback. Requests stay bounded."""
    from app import llm as llm_module

    c = LLMClient(_settings(llm_enabled=True))
    client = c.client
    assert client is not None
    assert client.max_retries == llm_module.LLM_MAX_RETRIES == 0
    assert client.timeout == llm_module.LLM_REQUEST_TIMEOUT_SECONDS
    assert llm_module.LLM_REQUEST_TIMEOUT_SECONDS >= 30  # local models are slow
    # A dead endpoint must not be retried without tools (same failure).
    assert {"connection", "timeout", "auth"} <= llm_module.UNRECOVERABLE_WITHOUT_TOOLS


# --------------------------------------------------------------------------- extract_json


def test_extract_json_strips_fences():
    text = "```json\n{\"a\": 1, \"b\": [1, 2]}\n```"
    assert extract_json(text) == {"a": 1, "b": [1, 2]}


def test_extract_json_first_object_only():
    text = "Here is the result: {\"ok\": true} and then {\"other\": 1}"
    assert extract_json(text) == {"ok": True}


def test_extract_json_garbage_raises():
    with pytest.raises(ValueError):
        extract_json("not json at all {{{")


# --------------------------------------------------------------------------- tag


def test_tag_offline_fallback():
    c = LLMClient(_settings(llm_enabled=False))
    out = c.tag(markdown="# Limits chapter", filename="limits.pdf")
    assert out["tags"] == ["limits"]
    assert out["effort_hours"] == 0.5
    assert "limits.pdf" in out["summary"]


def test_tag_mocked_happy_path():
    fake = MagicMock()
    fake.chat.completions.create.return_value.choices[
        0
    ].message.content = '{"summary":"Limits","tags":["math"],"effort_hours":3.0}'
    c = LLMClient(_settings(llm_enabled=True))
    c._client = fake
    markdown = "lorem ipsum dolor sit amet " * 360  # 1800 words -> baseline 3.0h, band [1.5, 4.5]
    out = c.tag(markdown=markdown, filename="limits.pdf")
    assert out["summary"] == "Limits"
    assert out["tags"] == ["math"]
    assert out["effort_hours"] == 3.0


def test_tag_fenced_json_parses():
    fake = MagicMock()
    fake.chat.completions.create.return_value.choices[
        0
    ].message.content = (
        "```json\n{\"summary\": \"Limits\", \"tags\": [\"math\"], "
        "\"effort_hours\": 3.0}\n```"
    )
    c = LLMClient(_settings(llm_enabled=True))
    c._client = fake
    markdown = "lorem ipsum dolor sit amet " * 360  # 1800 words -> baseline 3.0h, band [1.5, 4.5]
    out = c.tag(markdown=markdown, filename="limits.pdf")
    assert out["summary"] == "Limits"
    assert out["tags"] == ["math"]
    assert out["effort_hours"] == 3.0


def test_tag_garbage_output_falls_back():
    fake = MagicMock()
    fake.chat.completions.create.return_value.choices[
        0
    ].message.content = "not json at all {{{"
    c = LLMClient(_settings(llm_enabled=True))
    c._client = fake
    out = c.tag(markdown="# Limits", filename="limits.pdf")
    assert out["tags"] == ["limits"]
    assert out["effort_hours"] == 0.5


def test_tag_llm_estimate_clamped_to_band():
    fake = MagicMock()
    fake.chat.completions.create.return_value.choices[
        0
    ].message.content = '{"summary":"S","tags":["t"],"effort_hours":18.0}'
    c = LLMClient(_settings(llm_enabled=True))
    c._client = fake
    markdown = "word " * 600  # 600 words -> baseline 1.0h, band [0.5, 1.5]
    out = c.tag(markdown=markdown, filename="x.md")
    assert out["effort_hours"] == 1.5


def test_tag_llm_missing_effort_uses_baseline():
    fake = MagicMock()
    fake.chat.completions.create.return_value.choices[
        0
    ].message.content = '{"summary":"S","tags":["t"]}'
    c = LLMClient(_settings(llm_enabled=True))
    c._client = fake
    markdown = "word " * 600  # 600 words -> baseline 1.0h
    out = c.tag(markdown=markdown, filename="x.md")
    assert out["effort_hours"] == 1.0


def test_tag_empty_markdown_uses_default_effort():
    c = LLMClient(_settings(llm_enabled=False))
    out = c.tag(markdown="", filename="empty.md")
    assert out["effort_hours"] == 1.0


# --------------------------------------------------------------------------- cluster


def test_cluster_offline_new_topic():
    c = LLMClient(_settings(llm_enabled=False))
    out = c.cluster(
        content_hash="abc",
        filename="new.md",
        existing_topics=[],
        existing_material_hashes=[],
    )
    assert out["relation"] == "new"
    assert out["topic_id"] is None


def test_cluster_offline_duplicate_by_hash():
    c = LLMClient(_settings(llm_enabled=False))
    out = c.cluster(
        content_hash="dup-hash",
        filename="copy.md",
        existing_topics=[],
        existing_material_hashes=["dup-hash"],
    )
    assert out["relation"] == "duplicate"


def test_cluster_accepts_extra_kwargs():
    c = LLMClient(_settings(llm_enabled=False))
    out = c.cluster(
        content_hash="abc",
        filename="new.md",
        existing_topics=[],
        existing_material_hashes=[],
        markdown="# Limits\ncontent here",
        topic_name="Limits",
        effort_hours=2.0,
    )
    assert out["relation"] == "new"
    assert out["topic_id"] is None


def test_cluster_fenced_json_parses_and_validates_topic_id():
    existing = [{"id": "topic-1", "name": "Limits"}]
    fake = MagicMock()
    fake.chat.completions.create.return_value.choices[
        0
    ].message.content = (
        "```json\n{\"topic_id\": \"topic-1\", \"relation\": \"version\", "
        "\"reason\": \"update\"}\n```"
    )
    c = LLMClient(_settings(llm_enabled=True))
    c._client = fake
    out = c.cluster(
        content_hash="new-hash",
        filename="v2.md",
        existing_topics=existing,
        existing_material_hashes=[],
        markdown="# Limits v2",
        effort_hours=2.0,
    )
    assert out["relation"] == "version"
    assert out["topic_id"] == "topic-1"


def test_cluster_unknown_topic_id_becomes_none():
    existing = [{"id": "topic-1", "name": "Limits"}]
    fake = MagicMock()
    fake.chat.completions.create.return_value.choices[
        0
    ].message.content = (
        '{"topic_id": "no-such-topic", "relation": "new", "reason": "x"}'
    )
    c = LLMClient(_settings(llm_enabled=True))
    c._client = fake
    out = c.cluster(
        content_hash="new-hash",
        filename="v2.md",
        existing_topics=existing,
        existing_material_hashes=[],
    )
    assert out["topic_id"] is None


# --------------------------------------------------------------------------- chat


def test_chat_offline_fallback():
    c = LLMClient(_settings(llm_enabled=False))
    out = c.chat(message="what's due?", history=[])
    assert out["llm"] == "off"
    # Offline fallback is genuinely useful, never just "LLM not configured".
    assert "LLM not configured" not in out["reply"] or "upcoming" in out["reply"]
    assert len(out["reply"]) > 0


def test_chat_offline_answers_schedule_with_calendar_data():
    c = LLMClient(_settings(llm_enabled=False))
    context = {
        "now": "2026-11-03T12:00:00Z",
        "goals": [
            {
                "id": "g1",
                "title": "Limits",
                "deadline": "2026-12-18T00:00:00Z",
                "progress": 0.0,
            }
        ],
        "jobs": [
            {
                "id": "j1",
                "status": "pending",
                "kind": "learn",
                "slot_start": "2026-11-09T14:00:00Z",
                "slot_end": "2026-11-09T14:50:00Z",
                "material_title": "Limits chapter",
            }
        ],
        "events": [],
        "materials": [],
    }
    out = c.chat(message="what's due this week?", history=[], context=context)
    assert out["llm"] == "off"
    assert "14:00" in out["reply"]
    assert "Limits chapter" in out["reply"]
    assert "learn" in out["reply"]


def test_chat_offline_summarizes_material():
    c = LLMClient(_settings(llm_enabled=False))
    context = {
        "materials": [
            {
                "id": "m1",
                "filename": "limits.md",
                "summary": "Limits and continuity summary.",
                "tags": [],
                "topic_id": None,
                "status": "ready",
                "effort_hours": 2.0,
            }
        ],
        "goals": [],
        "jobs": [],
        "events": [],
    }
    out = c.chat(message="summarize limits.md", history=[], context=context)
    assert out["llm"] == "off"
    assert out["reply"] == "Limits and continuity summary."


def test_chat_mocked_happy_path():
    fake = MagicMock()
    fake.chat.completions.create.return_value.choices[
        0
    ].message.content = "Two goals are open."
    c = LLMClient(_settings(llm_enabled=True))
    c._client = fake
    out = c.chat(message="hi", history=[])
    assert out["llm"] == "on"
    assert out["reply"] == "Two goals are open."


def test_chat_passes_context_to_llm():
    fake = MagicMock()
    fake.chat.completions.create.return_value.choices[
        0
    ].message.content = "ok"
    c = LLMClient(_settings(llm_enabled=True))
    c._client = fake
    context = {"goals": [], "jobs": [], "events": [], "materials": []}
    c.chat(message="hi", history=[], context=context)
    call_kwargs = fake.chat.completions.create.call_args.kwargs
    system_msg = call_kwargs["messages"][0]["content"]
    assert "Calendar context" in system_msg


# --------------------------------------------------------------------------- tune_settings


def test_tune_settings_offline_noop():
    c = LLMClient(_settings(llm_enabled=False))
    out = c.tune_settings(context="busy week")
    assert out == {}  # offline: settings keep current values


def test_tune_settings_fenced_json_parses():
    fake = MagicMock()
    fake.chat.completions.create.return_value.choices[
        0
    ].message.content = (
        "```json\n{\"daily_capacity_hours\": 6.0, \"slot_minutes\": 30}\n```"
    )
    c = LLMClient(_settings(llm_enabled=True))
    c._client = fake
    out = c.tune_settings(context="busy week")
    assert out == {"daily_capacity_hours": 6.0, "slot_minutes": 30}


def test_tune_settings_out_of_bounds_falls_back():
    fake = MagicMock()
    fake.chat.completions.create.return_value.choices[
        0
    ].message.content = '{"daily_capacity_hours": 999, "slot_minutes": 30}'
    c = LLMClient(_settings(llm_enabled=True))
    c._client = fake
    out = c.tune_settings(context="busy week")
    assert out == {}  # validation error -> fallback (no exception)


def test_extract_json_braces_inside_string_literals():
    text = 'prefix {"reason": "a } b { c", "topic_id": null} trailing'
    assert extract_json(text) == {"reason": "a } b { c", "topic_id": None}


def test_extract_json_non_object_raises():
    with pytest.raises(ValueError):
        extract_json("[1, 2, 3]")


# --------------------------------------------------------------------------- chat tools


def _tool_message(content="", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _response(content="", tool_calls=None):
    """Wrap a message like the OpenAI SDK does: response.choices[0].message."""
    msg = _tool_message(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _tool_call(name, arguments, call_id="tc-1"):
    fn = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(id=call_id, type="function", function=fn)


def _scripted(*responses):
    fake = MagicMock()
    fake.chat.completions.create.side_effect = list(responses)
    return fake


def _seed_chat_db(conn):
    # Relative to today: the list_jobs chat tool defaults to "now .. +14 days",
    # so a hard-coded slot drops out of the window as the calendar moves on.
    slot = datetime.now(timezone.utc) + timedelta(days=1)
    slot = slot.replace(hour=14, minute=0, second=0, microsecond=0)
    deadline = (slot + timedelta(days=44)).strftime("%Y-%m-%dT00:00:00Z")
    gid = db.create_goal(conn, title="Limits", deadline=deadline)
    mid = db.create_material(
        conn,
        goal_id=gid,
        topic_id=None,
        filename="limits.md",
        original_path="/x/limits.md",
        markdown_path="/x/limits.md",
        content_hash="hash-limits",
        summary="Limits chapter summary.",
        tags="[]",
        effort_hours=1.0,
        status="ready",
    )
    eid = db.create_event(
        conn,
        kind="study",
        title="Limits session",
        dtstart=slot.strftime("%Y-%m-%dT%H:%M:%SZ"),
        dtend=(slot + timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        locked=0,
    )
    db.create_job(
        conn,
        event_id=eid,
        goal_id=gid,
        material_id=mid,
        kind="learn",
        duration_min=50,
        status="pending",
    )
    conn.commit()
    return gid, mid


def test_chat_tool_update_settings(conn):
    fake = _scripted(
        _response(
            tool_calls=[_tool_call("update_settings", '{"daily_capacity_hours": 6}')]
        ),
        _response("Done — capacity is now 6h."),
    )
    c = LLMClient(_settings(llm_enabled=True))
    c._client = fake
    out = c.chat(
        message="set my daily capacity to 6 hours",
        history=[],
        context={},
        conn=conn,
        now_iso="2026-11-07T12:00:00Z",
    )
    assert out["llm"] == "on"
    assert out["reply"] == "Done — capacity is now 6h."
    assert out["actions"][0]["tool"] == "update_settings"
    assert out["actions"][0]["args"] == {"daily_capacity_hours": 6}
    row = conn.execute(
        "SELECT value, updated_by FROM settings WHERE key = 'daily_capacity_hours'"
    ).fetchone()
    assert row["updated_by"] == "llm"
    assert json.loads(row["value"]) == 6.0


def test_chat_tool_list_jobs(conn):
    _seed_chat_db(conn)
    fake = _scripted(
        _response(tool_calls=[_tool_call("list_jobs", "{}")]),
        _response(
            "You have one job coming up: Limits chapter on Mon 09 Nov 14:00."
        ),
    )
    c = LLMClient(_settings(llm_enabled=True))
    c._client = fake
    out = c.chat(
        message="what's on my schedule?",
        history=[],
        context={},
        conn=conn,
        now_iso="2026-11-07T12:00:00Z",
    )
    assert out["llm"] == "on"
    assert "Limits chapter" in out["reply"]
    assert "14:00" in out["reply"]
    assert out["actions"][0]["tool"] == "list_jobs"
    assert len(out["actions"][0]["result"]["jobs"]) == 1


def test_chat_tool_bad_args_recover(conn):
    _seed_chat_db(conn)
    fake = _scripted(
        _response(
            tool_calls=[_tool_call("update_settings", '{"daily_capacity_hours": 99}')]
        ),
        _response("Capacity must stay between 0.5 and 12 hours."),
    )
    c = LLMClient(_settings(llm_enabled=True))
    c._client = fake
    out = c.chat(
        message="set capacity to 99 hours",
        history=[],
        context={},
        conn=conn,
        now_iso="2026-11-07T12:00:00Z",
    )
    assert out["llm"] == "on"
    assert out["reply"] == "Capacity must stay between 0.5 and 12 hours."
    assert len(out["actions"]) == 1
    assert out["actions"][0]["tool"] == "update_settings"
    assert "error" in out["actions"][0]["result"]
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'daily_capacity_hours'"
    ).fetchone()
    assert json.loads(row["value"]) == 4.0  # unchanged


def test_chat_tool_loop_cap(conn):
    fake = _scripted(
        *[_response(tool_calls=[_tool_call("list_goals", "{}")]) for _ in range(5)]
    )
    c = LLMClient(_settings(llm_enabled=True))
    c._client = fake
    out = c.chat(
        message="hi",
        history=[],
        context={},
        conn=conn,
        now_iso="2026-11-07T12:00:00Z",
    )
    assert out["llm"] == "off"
    assert len(out["reply"]) > 0
    assert [a["tool"] for a in out["actions"]] == ["list_goals"] * 5


def test_chat_tools_offline_untouched():
    c = LLMClient(_settings(llm_enabled=False))
    assert c.client is None  # OpenAI client never built
    out = c.chat(message="what's due?", history=[], context={})
    assert out["llm"] == "off"
    assert "actions" not in out


# ---------------------------------------------------------------------------
# diagnostics — regression test for the with_options SDK-shape bug


def test_check_connection_uses_client_level_with_options():
    """Regression: openai>=3 SDKs have ``with_options`` on the *client*, not on
    ``chat.completions`` (removed in 3.x). A fake that mimics the real SDK
    shape — with ``with_options`` only on the client — is what catches this."""

    class _FakeCompletions:
        def __init__(self):
            self.calls = []

        def create(self, model, messages, max_tokens, temperature):
            self.calls.append((model, messages, max_tokens, temperature))
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="pong"))]
            )

    class _FakeChat:
        def __init__(self):
            self.completions = _FakeCompletions()

    class _FakeClient:
        def __init__(self):
            self.chat = _FakeChat()
            self.options = None

        def with_options(self, **kwargs):
            self.options = kwargs
            return self

    client = _FakeClient()
    c = LLMClient(_settings(llm_enabled=True))
    c._client = client
    out = c.check_connection(timeout=5.0)
    assert out["ok"] is True
    assert out["model"] == "test-model"
    assert client.options == {"max_retries": 0, "timeout": 5.0}
    assert len(client.chat.completions.calls) == 1


def test_check_connection_error_reported_not_raised():
    # A client whose with_options chain blows up must surface as ok=False,
    # never as a raised exception (the settings panel renders the answer).
    class _BrokenCompletions:
        def create(self, **_):
            raise RuntimeError("endpoint down")

    class _BrokenChat:
        completions = _BrokenCompletions()

    class _BrokenClient:
        chat = _BrokenChat()

        def with_options(self, **kwargs):
            return self

    c = LLMClient(_settings(llm_enabled=True))
    c._client = _BrokenClient()
    out = c.check_connection()
    assert out["ok"] is False
    assert out["kind"] == "unknown"
