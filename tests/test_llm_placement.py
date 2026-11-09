"""LLM placement-ranking and annotation skill tests — mocked endpoints,
deterministic validation, no network."""

import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMP_DIRS: list[Path] = []


def _settings(llm_enabled=True, **overrides):
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


def _fake_client(payload: str):
    fake = MagicMock()
    completions = fake.chat.completions
    # code calls .with_options(max_retries=0, timeout=…).create(...): make
    # with_options return the same fake completions object
    completions.with_options.return_value = completions
    completions.create.return_value.choices[0].message.content = payload
    return fake


def _candidates(n=4):
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


# ------------------------------------------------------------- choose_placement

def test_choose_placement_mocked_happy_path():
    from app.llm import LLMClient

    c = LLMClient(_settings(llm_enabled=True))
    c._client = _fake_client('{"choice": 1, "reason": "morning is quieter"}')
    out = c.choose_placement(
        job_context={"kind": "learn", "subjects": ["math"]},
        candidates=_candidates(4),
    )
    assert out == {"choice": 1, "reason": "morning is quieter"}


def test_choose_placement_out_of_range_raises():
    from app.llm import LLMClient

    c = LLMClient(_settings(llm_enabled=True))
    c._client = _fake_client('{"choice": 9, "reason": "nope"}')
    with pytest.raises(ValueError):
        c.choose_placement(job_context={}, candidates=_candidates(4))


def test_choose_placement_garbage_raises():
    from app.llm import LLMClient

    c = LLMClient(_settings(llm_enabled=True))
    c._client = _fake_client("not json at all")
    with pytest.raises(Exception):
        c.choose_placement(job_context={}, candidates=_candidates(4))


def test_choose_placement_bool_choice_rejected():
    from app.llm import LLMClient

    c = LLMClient(_settings(llm_enabled=True))
    c._client = _fake_client('{"choice": true, "reason": "x"}')
    with pytest.raises(ValueError):
        c.choose_placement(job_context={}, candidates=_candidates(4))


def test_choose_placement_requires_client():
    from app.llm import LLMClient

    c = LLMClient(_settings(llm_enabled=False))
    with pytest.raises(RuntimeError):
        c.choose_placement(job_context={}, candidates=_candidates(1))


# ------------------------------------------------------------- annotate_events

def test_annotate_events_mocked_filters_unknown_ids():
    from app.llm import LLMClient

    c = LLMClient(_settings(llm_enabled=True))
    payload = json.dumps(
        {
            "annotations": [
                {"event_id": "e1", "subject": "Math", "fatigue": 7, "note": "heavy"},
                {"event_id": "e2", "subject": "PHYSICS", "fatigue": 2},
                {"event_id": "eX", "subject": "bogus", "fatigue": 1},
            ]
        }
    )
    c._client = _fake_client(payload)
    out = c.annotate_events(
        events=[
            {"event_id": "e1", "title": "Math", "duration_minutes": 180, "venue": None},
            {"event_id": "e2", "title": "Physics", "duration_minutes": 90, "venue": None},
        ]
    )
    by_id = {item["event_id"]: item for item in out}
    assert set(by_id) == {"e1", "e2"}
    assert by_id["e1"]["fatigue"] == 3  # clamped to 0-3
    assert by_id["e1"]["subject"] == "math"
    assert by_id["e2"]["subject"] == "physics"  # normalised lowercase


def test_annotate_events_requires_client():
    from app.llm import LLMClient

    c = LLMClient(_settings(llm_enabled=False))
    with pytest.raises(RuntimeError):
        c.annotate_events(events=[{"event_id": "e1", "title": "x", "duration_minutes": 1}])
