"""OpenAI-compatible tool registry for the chat skill.

Ten narrow tools: six read-only (list_goals, list_jobs, list_events,
list_materials, get_stats, list_constraints) and four bounded writes
(update_settings, set_material_effort, add_constraint, remove_constraint).
The engine remains the scheduler of record — tools never move jobs directly;
constraint writes take effect through a replan, and external events are
read-only.
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
            "name": "list_constraints",
            "description": (
                "List the scheduling constraints the planner enforces "
                "(e.g. 'no math right after math class', 'no study within "
                "60 min of a fatigue-3 event'). Read-only. "
                "No parameters."
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
            "name": "add_constraint",
            "description": (
                "Create a scheduling constraint from the user's request "
                "(e.g. 'avoid math right after math class'). Known kinds: "
                "'subject_gap' {subject, gap_minutes 15-240, optional 'hard'}, "
                "'post_fatigue_buffer' {min_fatigue 1-3, buffer_minutes 15-240, "
                "optional 'hard'}, 'interleave' {max_consecutive 1-5}, "
                "'blocked_day_part' {start_hour 0-23, end_hour 0-23, optional 'hard'}. "
                "Interleave is soft-only (hard=true is rejected for it). "
                "Hard constraints exclude candidates (relaxed only if no candidate "
                "survives); soft constraints just penalise them. Re-plans open goals "
                "and returns a receipt."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": (
                            "subject_gap | post_fatigue_buffer | interleave | "
                            "blocked_day_part"
                        ),
                    },
                    "params": {
                        "type": "object",
                        "description": (
                            "Kind-specific parameters, e.g. {\"subject\": \"math\", "
                            "\"gap_minutes\": 60}. Validated server-side."
                        ),
                        "additionalProperties": True,
                    },
                    "label": {
                        "type": "string",
                        "description": "Short human-readable description (optional).",
                    },
                    "hard": {
                        "type": "boolean",
                        "description": "Hard-exclude matching slots instead of penalising (default false).",
                    },
                },
                "required": ["kind", "params"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_constraint",
            "description": (
                "Delete a scheduling constraint by id (from list_constraints). "
                "Re-plans open goals and returns a receipt."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "constraint_id": {
                        "type": "string",
                        "description": "Id of the constraint to delete.",
                    }
                },
                "required": ["constraint_id"],
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
        if name == "list_constraints":
            return {"constraints": actions.list_constraints(conn)}
        if name == "add_constraint":
            return actions.add_constraint(
                conn,
                kind=_require(args, "kind"),
                params=args.get("params") or {},
                label=args.get("label"),
                hard=args.get("hard", False),
                source="llm",
                now_iso=now_iso,
            )
        if name == "remove_constraint":
            return actions.remove_constraint(
                conn, _require(args, "constraint_id"), now_iso=now_iso
            )
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
