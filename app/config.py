"""Application configuration and dynamic settings.

Settings are persisted in SQLite (table ``settings``) and cached in memory.
Defaults are the placeholder "fake time" values from docs/DATA_MODEL.md.
Bounds are enforced server-side (AGENTS.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .errors import ValidationError

# Bounds (enforced server-side, LLM or user)
DAILY_CAPACITY_MIN = 0.5
DAILY_CAPACITY_MAX = 12.0
SLOT_MINUTES_MIN = 15
SLOT_MINUTES_MAX = 120
EFFORT_HOURS_MIN = 0.1
EFFORT_HOURS_MAX = 20.0

DEFAULT_SETTINGS: dict[str, str] = {
    "daily_capacity_hours": "4.0",
    "slot_minutes": "50",
    "min_slot_minutes": "30",
    "revision_intervals_days": "[1,3,7,14]",
    "llm_base_url": "",
    "llm_model": "",
    "llm_api_key": "",
    "default_effort_hours": "1.0",
}

STUDY_WINDOW_START_HOUR = 8
STUDY_WINDOW_END_HOUR = 22


@dataclass(frozen=True)
class Settings:
    daily_capacity_hours: float
    slot_minutes: int
    min_slot_minutes: int
    revision_intervals_days: list[int]
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    default_effort_hours: float

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)


def validate_capacity_hours(value: float) -> float:
    if not (DAILY_CAPACITY_MIN <= value <= DAILY_CAPACITY_MAX):
        raise ValidationError(
            f"daily_capacity_hours must be between {DAILY_CAPACITY_MIN} and {DAILY_CAPACITY_MAX}"
        )
    return value


def validate_slot_minutes(value: int) -> int:
    if not (SLOT_MINUTES_MIN <= value <= SLOT_MINUTES_MAX):
        raise ValidationError(
            f"slot_minutes must be between {SLOT_MINUTES_MIN} and {SLOT_MINUTES_MAX}"
        )
    return value


def validate_effort_hours(value: float) -> float:
    if not (EFFORT_HOURS_MIN <= value <= EFFORT_HOURS_MAX):
        raise ValidationError(
            f"effort_hours must be between {EFFORT_HOURS_MIN} and {EFFORT_HOURS_MAX}"
        )
    return value


def _decode_setting(value: str) -> str:
    """Settings are persisted via ``json.dumps``, so a string setting like the
    LLM base URL is stored with surrounding quotes. Decode it back to a plain
    string so the LLM client receives a usable URL / key."""
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value
    return decoded if isinstance(decoded, str) else value


def parse_settings(rows: list[dict[str, Any]]) -> Settings:
    values = {row["key"]: row["value"] for row in rows}
    return Settings(
        daily_capacity_hours=float(
            values.get("daily_capacity_hours", DEFAULT_SETTINGS["daily_capacity_hours"])
        ),
        slot_minutes=int(
            values.get("slot_minutes", DEFAULT_SETTINGS["slot_minutes"])
        ),
        min_slot_minutes=int(
            values.get("min_slot_minutes", DEFAULT_SETTINGS["min_slot_minutes"])
        ),
        revision_intervals_days=json.loads(
            values.get(
                "revision_intervals_days",
                DEFAULT_SETTINGS["revision_intervals_days"],
            )
        ),
        llm_base_url=_decode_setting(values.get("llm_base_url", "")),
        llm_model=_decode_setting(values.get("llm_model", "")),
        llm_api_key=_decode_setting(values.get("llm_api_key", "")),
        default_effort_hours=float(
            values.get("default_effort_hours", DEFAULT_SETTINGS["default_effort_hours"])
        ),
    )
