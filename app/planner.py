"""Replan entry point that injects an LLM placement chooser when configured.

``engine.replan`` itself performs no network I/O — the LLM is an *injected
dependency* (``chooser``), exactly like the injected clock. This module owns
the wiring: when ``llm_placement_mode='rank'`` and an LLM endpoint is
configured, it builds a chooser bound to one replan call; otherwise the engine
takes its fully deterministic path.

Budget: at most ``MAX_LLM_PLACEMENT_CALLS`` calls **and** ``MAX_LLM_PLACEMENT_SECONDS``
ellapsed wall-clock per replan. Jobs beyond the budget are planned by the
deterministic scorer with a "LLM budget reached" receipt — never an error,
never a stall.
"""

from __future__ import annotations

import time
from typing import Any

from . import db, engine
from .config import parse_settings

MAX_LLM_PLACEMENT_CALLS = 16
MAX_LLM_PLACEMENT_SECONDS = 20.0


def replan(conn, goal_id: str, now_iso: str, allow_llm_chooser: bool = True) -> dict[str, Any]:
    """Replan ``goal_id`` honouring ``llm_placement_mode``.

    Returns the same dict as ``engine.replan``. When ``allow_llm_chooser`` is
    False the LLM placement chooser is never injected, even if
    ``llm_placement_mode='rank'`` and an endpoint is configured (used by the
    ICS import path so LLM I/O never holds the import transaction).
    """
    settings = parse_settings(db.list_settings(conn))
    chooser = None
    if (
        allow_llm_chooser
        and getattr(settings, "llm_placement_mode", "off") == "rank"
        and settings.llm_enabled
    ):
        # Lazy import: keeps llm -> tools -> actions -> planner acyclic.
        from .llm import LLMClient

        client = LLMClient(settings)
        budget = {"calls": 0, "deadline": time.monotonic() + MAX_LLM_PLACEMENT_SECONDS}

        def chooser(job_context: dict, candidates: list[dict]) -> dict | None:
            if budget["calls"] >= MAX_LLM_PLACEMENT_CALLS or time.monotonic() > budget["deadline"]:
                return {"budget_exhausted": True}
            budget["calls"] += 1
            remaining = budget["deadline"] - time.monotonic()
            return client.choose_placement(
                job_context=job_context,
                candidates=candidates,
                timeout=min(15, max(1, remaining)),
            )

    return engine.replan(conn, goal_id, now_iso, chooser=chooser)
