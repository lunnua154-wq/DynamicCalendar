"""Semantic annotations for external events: ``subject`` + ``fatigue`` (0–3).

Two paths, both storing into the ``event_annotations`` table:

- **heuristic** (always available, offline-first): duration-based fatigue
  (≥3h → 3, ≥2h → 2, ≥45min → 1, plus 1 for ``lab`` in the title) and a
  keyword subject map over the title;
- **LLM** (when configured): one *batched* call for all un-annotated externals;
  per-item validation; items the LLM misses or mangles fall back to the
  heuristic individually.

The engine only ever *reads* this table — annotation is produced here, never
inside ``engine.replan``.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from . import db

#: keyword → subject. First hit wins; checked against the lowercased title.
_SUBJECT_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("calculus", "math"),
    ("algebra", "math"),
    ("geometry", "math"),
    ("trigonometry", "math"),
    ("statistics", "statistics"),
    ("math", "math"),
    ("physics", "physics"),
    ("chemistry", "chemistry"),
    ("biology", "biology"),
    ("photosynthesis", "biology"),
    ("english", "english"),
    ("literature", "english"),
    ("history", "history"),
    ("computer science", "cs"),
    ("programming", "cs"),
    ("algorithms", "cs"),
    ("software", "cs"),
    ("coding", "cs"),
    ("economics", "economics"),
    ("psychology", "psychology"),
    ("spanish", "spanish"),
    ("french", "french"),
    ("german", "german"),
    ("chinese", "chinese"),
    ("japanese", "japanese"),
    ("music", "music"),
    ("art", "art"),
)


#: regex → subject for codes like "CS1010" / "CS1010E" that the keyword list misses
_SUBJECT_REGEXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bcs\d", re.IGNORECASE), "cs"),
    (re.compile(r"\bce\d|\binfo\s?sys", re.IGNORECASE), "cs"),
)


def heuristic_annotation(
    title: str | None, duration_min: int, *, all_day: bool = False
) -> dict[str, Any]:
    """Offline annotation: duration-based fatigue + keyword subject.

    All-day events carry no fatigue signal (deadline markers are not lectures).
    """
    subj_source = (title or "").lower()
    if all_day:
        fatigue = 0
    else:
        fatigue = duration_fatigue(duration_min, subj_source)
    subject = "general"
    for keyword, subj in _SUBJECT_KEYWORDS:
        if keyword in subj_source:
            subject = subj
            break
    else:
        for pattern, subj in _SUBJECT_REGEXES:
            if pattern.search(subj_source):
                subject = subj
                break
    return {"subject": subject, "fatigue": fatigue}


def duration_fatigue(duration_min: int, lowered_title: str = "") -> int:
    if duration_min >= 180:
        fatigue = 3
    elif duration_min >= 120:
        fatigue = 2
    elif duration_min >= 45:
        fatigue = 1
    else:
        fatigue = 0
    if "lab" in lowered_title:
        fatigue = min(3, fatigue + 1)
    return int(fatigue)


def event_duration_minutes(event: dict[str, Any]) -> int:
    try:
        start = datetime.strptime(str(event["dtstart"]).replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
        end = datetime.strptime(str(event["dtend"]).replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
        return max(0, int((end - start).total_seconds() // 60))
    except Exception:
        return 0


def _normalize_llm_item(item: Any, event_ids: set[str]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    event_id = str(item.get("event_id") or "").strip()
    if event_id not in event_ids:
        return None
    try:
        fatigue = int(item.get("fatigue", 0))
    except (TypeError, ValueError):
        fatigue = 0
    if isinstance(item.get("fatigue"), bool):
        fatigue = 0
    fatigue = max(0, min(3, fatigue))
    subject = str(item.get("subject") or "general").strip().lower()
    if not subject or len(subject) > 40:
        subject = "general"
    note = item.get("note")
    note = str(note)[:200] if note else None
    return {"event_id": event_id, "subject": subject, "fatigue": fatigue, "note": note}


def annotate_missing(conn, *, llm: Any = None, time_budget_seconds: float | None = 45.0) -> int:
    """Annotate every locked external event lacking an annotation.

    Returns the number of events annotated. With ``llm`` configured and
    ``time_budget_seconds`` not zero, un-annotated events are sent in chunks of
    15; once the budget is spent, remaining events fall back to the heuristic
    individually (never raises — annotation must never block an import/replan).
    Pass ``time_budget_seconds=0`` to skip the LLM entirely (app startup and
    ICS import use this — deterministic, instant, offline-safe); ``None`` means
    no wall-clock budget (explicit user-triggered re-annotation).
    """
    externals = [e for e in db.list_events(conn) if e.get("kind") == "external" and e.get("locked")]
    annotated_ids = db.annotated_event_ids(conn)
    missing = [e for e in externals if e["id"] not in annotated_ids]
    if not missing:
        return 0

    payload = [
        {
            "event_id": e["id"],
            "title": e["title"],
            "duration_minutes": event_duration_minutes(e),
            "venue": e.get("venue"),
        }
        for e in missing
    ]
    llm_items: list[dict[str, Any]] = []
    if llm is not None and time_budget_seconds != 0:
        try:
            llm_items = llm.annotate_events(
                events=payload, time_budget_seconds=time_budget_seconds
            )
        except Exception:
            llm_items = []

    llm_by_id = {item["event_id"]: item for item in llm_items}
    count = 0
    for event in missing:
        item = llm_by_id.get(event["id"])
        if item is not None:
            db.upsert_annotation(
                conn,
                event["id"],
                subject=item["subject"],
                fatigue=item["fatigue"],
                source="llm",
                note=item.get("note"),
            )
        else:
            duration = event_duration_minutes(event)
            all_day = bool(event.get("all_day"))
            heuristic = heuristic_annotation(event.get("title"), duration, all_day=all_day)
            db.upsert_annotation(
                conn, event["id"], subject=heuristic["subject"],
                fatigue=heuristic["fatigue"], source="heuristic",
            )
        count += 1
    return count


def normalize_llm_annotations(data: Any, event_ids: set[str]) -> list[dict[str, Any]]:
    """Validate the ``{"annotations": [...]}`` payload returned by the LLM."""
    if not isinstance(data, dict):
        return []
    items = data.get("annotations")
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        norm = _normalize_llm_item(item, event_ids)
        if norm is not None and norm["event_id"] not in seen:
            seen.add(norm["event_id"])
            out.append(norm)
    return out
