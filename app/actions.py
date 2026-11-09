"""Service-layer actions used by the v0.1 chat tools.

Read-mostly helpers plus two bounded write actions (update_settings,
set_material_effort) and constraint actions (add/list/remove). The engine
remains the scheduler of record: every write path that changes cognitive load
or plan shape ends in a replan; no tool moves jobs or touches external events.

Stdlib only — no LLM imports.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db, planner
from .constraints import (
    constraint_label,
    parse_constraint_row,
    serialise_params,
    validate_constraint,
)
from .config import (
    validate_capacity_hours,
    validate_effort_hours,
    validate_slot_minutes,
)
from .errors import NotFoundError


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _date_of(iso: str):
    s = iso.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc).date()


def _default_window() -> tuple[str, str]:
    """Default chat window: now -> end of day, 14 days out (matches
    _build_chat_context in app/main.py)."""
    now = datetime.now(timezone.utc)
    today = now.date()
    end = today + timedelta(days=14)
    return (
        now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        end.strftime("%Y-%m-%dT23:59:59Z"),
    )


def _goal_progress(conn, goal_id: str) -> dict[str, Any]:
    """progress, total_effort_hours, done_effort_hours, planned_jobs, done_jobs.

    Same shape as _build_chat_context in app/main.py.
    """
    total_minutes = 0.0
    for r in conn.execute(
        "SELECT effort_hours, status FROM materials WHERE goal_id = ?", (goal_id,)
    ).fetchall():
        if (
            r["status"] in ("ready", "tagged", "new")
            and r["effort_hours"] is not None
        ):
            total_minutes += float(r["effort_hours"]) * 60
    done_minutes = 0
    planned = 0
    done = 0
    for j in conn.execute(
        "SELECT status, duration_min FROM jobs WHERE goal_id = ?", (goal_id,)
    ).fetchall():
        if j["status"] == "done":
            done_minutes += int(j["duration_min"])
            done += 1
        elif j["status"] == "pending":
            planned += 1
    progress = 0.0
    if total_minutes > 0:
        progress = min(1.0, done_minutes / total_minutes)
    return {
        "progress": round(progress, 4),
        "total_effort_hours": round(total_minutes / 60, 4),
        "done_effort_hours": round(done_minutes / 60, 4),
        "planned_jobs": planned,
        "done_jobs": done,
    }


def list_goals(conn) -> list[dict[str, Any]]:
    """Open goals with progress ({**goal, **progress})."""
    out: list[dict[str, Any]] = []
    for g in db.list_goals(conn):
        if g["status"] != "open":
            continue
        out.append({**g, **_goal_progress(conn, g["id"])})
    return out


def list_jobs(
    conn,
    from_iso: str | None = None,
    to_iso: str | None = None,
) -> list[dict[str, Any]]:
    """Jobs in a window; default = now -> +14 days (ids included)."""
    if from_iso is None or to_iso is None:
        df, dt = _default_window()
        from_iso = from_iso or df
        to_iso = to_iso or dt
    return db.list_jobs(conn, from_iso=from_iso, to_iso=to_iso)


def list_events(
    conn,
    from_iso: str | None = None,
    to_iso: str | None = None,
) -> list[dict[str, Any]]:
    """Locked external events only in a window; default = now -> +14 days."""
    if from_iso is None or to_iso is None:
        df, dt = _default_window()
        from_iso = from_iso or df
        to_iso = to_iso or dt
    return [
        e
        for e in db.list_events(conn, from_iso=from_iso, to_iso=to_iso)
        if e.get("kind") == "external" and e.get("locked")
    ]


def list_materials(conn) -> list[dict[str, Any]]:
    """[:50] compact material shapes (same as the chat context)."""
    out: list[dict[str, Any]] = []
    for m in db.list_materials(conn)[:50]:
        out.append(
            {
                "id": m["id"],
                "filename": m["filename"],
                "summary": m["summary"],
                "tags": json.loads(m["tags"]) if m["tags"] else [],
                "topic_id": m["topic_id"],
                "status": m["status"],
                "effort_hours": m["effort_hours"],
            }
        )
    return out


def get_stats(conn, goal_id: str | None = None) -> dict[str, Any]:
    """Minimal stats summary: totals {planned_min, done_min, progress,
    materials, revisions_due} plus per-goal effort totals.

    Mirrors the /api/stats derivation (trailing 13 days -> next 14 day
    window) without the expanded goals list or by_day series.
    """
    goals_rows = db.list_goals(conn)
    if goal_id:
        goals_rows = [g for g in goals_rows if g["id"] == goal_id]
        if not goals_rows:
            raise NotFoundError(f"goal {goal_id} not found")
    now = datetime.now(timezone.utc)
    today = now.date()
    start_day = today - timedelta(days=13)
    end_day = today + timedelta(days=14)

    total_planned = 0
    total_done = 0
    revisions_due = 0
    total_materials = 0
    per_goal: list[dict[str, Any]] = []
    for g in goals_rows:
        gp = _goal_progress(conn, g["id"])
        planned_min = 0
        done_min = 0
        rev_due = 0
        for j in db.jobs_for_goal(conn, g["id"]):
            start_date = _date_of(j["slot_start"])
            if start_date < start_day or start_date > end_day:
                continue
            if j["status"] == "pending":
                planned_min += int(j["duration_min"])
                if j["kind"] == "revision":
                    rev_due += 1
            elif j["status"] == "done":
                done_min += int(j["duration_min"])
        mats = [
            m
            for m in db.list_materials(conn)
            if m["goal_id"] == g["id"]
            and m["status"] in ("ready", "tagged", "new")
        ]
        total_materials += len(mats)
        per_goal.append(
            {
                "goal_id": g["id"],
                "title": g["title"],
                "status": g["status"],
                "progress": gp["progress"],
                "total_effort_hours": gp["total_effort_hours"],
                "done_effort_hours": gp["done_effort_hours"],
                "planned_min": planned_min,
                "done_min": done_min,
                "revisions_due": rev_due,
                "materials": len(mats),
            }
        )
        total_planned += planned_min
        total_done += done_min
        revisions_due += rev_due

    total_effort = sum(pg["total_effort_hours"] for pg in per_goal)
    progress = 0.0
    if total_effort > 0:
        progress = min(1.0, (total_done / 60) / total_effort)
    return {
        "totals": {
            "planned_min": total_planned,
            "done_min": total_done,
            "progress": round(progress, 4),
            "materials": total_materials,
            "revisions_due": revisions_due,
        },
        "goals": per_goal,
    }


def set_material_effort(
    conn, material_id: str, hours: float, now_iso: str
) -> dict[str, Any]:
    """Set effort_hours on a material; replan its goal if one is attached.

    Raises NotFoundError if the material is missing.
    """
    validate_effort_hours(float(hours))
    material = db.update_material(conn, material_id, effort_hours=float(hours))
    if material.get("goal_id"):
        planner.replan(conn, material["goal_id"], now_iso)
    return material


def update_settings(
    conn, changes: dict[str, Any], now_iso: str
) -> dict[str, Any]:
    """Bounds-validated settings write (updated_by='llm').

    Accepts optional keys daily_capacity_hours (0.5-12) and slot_minutes
    (15-120). Unknown keys raise ValueError. now_iso is kept for signature
    symmetry with the other write actions; the settings table stamps its own
    updated_at.
    """
    _ = now_iso  # db.update_setting stamps updated_at itself
    allowed = ("daily_capacity_hours", "slot_minutes")
    updated: dict[str, Any] = {}
    for key, value in changes.items():
        if key not in allowed:
            raise ValueError(
                f"unknown setting key {key!r}; allowed keys: {', '.join(allowed)}"
            )
        if key == "daily_capacity_hours":
            updated[key] = validate_capacity_hours(float(value))
        else:
            updated[key] = validate_slot_minutes(int(value))
    for key, value in updated.items():
        db.update_setting(conn, key, json.dumps(value), "llm")
    return {"updated": updated}


# ----------------------------------------------------------- constraints

def _replan_open_goals(conn, now_iso: str) -> int:
    count = 0
    for goal in db.list_goals(conn):
        if goal["status"] == "open":
            planner.replan(conn, goal["id"], now_iso)
            count += 1
    return count


def constraint_out(row: dict) -> dict:
    """One constraint row as the API exposes it."""
    parsed = parse_constraint_row(row)
    if parsed is None:
        # broken row (out-of-band edit): surface it as unparsed instead of crashing
        return {
            "id": row["id"],
            "kind": row["kind"],
            "params": {},
            "hard": bool(row["hard"]),
            "enabled": bool(row["enabled"]),
            "source": row["source"],
            "label": row.get("label") or row["kind"],
            "created_at": row["created_at"],
            "broken": True,
        }
    return {
        "id": parsed["id"],
        "kind": parsed["kind"],
        "params": parsed["params"],
        "hard": parsed["hard"],
        "enabled": bool(row["enabled"]),
        "source": row["source"],
        "label": parsed["label"],
        "created_at": row["created_at"],
    }


def list_constraints(conn) -> list[dict]:
    """All constraints in stable creation order (oldest first)."""
    return [constraint_out(row) for row in db.list_constraints(conn)]


def add_constraint(
    conn,
    *,
    kind: Any,
    params: Any,
    label: Any = None,
    hard: Any = False,
    source: str = "user",
    now_iso: str | None = None,
) -> dict:
    """Validate + persist a constraint, then replan open goals.

    ``params`` may arrive as a dict (chat tools) or a JSON string. ``source``
    is 'user' for the REST API and 'llm' for the chat tool.
    """
    now_iso = now_iso or _now_utc()
    if isinstance(params, str):
        params = json.loads(params or "{}")
    kind_text, params_norm = validate_constraint(kind, params)
    if kind_text == "interleave" and bool(hard):
        raise ValueError("interleave is soft-only; 'hard' is not supported for it")
    params_serialized = serialise_params(params_norm)
    for row in db.list_constraints(conn):
        if row["kind"] == kind_text and row["params"] == params_serialized:
            return {"constraint": constraint_out(row), "replanned_goals": 0, "existing": True}
    label_text = str(label).strip()[:120] if label else constraint_label(kind_text, params_norm)
    constraint_id = db.create_constraint(
        conn,
        kind=kind_text,
        params=params_serialized,
        hard=bool(hard),
        source=source if source in ("user", "llm") else "user",
        label=label_text,
    )
    replanned = _replan_open_goals(conn, now_iso)
    row = next(
        r for r in db.list_constraints(conn) if r["id"] == constraint_id
    )
    return {
        "constraint": constraint_out(row),
        "replanned_goals": replanned,
    }


def remove_constraint(conn, constraint_id: Any, now_iso: str | None = None) -> dict:
    """Delete a constraint by id; replan open goals. Raises NotFoundError."""
    now_iso = now_iso or _now_utc()
    cid = str(constraint_id or "").strip()
    if not cid:
        raise ValueError("constraint_id is required")
    if not db.delete_constraint(conn, cid):
        raise NotFoundError(f"constraint {cid} not found")
    replanned = _replan_open_goals(conn, now_iso)
    return {"deleted": cid, "replanned_goals": replanned}
