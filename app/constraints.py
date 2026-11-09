"""Scheduling constraints: model, validation, scoring and NL parsing.

Constraints are **data** (table ``constraints``): the LLM compiles natural
language into a validated ``(kind, params)`` tuple; Python enforces it
deterministically inside the engine. Every constraint is *soft* (a penalty, the
normal case) or *hard* (the candidate is filtered out — but see the relaxation
rule in the engine: a plan can never deadlock, hard constraints degrade to soft
when no candidate survives).

This module is stdlib-only and performs no I/O except reading through ``db``
helpers passed in by the caller — the engine calls it with plain dicts.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any

#: penalty applied when a soft fatigue-buffer constraint matches
POST_FATIGUE_PENALTY = 50.0
#: penalty applied when a soft subject-gap constraint matches
SUBJECT_GAP_PENALTY = 50.0
#: penalty for built-in lower-priority taste rules
INTERLEAVE_PENALTY = 25.0
BLOCKED_DAY_PART_PENALTY = 40.0
BUILTIN_FATIGUE_PENALTY_2 = 30.0
BUILTIN_FATIGUE_PENALTY_3 = 60.0
BUILTIN_SUBJECT_ADJACENCY_PENALTY = 30.0
BUILTIN_INTERLEAVE_PENALTY = 10.0
#: how long after an event the "immediately after" heuristics apply
ADJACENCY_WINDOW_MINUTES = 60

CONSTRAINT_KINDS = (
    "post_fatigue_buffer",
    "subject_gap",
    "interleave",
    "blocked_day_part",
)

_SUBJECT_MAX = 40


def _as_int(value: Any, key: str, lo: int, hi: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    try:
        num = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be an integer") from None
    if num != float(num):
        raise ValueError(f"{key} must be a whole number")
    if not (lo <= num <= hi):
        raise ValueError(f"{key} must be between {lo} and {hi}")
    return num


def _as_subject(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    if not text:
        raise ValueError("params.subject must be a non-empty string")
    if len(text) > _SUBJECT_MAX:
        raise ValueError(f"params.subject must be at most {_SUBJECT_MAX} characters")
    if not re.fullmatch(r"[a-z0-9][a-z0-9 \-/&+]*", text):
        raise ValueError("params.subject may only contain letters, numbers, spaces, - / & +")
    return text


def validate_params(kind: str, params: Any) -> dict[str, Any]:
    """Validate ``params`` for ``kind``; returns a normalised dict.

    Raises ``ValueError`` with a human-readable message on any problem.
    """
    if not isinstance(params, dict):
        raise ValueError("params must be a JSON object")
    if kind == "post_fatigue_buffer":
        return {
            "min_fatigue": _as_int(params.get("min_fatigue", 2), "params.min_fatigue", 1, 3),
            "buffer_minutes": _as_int(
                params.get("buffer_minutes", ADJACENCY_WINDOW_MINUTES),
                "params.buffer_minutes", 15, 240,
            ),
        }
    if kind == "subject_gap":
        return {
            "subject": _as_subject(params.get("subject")),
            "gap_minutes": _as_int(
                params.get("gap_minutes", ADJACENCY_WINDOW_MINUTES),
                "params.gap_minutes", 15, 240,
            ),
        }
    if kind == "interleave":
        return {
            "max_consecutive": _as_int(
                params.get("max_consecutive", 2), "params.max_consecutive", 1, 5
            ),
        }
    if kind == "blocked_day_part":
        start = _as_int(params.get("start_hour"), "params.start_hour", 0, 23)
        end = _as_int(params.get("end_hour"), "params.end_hour", 0, 23)
        if start >= end:
            raise ValueError("params.start_hour must be before params.end_hour")
        return {"start_hour": start, "end_hour": end}
    raise ValueError(f"unknown constraint kind {kind!r}; known kinds: {', '.join(CONSTRAINT_KINDS)}")


def validate_constraint(kind: Any, params: Any) -> tuple[str, dict[str, Any]]:
    """Validate kind + params together. Returns ``(kind, normalised_params)``."""
    kind_text = str(kind or "").strip().lower()
    if kind_text not in CONSTRAINT_KINDS:
        raise ValueError(
            f"unknown constraint kind {kind_text!r}; known kinds: {', '.join(CONSTRAINT_KINDS)}"
        )
    return kind_text, validate_params(kind_text, params)


def constraint_label(kind: str, params: dict[str, Any]) -> str:
    """Human-readable label generated from kind + params (used when omitted)."""
    if kind == "post_fatigue_buffer":
        return (
            f"No study within {params['buffer_minutes']} min after a "
            f"fatigue≥{params['min_fatigue']} event"
        )
    if kind == "subject_gap":
        return f"No {params['subject']} study within {params['gap_minutes']} min after {params['subject']} events"
    if kind == "interleave":
        return f"No more than {params['max_consecutive']} consecutive same-subject jobs"
    if kind == "blocked_day_part":
        return f"No study between {params['start_hour']:02.0f}:00 and {params['end_hour']:02.0f}:00"
    return kind  # pragma: no cover - unknown kinds are rejected by validation


def parse_constraint_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a ``constraints`` table row into ``{kind, params, hard, label, id}``.

    Out-of-band or hand-corrupted rows (bad JSON, missing/again invalid params)
    return ``None`` — callers must skip them, so one bad row can never crash
    every replan.
    """
    try:
        params = json.loads(row["params"])
        if not isinstance(params, dict):
            return None
        kind = str(row["kind"])
        params = validate_params(kind, params)
    except (TypeError, json.JSONDecodeError, ValueError):
        return None
    return {
        "id": row["id"],
        "kind": kind,
        "params": params,
        "hard": bool(row["hard"]),
        "label": row.get("label") or kind,
    }


def serialise_params(params: dict[str, Any]) -> str:
    return json.dumps(params, separators=(",", ":"), sort_keys=True)


# ----------------------------------------------------------------- scoring


def _in_after_window(slot_start, end, minutes: int) -> bool:
    if slot_start < end:
        return False
    return slot_start < end + timedelta(minutes=minutes)


def score_slot(
    slot_start,
    slot_end,  # noqa: ARG001 - end kept for symmetry/future rules
    *,
    subjects: list[str],
    externals: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
    smart: bool,
    relax_hard: bool = False,
) -> tuple[float, bool, list[str]]:
    """Score a candidate slot.

    Returns ``(penalty, blocked, reasons)``. ``blocked`` is True when a *hard*
    constraint matches and ``relax_hard`` is False. Penalties are additive;
    ``reasons`` collects short human-readable explanations for the placement note.

    Built-in "right after" wisdom applies only inside ``ADJACENCY_WINDOW_MINUTES``
    (60 min) after the external — a longer user-configured constraint window does
    not stretch it. Adjacency between jobs is scored by the engine (it knows the
    chronological neighbours of the candidate), not here.
    """
    penalty = 0.0
    blocked = False
    reasons: list[str] = []

    def penalise(points: float, reason: str, hard: bool) -> None:
        nonlocal penalty, blocked
        if hard and not relax_hard:
            blocked = True
            reasons.append(f"[hard] {reason}")
        else:
            penalty += points
            reasons.append(reason)

    subject_set = {s for s in subjects if s}

    for ext in externals:
        end = ext["end"]
        fatigue = int(ext.get("fatigue") or 0)
        ext_subject = (ext.get("subject") or "").lower()
        if smart and fatigue >= 2 and _in_after_window(slot_start, end, ADJACENCY_WINDOW_MINUTES):
            points = BUILTIN_FATIGUE_PENALTY_3 if fatigue >= 3 else BUILTIN_FATIGUE_PENALTY_2
            penalise(points, f"within 60 min of a fatigue-{fatigue} event", False)
        if (
            smart
            and ext_subject
            and ext_subject != "general"
            and ext_subject in subject_set
            and _in_after_window(slot_start, end, ADJACENCY_WINDOW_MINUTES)
        ):
            penalise(
                BUILTIN_SUBJECT_ADJACENCY_PENALTY, f"right after {ext_subject} events", False
            )

    for con in constraints:
        kind, params = con["kind"], con["params"]
        hard = bool(con.get("hard"))
        if kind == "post_fatigue_buffer":
            for ext in externals:
                if int(ext.get("fatigue") or 0) >= int(params["min_fatigue"]):
                    if _in_after_window(slot_start, ext["end"], int(params["buffer_minutes"])):
                        penalise(
                            POST_FATIGUE_PENALTY,
                            f"within {params['buffer_minutes']} min of a "
                            f"fatigue-{ext['fatigue']} event",
                            hard,
                        )
                        break
        elif kind == "subject_gap":
            want = params["subject"]
            if want in subject_set:
                for ext in externals:
                    if (ext.get("subject") or "").lower() == want and _in_after_window(
                        slot_start, ext["end"], int(params["gap_minutes"])
                    ):
                        penalise(
                            SUBJECT_GAP_PENALTY,
                            f"within {params['gap_minutes']} min of a {want} event",
                            hard,
                        )
                        break
        elif kind == "blocked_day_part":
            hour = slot_start.hour
            if int(params["start_hour"]) <= hour < int(params["end_hour"]):
                penalise(
                    BLOCKED_DAY_PART_PENALTY,
                    f"inside blocked day part {params['start_hour']:02.0f}:00–"
                    f"{params['end_hour']:02.0f}:00",
                    hard,
                )

    return penalty, blocked, reasons


def job_adjacency_penalty(
    slot_start,
    *,
    primary: str,
    preceding_entries: list[tuple],
    constraints: list[dict[str, Any]],
    smart: bool,
) -> tuple[float, list[str]]:
    """Penalty for placing a job directly back-to-back with same-subject work.

    ``preceding_entries`` are ``(start, end, subject)`` tuples for occupied
    ranges ending at or before the candidate. Only *touching* chains count — a
    slot with a full free slot (or an overnight break) before it is never
    penalised, and a foreign-subject job in between breaks the run. Candidate-
    dependent, so argmin is actually steered and same-day packing survives.
    """
    penalty = 0.0
    reasons: list[str] = []
    if not primary:
        return penalty, reasons
    entries = [e for e in preceding_entries if e[1] <= slot_start]
    if not entries:
        return penalty, reasons
    entries.sort(key=lambda e: (e[0], e[1]))
    last_start, last_end, last_subject = entries[-1]
    if smart and last_subject == primary and slot_start == last_end:
        penalty += BUILTIN_INTERLEAVE_PENALTY
        reasons.append(f"back-to-back {primary} jobs")
    interleave = next((c for c in constraints if c["kind"] == "interleave"), None)
    if interleave is not None:
        n = int(interleave["params"]["max_consecutive"])
        run = 0
        cursor = slot_start
        for start, end, subject in reversed(entries):
            if subject != primary or end != cursor:
                break
            run += 1
            cursor = start
        if run >= n:
            penalty += INTERLEAVE_PENALTY
            reasons.append(f"more than {n} consecutive {primary} jobs")
    return penalty, reasons


# ------------------------------------------------------------------- NL parse

_AFTER_CLAUSE = re.compile(
    r"(?:no|avoid|don't|do not|never|not)\s+(?:studying\s+|study\s+)?"
    r"([a-z][a-z0-9\- ]{0,30}?)\s+"
    r"(?:right\s+)?after\s+(?:my\s+|the\s+)?"
    r"([a-z][a-z0-9\- ]{0,30}?)\s*(?:class|lecture|course|session|lab|seminar)",
    re.IGNORECASE,
)
_FATIGUE_CLAUSE = re.compile(
    r"(?:no|avoid|don't|do not|never|not)\s+(?:studying\s+|study\s+)?"
    r"(?:right\s+)?after\s+"
    r"(?:a\s+|an\s+|the\s+)?"
    r"(?:long|tiring|exhausting|draining|heavy|fatigu\w*|[0-9]+\s*[-\u2013]?\s*h(?:our)?s?)\b",
    re.IGNORECASE,
)
_LUNCH_CLAUSE = re.compile(
    r"(?:no|avoid|block|never)\s+(?:study(?:ing)?\s+)?(?:at\s+)?lunch",
    re.IGNORECASE,
)
_INTERLEAVE_CLAUSE = re.compile(
    r"(?:no|avoid|max(?:imum)?\s*)\s*(?:more\s+than\s+)?(\d+)\s+(?:consecutive|in\s+a\s+row)",
    re.IGNORECASE,
)


def parse_nl(text: str) -> dict[str, Any] | None:
    """Deterministic parser for the common constraint utterances (offline mode).

    Returns ``{kind, params, label}`` or ``None`` when nothing matches — the
    caller should then fall back to the LLM compiler or ask the user to rephrase.
    """
    msg = (text or "").strip().lower()
    if not msg:
        return None

    if _FATIGUE_CLAUSE.search(msg):
        params = {"min_fatigue": 2, "buffer_minutes": 60}
        return {
            "kind": "post_fatigue_buffer",
            "params": params,
            "label": constraint_label("post_fatigue_buffer", params),
        }
    if _LUNCH_CLAUSE.search(msg):
        params = {"start_hour": 12, "end_hour": 13}
        return {
            "kind": "blocked_day_part",
            "params": params,
            "label": constraint_label("blocked_day_part", params),
        }

    m = _AFTER_CLAUSE.search(msg)
    if m:
        subject, other = m.group(1).strip().lower(), m.group(2).strip().lower()
        # "avoid math right after math class" -> subject_gap on math;
        # "avoid studying right after math class" -> group(1) is "right"
        # (a non-subject) -> fall back to the event's own subject
        stopwords = {"right", "too", "not", "more", "a", "an", "the", "to", "of"}
        chosen = subject if subject not in stopwords else other
        if chosen in stopwords:
            return None
        # strip leading verb-ish filler tokens: "doing math" -> "math",
        # "want to study math" -> "math"
        filler = {
            "do", "doing", "did", "studying", "study", "studies", "want", "to",
            "need", "like", "love", "hate", "care", "bother", "mind", "learn",
            "learning", "review", "revise", "revising", "go", "get",
        }
        tokens = chosen.split()
        while tokens and tokens[0] in filler:
            tokens.pop(0)
        if not tokens:
            return None
        chosen = " ".join(tokens)
        try:
            params = validate_params("subject_gap", {"subject": chosen, "gap_minutes": 60})
        except ValueError:
            return None
        return {
            "kind": "subject_gap",
            "params": params,
            "label": constraint_label("subject_gap", params),
        }

    m = _INTERLEAVE_CLAUSE.search(msg)
    if m:
        try:
            params = validate_params("interleave", {"max_consecutive": int(m.group(1))})
        except ValueError:
            return None
        return {
            "kind": "interleave",
            "params": params,
            "label": constraint_label("interleave", params),
        }

    return None
