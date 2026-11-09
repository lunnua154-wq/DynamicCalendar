"""Application configuration and dynamic settings.

Settings are persisted in SQLite (table ``settings``) and cached in memory.
Defaults are the placeholder "fake time" values from docs/DATA_MODEL.md.
Bounds are enforced server-side (AGENTS.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from .errors import ValidationError

# Bounds (enforced server-side, LLM or user)
DAILY_CAPACITY_MIN = 0.5
DAILY_CAPACITY_MAX = 12.0
SLOT_MINUTES_MIN = 15
SLOT_MINUTES_MAX = 120
EFFORT_HOURS_MIN = 0.1
EFFORT_HOURS_MAX = 20.0
UTC_OFFSET_MIN = -840
UTC_OFFSET_MAX = 840
LLM_MODEL_MAX_CHARS = 200

DEFAULT_SETTINGS: dict[str, str] = {
    "daily_capacity_hours": "4.0",
    "slot_minutes": "50",
    "min_slot_minutes": "30",
    "revision_intervals_days": "[1,3,7,14]",
    "llm_base_url": "",
    "llm_model": "",
    "llm_api_key": "",
    "default_effort_hours": "1.0",
    # Smart placement (deterministic scorer) and optional LLM slot ranking.
    "smart_placement": "on",
    "llm_placement_mode": "off",
    # Cloudflare OCR (M4) — off by default; opt-in via settings.
    "ocr_provider": "off",
    "cf_account_id": "",
    "cf_api_token": "",
    "ocr_model": "@cf/unum/uform-gen2-qwen-500m",
    "ocr_min_chars": "40",
}

#: Seeded from the host clock on first run (see ``db.init_db``), then a normal
#: dynamic setting: local study windows without making the engine clock-aware.
HOST_OFFSET_SETTING = "utc_offset_minutes"

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
    # Cloudflare OCR (M4) — off by default; opt-in via settings.
    ocr_provider: str = "off"
    cf_account_id: str = ""
    cf_api_token: str = ""
    ocr_model: str = "@cf/unum/uform-gen2-qwen-500m"
    ocr_min_chars: int = 40
    # Local clock offset used to lay out study windows (storage stays UTC).
    utc_offset_minutes: int = 0
    # Smart placement: 'on' = penalised candidate scoring in the engine;
    # 'off' = legacy earliest-free-slot scoring. Stored constraints still
    # apply in both modes.
    smart_placement: str = "on"
    # LLM slot selection: 'off' (default) or 'rank' (LLM picks from a
    # hard-valid shortlist; deterministic fallback on any failure).
    llm_placement_mode: str = "off"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)

    @property
    def ocr_enabled(self) -> bool:
        """True only when Cloudflare OCR is configured with credentials."""
        return (
            self.ocr_provider == "cloudflare"
            and bool(self.cf_account_id)
            and bool(self.cf_api_token)
        )


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


def validate_utc_offset_minutes(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("utc_offset_minutes must be a whole number of minutes")
    if not (UTC_OFFSET_MIN <= value <= UTC_OFFSET_MAX):
        raise ValidationError(
            f"utc_offset_minutes must be between {UTC_OFFSET_MIN} and {UTC_OFFSET_MAX}"
        )
    return value


def validate_llm_base_url(value: str) -> str:
    """Empty means "offline mode"; otherwise it must be an http(s) URL.

    A malformed URL would silently disable the LLM (the client construction is
    wrapped in a fallback), which is exactly the failure users cannot diagnose.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValidationError(
            "llm_base_url must be an http(s) URL such as http://localhost:11434/v1"
        )
    return text


def validate_llm_model(value: str) -> str:
    text = str(value or "").strip()
    if len(text) > LLM_MODEL_MAX_CHARS:
        raise ValidationError(f"llm_model must be at most {LLM_MODEL_MAX_CHARS} characters")
    if any(ord(ch) < 32 for ch in text):
        raise ValidationError("llm_model must not contain control characters")
    return text


def validate_smart_placement(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text not in ("on", "off"):
        raise ValidationError("smart_placement must be 'on' or 'off'")
    return text


def validate_llm_placement_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text not in ("off", "rank"):
        raise ValidationError("llm_placement_mode must be 'off' or 'rank'")
    return text


def host_utc_offset_minutes() -> int:
    """Offset of the host clock from UTC, in minutes (e.g. +480 for UTC+8)."""
    offset = datetime.now(timezone.utc).astimezone().utcoffset()
    return int((offset.total_seconds() if offset else 0) // 60)


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
        ocr_provider=_decode_setting(
            values.get("ocr_provider", DEFAULT_SETTINGS["ocr_provider"])
        ),
        cf_account_id=_decode_setting(values.get("cf_account_id", "")),
        cf_api_token=_decode_setting(values.get("cf_api_token", "")),
        ocr_model=_decode_setting(values.get("ocr_model", DEFAULT_SETTINGS["ocr_model"])),
        ocr_min_chars=int(
            values.get("ocr_min_chars", DEFAULT_SETTINGS["ocr_min_chars"])
        ),
        utc_offset_minutes=(
            int(_decode_setting(values.get("utc_offset_minutes", "")) or 0)
        ),
        smart_placement=validate_smart_placement(
            _decode_setting(values.get("smart_placement", "on")) or "on"
        ),
        llm_placement_mode=validate_llm_placement_mode(
            _decode_setting(values.get("llm_placement_mode", "off")) or "off"
        ),
    )
