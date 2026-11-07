"""OpenAI-compatible tool registry for the v0.1 chat skill.

Exactly seven narrow tools: five read-only (list_goals, list_jobs,
list_events, list_materials, get_stats) and two bounded writes
(update_settings, set_material_effort). The engine remains the only
scheduler — no tool moves jobs or creates/changes external events, and
external events are read-only.
"""

from __future__ import annotations

from typing import Any

from . import actions

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_goals",
            "description": (
                "List open study goals with progress (deadlines, effort "
                "done vs planned). Use when the user asks about their "
                "goals, what they are studying, or how far along they are."
                " No parameters."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_jobs",
            "description": (
                "List study/revision jobs in a time window. from/to are "
                "optional ISO-8601 UTC timestamps; default = now .. +14 "
                "days. Use for 'what's due this week' or 'what's on my "
                "schedule'. Read-only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from_iso": {
                        "type": "string",
                        "description": (
                            "Optional window start, ISO-8601 UTC "
                            "(default = now)."
                        ),
                    },
                    "to_iso": {
                        "type": "string",
                        "description": (
                            "Optional window end, ISO-8601 UTC "
                            "(default = now + 14 days)."
                        ),
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": (
                "List locked external calendar events (lectures, exams) "
                "in a time window. from/to are optional ISO-8601 UTC "
                "timestamps; default = now .. +14 days. Read-only: never "
                "creates or moves events."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from_iso": {
                        "type": "string",
                        "description": (
                            "Optional window start, ISO-8601 UTC "
                            "(default = now)."
                        ),
                    },
                    "to_iso": {
                        "type": "string",
                        "description": (
                            "Optional window end, ISO-8601 UTC "
                            "(default = now + 14 days)."
                        ),
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_materials",
            "description": (
                "List recently ingested study materials (files) with "
                "summaries, tags, status and effort hours. Use when the "
                "user asks about materials or wants a summary of a file."
                " No parameters."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stats",
            "description": (
                "Study progress summary: planned/done minutes, progress, "
                "materials and revisions due. Optionally pass goal_id for "
                "one goal. Read-only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": {
                        "type": "string",
                        "description": "Optional goal id to scope one goal.",
                    }
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_settings",
            "description": (
                "Change study settings: daily_capacity_hours (0.5-12) "
                "and/or slot_minutes (15-120). Only when the user "
                "explicitly asks to change study settings. Values are "
                "bounds-validated and stored with updated_by='llm'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "daily_capacity_hours": {
                        "type": "number",
                        "description": "Daily study capacity in hours (0.5-12).",
                    },
                    "slot_minutes": {
                        "type": "integer",
                        "description": "Planning slot length in minutes (15-120).",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_material_effort",
            "description": (
                "Set the estimated effort hours for a material (0.1-20). "
                "Re-plans the attached goal, if any, right after."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "material_id": {
                        "type": "string",
                        "description": "Id of the material (from list_materials).",
                    },
                    "hours": {
                        "type": "number",
                        "description": "Effort hours (0.1-20).",
                    },
                },
                "required": ["material_id", "hours"],
                "additionalProperties": False,
            },
        },
    },
]


def _require(args: dict, key: str) -> Any:
    if key not in args:
        raise ValueError(f"missing argument: {key}")
    return args[key]


def dispatch(
    conn, name: str, arguments: dict, now_iso: str
) -> dict[str, Any]:
    """Route a tool call to app.actions. Never raises: every failure becomes
    {"error": <message>} so the LLM loop can keep going and report a receipt."""
    args = dict(arguments or {})
    try:
        if name == "list_goals":
            return {"goals": actions.list_goals(conn)}
        if name == "list_jobs":
            return {
                "jobs": actions.list_jobs(
                    conn, from_iso=args.get("from_iso"), to_iso=args.get("to_iso")
                )
            }
        if name == "list_events":
            return {
                "events": actions.list_events(
                    conn, from_iso=args.get("from_iso"), to_iso=args.get("to_iso")
                )
            }
        if name == "list_materials":
            return {"materials": actions.list_materials(conn)}
        if name == "get_stats":
            return {"stats": actions.get_stats(conn, goal_id=args.get("goal_id"))}
        if name == "update_settings":
            changes: dict[str, Any] = {}
            for key in ("daily_capacity_hours", "slot_minutes"):
                if key in args:
                    changes[key] = args[key]
            return actions.update_settings(conn, changes, now_iso)
        if name == "set_material_effort":
            return actions.set_material_effort(
                conn,
                str(_require(args, "material_id")),
                float(_require(args, "hours")),
                now_iso,
            )
        return {"error": f"unknown tool {name}"}
    except Exception as exc:  # coercion, validation, not-found: never propagate
        return {"error": str(exc)}


def tool_specs() -> list[dict[str, Any]]:
    """Return the OpenAI tool spec list (TOOLS)."""
    return TOOLS
