"""LLM skill tests — mocked LLM happy path, garbage output -> fallback,
and unconfigured client -> offline fallback (no network)."""

from unittest.mock import MagicMock

from app.config import Settings
from app.llm import LLMClient


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


def test_tag_offline_fallback():
    c = LLMClient(_settings(llm_enabled=False))
    out = c.tag(markdown="# Limits chapter", filename="limits.pdf")
    assert out["tags"] == ["limits"]
    assert out["effort_hours"] == 1.0
    assert "limits.pdf" in out["summary"]


def test_tag_mocked_happy_path():
    fake = MagicMock()
    fake.chat.completions.create.return_value.choices[
        0
    ].message.content = '{"summary":"Limits","tags":["math"],"effort_hours":3.0}'
    c = LLMClient(_settings(llm_enabled=True))
    c._client = fake
    out = c.tag(markdown="# Limits", filename="limits.pdf")
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
    assert out["effort_hours"] == 1.0


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


def test_chat_offline_fallback():
    c = LLMClient(_settings(llm_enabled=False))
    out = c.chat(message="what's due?", history=[])
    assert out["llm"] == "off"
    assert "LLM not configured" in out["reply"]


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


def test_tune_settings_offline_noop():
    c = LLMClient(_settings(llm_enabled=False))
    out = c.tune_settings(context="busy week")
    assert out == {}  # offline: settings keep current values
