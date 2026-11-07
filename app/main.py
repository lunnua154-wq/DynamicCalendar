"""DynaCalendar FastAPI app — serves the 3 pages and the JSON API.

Routes mirror docs/API.md (M0–M3). The database path defaults to
``data/dynacalendar.db`` (overridable via ``DYNACALENDAR_DB``).
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, engine, ics_sync, ingest, llm as llm_mod
from .config import (
    DEFAULT_SETTINGS,
    parse_settings,
    _decode_setting,
    validate_capacity_hours,
    validate_effort_hours,
    validate_llm_base_url,
    validate_llm_model,
    validate_slot_minutes,
    validate_utc_offset_minutes,
)
from .errors import ConflictError, DynaCalendarError, NotFoundError, ValidationError

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
DIST_DIR = WEB_DIR / "dist"
DEFAULT_DB_PATH = os.environ.get("DYNACALENDAR_DB", str(BASE_DIR / "data" / "dynacalendar.db"))


def _asset_dir() -> Path:
    return DIST_DIR if DIST_DIR.exists() else WEB_DIR


def _page(name: str) -> Path:
    """Built page when available, else a page explaining how to build it.

    Without this, a fresh checkout that never ran ``npm run build`` serves raw
    HTML whose module imports (``@fullcalendar/core``) cannot load, so the user
    sees a blank page and a console full of errors.
    """
    built = DIST_DIR / name
    if built.exists():
        return built
    not_built = WEB_DIR / "not-built.html"
    if not_built.exists():
        return not_built
    return WEB_DIR / name


app = FastAPI(title="DynaCalendar")

# The app is served same-origin (FastAPI serves web/dist, and the Vite dev
# server proxies /api), so no cross-origin browser access is expected in
# production. Only the Vite dev origins are allowed, never a wildcard.
_DEV_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_DEV_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(DynaCalendarError)
async def _domain_error_handler(_, exc: DynaCalendarError) -> JSONResponse:
    return JSONResponse({"error": exc.message}, status_code=exc.status_code)


# One handler covers both FastAPI's HTTPException and Starlette's
# (unknown-path 404 / method-not-allowed 405 — e.g. routes not matched).
@app.exception_handler(HTTPException)
@app.exception_handler(StarletteHTTPException)
async def _http_error_handler(_, exc) -> JSONResponse:
    """Normalise FastAPI's/Starlette's ``detail`` into the documented envelope."""
    detail = getattr(exc, "detail", "error")
    return JSONResponse({"error": str(detail)}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(_, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    first = errors[0].get("msg") if errors else "invalid request"
    # jsonable_encoder keeps ctx values (e.g. ValueError instances) serialisable.
    return JSONResponse(
        {"error": str(first), "detail": jsonable_encoder(errors)}, status_code=422
    )


# --------------------------------------------------------------------------- helpers

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_dt(value: Any) -> datetime:
    """Parse an ISO-8601 UTC string (``...Z``, ``+00:00``, ms, or date-only)
    into an aware UTC datetime.

    Raises ``ValidationError`` (mapped to 422) for non-string or garbage input
    instead of leaking a raw ValueError/TypeError as a 500.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("expected an ISO-8601 timestamp string")
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValidationError(f"invalid ISO-8601 timestamp: {value[:80]}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_iso(value: str) -> str:
    """Normalize ``...Z`` / ``+00:00`` / milliseconds / date-only to
    ``%Y-%m-%dT%H:%M:%SZ`` UTC."""
    return _parse_iso_dt(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _window_bound(value: str | None, *, end: bool) -> str | None:
    """Normalise a ``from``/``to`` query parameter for the repository layer.

    The documented contract is a **local calendar day** (``YYYY-MM-DD``) and the
    repository compares ``dtstart < to`` / ``dtend > from``, so a date-only bound
    must expand to the whole day: ``from`` -> 00:00:00Z, ``to`` -> the *next*
    day's 00:00:00Z. Without this the last day of every range was silently
    dropped (a month view lost its final day).
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "T" not in text:
        try:
            day = date.fromisoformat(text[:10])
        except ValueError as exc:
            raise ValidationError(
                f"invalid date: {value} (expected YYYY-MM-DD)"
            ) from exc
        if end:
            day = day + timedelta(days=1)
        return f"{day.isoformat()}T00:00:00Z"
    return _normalize_iso(text)


async def _read_body(request: Request) -> dict[str, Any]:
    """Read a JSON or form-encoded body into a single dict.

    Kept backward compatible: existing callers sending form-encoded bodies
    work unchanged, and JSON bodies (the web client) are accepted too.
    """
    ct = request.headers.get("content-type", "") or ""
    if "application/json" in ct:
        try:
            data = await request.json()
        except Exception as exc:
            raise ValidationError("request body is not valid JSON") from exc
        return data if isinstance(data, dict) else {}
    if "application/x-www-form-urlencoded" in ct or "multipart/form-data" in ct:
        try:
            form = await request.form()
        except Exception as exc:
            raise ValidationError("request body is not valid form data") from exc
        return {k: v for k, v in form.items()}
    # Fallback: try JSON, then form.
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        try:
            form = await request.form()
        except Exception:
            return {}
        return {k: v for k, v in form.items()}


def _goal_progress(conn, goal_id: str) -> dict[str, Any]:
    """progress, total_effort_hours, done_effort_hours, planned_jobs, done_jobs.

    progress = done learn/revision minutes / total material effort hours
    (clamp 0..1; 0 when no effort). Only materials with status in
    (ready, tagged, new) count toward total effort.
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


def _goal_with_progress(conn, goal_id: str) -> dict[str, Any]:
    g = db.get_goal(conn, goal_id)  # 404 if missing
    return {**g, **_goal_progress(conn, goal_id)}


def _build_chat_context(conn, now_iso: str) -> dict[str, Any]:
    now = _parse_iso_dt(now_iso)
    today = now.date()
    end = today + timedelta(days=14)
    from_iso = now_iso
    to_iso = end.strftime("%Y-%m-%dT23:59:59Z")
    settings = parse_settings(db.list_settings(conn))

    goals = []
    for g in db.list_goals(conn):
        if g["status"] != "open":
            continue
        goals.append({**g, **_goal_progress(conn, g["id"])})

    materials = db.list_materials(conn)
    by_id = {m["id"]: m for m in materials}
    jobs = db.list_jobs(conn, from_iso=from_iso, to_iso=to_iso)
    for job in jobs:
        # The offline reply names the work: a custom event title wins, else the
        # material file name (engine events carry an empty title on purpose).
        material = by_id.get(job["material_id"]) or {}
        job["material_title"] = (
            job.get("event_title") or material.get("filename") or "Study job"
        )
        job["title"] = job["material_title"]
    events = db.list_events(conn, from_iso=from_iso, to_iso=to_iso)
    externals = [e for e in events if e["kind"] == "external"]

    return {
        "now": now_iso,
        # Times in the reply are rendered in the user's local clock, matching
        # the calendar (storage stays UTC).
        "utc_offset_minutes": settings.utc_offset_minutes,
        "goals": goals,
        "jobs": jobs,
        "events": externals,
        "materials": [
            {
                "id": m["id"],
                "filename": m["filename"],
                "summary": m["summary"],
                "tags": json.loads(m["tags"]) if m["tags"] else [],
                "topic_id": m["topic_id"],
                "status": m["status"],
                "effort_hours": m["effort_hours"],
            }
            for m in materials[:50]
        ],
    }


# --------------------------------------------------------------------------- db dependency

def _get_conn():
    Path(DEFAULT_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(DEFAULT_DB_PATH)
    db.init_db(conn)
    return conn


# --------------------------------------------------------------------------- pages

@app.get("/")
def calendar_page():
    return FileResponse(_page("calendar.html"))


@app.get("/week")
def week_page():
    return FileResponse(_page("week.html"))


@app.get("/inbox")
def inbox_page():
    return FileResponse(_page("inbox.html"))


# Only mount the built bundle. Mounting ``web/`` when there is no build would
# expose package.json / package-lock.json / node_modules over HTTP.
if DIST_DIR.exists():
    app.mount("/static", StaticFiles(directory=DIST_DIR), name="static")

# Serve hashed Vite build assets at /assets/ (production build path).
_ASSETS_DIR = DIST_DIR / "assets"
if _ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")
# --------------------------------------------------------------------------- calendar

@app.get("/api/events")
def list_events(from_: str | None = Query(None, alias="from"), to: str | None = Query(None, alias="to")):
    conn = _get_conn()
    try:
        start, stop = _window_bound(from_, end=False), _window_bound(to, end=True)
        events = db.list_events(conn, start, stop)
        jobs = {j["event_id"]: j for j in db.list_jobs(conn, start, stop)}
        materials = {m["id"]: m for m in db.list_materials(conn)}
        enriched = []
        for e in events:
            item = dict(e)
            job = jobs.get(e["id"])
            if job:
                material = materials.get(job["material_id"], {})
                item["job"] = {
                    "id": job["id"],
                    "status": job["status"],
                    "kind": job["kind"],
                    "material_id": job["material_id"],
                    "goal_id": job["goal_id"],
                    "revision_round": job["revision_round"],
                    "duration_min": job["duration_min"],
                    "pinned": job["pinned"],
                }
                if not item.get("title") and material.get("filename"):
                    item["title"] = material["filename"]
            enriched.append(item)
        return enriched
    finally:
        conn.close()


@app.post("/api/ics/import")
async def ics_import(file: UploadFile = File(...)):
    # import_ics manages its own connection (all-or-nothing import).
    result = ics_sync.import_ics(file.file, db_path=DEFAULT_DB_PATH)
    # New/changed external events are hard constraints: replan every open
    # goal so pending (and, where the import clobbers them, pinned) jobs
    # never silently overlap the imported schedule.
    conn = _get_conn()
    try:
        with conn:
            open_goals = [g for g in db.list_goals(conn) if g["status"] == "open"]
            for g in open_goals:
                engine.replan(conn, g["id"], _now_utc())
        result = dict(result)
        result["replanned_goals"] = [g["id"] for g in open_goals]
        return result
    finally:
        conn.close()


# --------------------------------------------------------------------------- goals

@app.get("/api/goals")
def list_goals():
    conn = _get_conn()
    try:
        rows = db.list_goals(conn)
        return [{**r, **_goal_progress(conn, r["id"])} for r in rows]
    finally:
        conn.close()


@app.get("/api/goals/{goal_id}")
def get_goal_endpoint(goal_id: str):
    conn = _get_conn()
    try:
        return _goal_with_progress(conn, goal_id)
    finally:
        conn.close()


@app.post("/api/goals")
async def create_goal(request: Request):
    body = await _read_body(request)
    title = body.get("title")
    if not isinstance(title, str) or not title.strip():
        raise HTTPException(status_code=422, detail="title is required")
    title = title.strip()
    deadline = body.get("deadline")
    exam_event_id = body.get("exam_event_id") or None
    conn = _get_conn()
    try:
        if not deadline:
            if not exam_event_id:
                raise HTTPException(
                    status_code=422, detail="deadline or exam_event_id is required"
                )
            event = db.get_event(conn, exam_event_id)  # 404 if missing
            if event["kind"] != "external":
                raise HTTPException(
                    status_code=422, detail="exam_event_id must reference an external event"
                )
            # Derive the deadline from the external event's DTSTART (date, UTC midnight).
            deadline = f"{event['dtstart'][:10]}T00:00:00Z"
        else:
            deadline = _normalize_iso(deadline)
        with conn:
            goal_id = db.create_goal(
                conn, title=title, deadline=deadline, exam_event_id=exam_event_id
            )
            engine.replan(conn, goal_id, _now_utc())
        return _goal_with_progress(conn, goal_id)
    finally:
        conn.close()


@app.post("/api/goals/{goal_id}/replan")
def replan_goal(goal_id: str):
    conn = _get_conn()
    try:
        with conn:
            db.get_goal(conn, goal_id)  # 404 if missing
            result = engine.replan(conn, goal_id, _now_utc())
        return {
            "moved_jobs": result["moved_jobs"],
            "revisions_added": result["revisions_added"],
            "moved_count": len(result["moved_jobs"]),
        }
    finally:
        conn.close()


# --------------------------------------------------------------------------- jobs

@app.get("/api/jobs")
def list_jobs(from_: str | None = Query(None, alias="from"), to: str | None = Query(None, alias="to")):
    conn = _get_conn()
    try:
        jobs = db.list_jobs(conn, _window_bound(from_, end=False), _window_bound(to, end=True))
        materials = {m["id"]: m for m in db.list_materials(conn)}
        for j in jobs:
            m = materials.get(j["material_id"], {})
            title = m.get("filename") or "Study job"
            j["material_title"] = title
            j["title"] = title
        return jobs
    finally:
        conn.close()


@app.post("/api/jobs/{job_id}/complete")
def complete_job(job_id: str):
    conn = _get_conn()
    try:
        with conn:  # single atomic transaction
            job = db.get_job(conn, job_id)  # 404 if missing
            if job["status"] == "done":
                # Idempotent: ticking an already-done job does nothing.
                return {"replanned": [], "revisions_added": []}
            if job["status"] in ("skipped", "conflicted"):
                raise ConflictError(f"cannot complete a {job['status']} job")
            db.update_job(conn, job_id, status="done", done_at=_now_utc())
            result = engine.replan(conn, job["goal_id"], _now_utc())
        return {
            "replanned": result["moved_jobs"],
            "revisions_added": result["revisions_added"],
        }
    finally:
        conn.close()


@app.post("/api/jobs/{job_id}/move")
async def move_job(job_id: str, request: Request):
    body = await _read_body(request)
    slot_start = body.get("slot_start")
    slot_end = body.get("slot_end")
    if not slot_start or not slot_end:
        raise HTTPException(
            status_code=422, detail="slot_start and slot_end are required"
        )
    new_start = _normalize_iso(slot_start)
    new_end = _normalize_iso(slot_end)
    if new_end <= new_start:
        raise ValidationError("slot_end must be after slot_start")
    conn = _get_conn()
    try:
        with conn:
            job = db.get_job(conn, job_id)  # 404 if missing
            if job["status"] in ("done", "skipped"):
                raise ConflictError(f"cannot move a {job['status']} job")
            was_conflicted = job["status"] == "conflicted"
            now_str = _now_utc()
            if new_start < now_str:
                raise ConflictError("new slot is in the past")
            goal = db.get_goal(conn, job["goal_id"])
            if new_start[:10] > goal["deadline"][:10]:
                raise ConflictError("new slot is after the goal deadline")
            # Veto: must not overlap any locked external event.
            for e in db.list_events(conn):
                if e["kind"] != "external" or not e["locked"]:
                    continue
                if new_start < e["dtend"] and e["dtstart"] < new_end:
                    raise ConflictError(
                        f"can't schedule over external event: {e['title']}"
                    )
            # Veto: must not overlap another job (done/pending occupy time).
            for j in db.list_jobs(conn):
                if j["id"] == job_id:
                    continue
                if j["status"] not in ("pending", "done"):
                    continue
                if new_start < j["slot_end"] and j["slot_start"] < new_end:
                    raise ConflictError(f"new slot overlaps job {j['id']}")
            db.update_event(conn, job["event_id"], dtstart=new_start, dtend=new_end)
            # A conflicted job explicitly moved by the user becomes a normal
            # pending job again (pinned); otherwise it stays conflicted
            # forever and its revision round is silently suppressed.
            job_updates = {"pinned": 1}
            if was_conflicted:
                job_updates["status"] = "pending"
            db.update_job(conn, job_id, **job_updates)
            result = engine.replan(conn, job["goal_id"], now_str)
        return {"job": db.get_job(conn, job_id), "moved_jobs": result["moved_jobs"]}
    finally:
        conn.close()


@app.post("/api/jobs/{job_id}/skip")
def skip_job(job_id: str):
    conn = _get_conn()
    try:
        with conn:
            job = db.get_job(conn, job_id)  # 404 if missing
            if job["status"] == "done":
                raise ConflictError("cannot skip a done job")
            db.update_job(conn, job_id, status="skipped")
            engine.replan(conn, job["goal_id"], _now_utc())
        return db.get_job(conn, job_id)
    finally:
        conn.close()


# --------------------------------------------------------------------------- inbox

@app.post("/api/inbox/upload")
async def inbox_upload(
    files: list[UploadFile] = File(...),
    goal_id: str = Form(None),
):
    """Upload + ingest files: store_upload() + process_material() per file,
    then one engine.replan() for the goal if goal_id is supplied."""
    goal_id = goal_id or None
    conn = _get_conn()
    try:
        settings = parse_settings(db.list_settings(conn))
        client = llm_mod.LLMClient(settings)
        materials_dir = Path(DEFAULT_DB_PATH).parent / "materials"
        materials_dir.mkdir(parents=True, exist_ok=True)

        results = []
        ready_material_ids = []
        for upload in files:
            filename = upload.filename or "untitled"
            raw = await upload.read()
            stored = ingest.store_upload(
                conn,
                raw=raw,
                filename=filename,
                goal_id=None,  # attach to goal only after a file is ready
                originals_dir=materials_dir,
            )
            if stored["status"] == "duplicate":
                material = db.get_material(conn, stored["material_id"])
                topic_name = None
                if material["topic_id"]:
                    row = conn.execute(
                        "SELECT name FROM topics WHERE id = ?", (material["topic_id"],)
                    ).fetchone()
                    topic_name = row["name"] if row else None
                results.append(
                    {
                        "filename": filename,
                        "status": "duplicate",
                        "material_id": stored["material_id"],
                        "topic_id": material["topic_id"],
                        "topic_name": topic_name,
                        "summary": material["summary"],
                        "tags": (
                            json.loads(material["tags"]) if material["tags"] else []
                        ),
                        "effort_hours": material["effort_hours"],
                        "duplicate_of": stored["duplicate_of"],
                        "status_reason": material["status_reason"],
                        "cluster_relation": "duplicate",
                        "cluster_reason": "identical content already ingested",
                    }
                )
                continue

            material = ingest.process_material(
                conn,
                stored["material_id"],
                markdown_dir=materials_dir,
                client=client,
                settings=settings,
                now_iso=_now_utc(),
            )
            results.append(
                {
                    "filename": material["filename"],
                    "status": material["status"],
                    "material_id": material["id"],
                    "topic_id": material["topic_id"],
                    "topic_name": material.get("topic_name"),
                    "summary": material["summary"],
                    "tags": (
                        json.loads(material["tags"]) if material["tags"] else []
                    ),
                    "effort_hours": material["effort_hours"],
                    "duplicate_of": material["duplicate_of"],
                    "status_reason": material["status_reason"],
                    "cluster_relation": material.get("cluster_relation", "new"),
                    "cluster_reason": material.get("cluster_reason", ""),
                }
            )
            if material["status"] == "ready":
                ready_material_ids.append(material["id"])

        if goal_id:
            with conn:
                # Attach only ready materials (skip duplicate/error) and replan once.
                for mid in ready_material_ids:
                    db.update_material(conn, mid, goal_id=goal_id)
                engine.replan(conn, goal_id, _now_utc())
        return results
    finally:
        conn.close()


@app.get("/api/inbox/files")
def list_inbox_files():
    conn = _get_conn()
    try:
        materials = db.list_materials(conn)
        topics = {t["id"]: t["name"] for t in db.list_topics(conn)}
        out = []
        for m in materials:
            out.append(
                {
                    "id": m["id"],
                    "filename": m["filename"],
                    "summary": m["summary"],
                    "tags": json.loads(m["tags"]) if m["tags"] else [],
                    "effort_hours": m["effort_hours"],
                    "status": m["status"],
                    "duplicate_of": m["duplicate_of"],
                    "topic_id": m["topic_id"],
                    "topic_name": topics.get(m["topic_id"]) if m["topic_id"] else None,
                    "status_reason": m["status_reason"],
                    "goal_id": m["goal_id"],
                }
            )
        return out
    finally:
        conn.close()


@app.post("/api/inbox/files/{material_id}/dismiss")
async def dismiss_material(material_id: str, request: Request):
    body = await _read_body(request)
    action = body.get("action")
    if action not in ("duplicate", "superseded", "new"):
        raise HTTPException(
            status_code=422,
            detail="action must be duplicate, superseded or new",
        )
    # "new" undoes a duplicate: the material is fully processed, so mark it ready.
    status = "ready" if action == "new" else action
    conn = _get_conn()
    try:
        with conn:
            material = db.get_material(conn, material_id)  # 404 if missing
            db.update_material(conn, material_id, status=status)
            if material["goal_id"]:
                engine.replan(conn, material["goal_id"], _now_utc())
        return db.get_material(conn, material_id)
    finally:
        conn.close()


@app.post("/api/inbox/files/{material_id}/effort")
async def set_effort(material_id: str, request: Request):
    body = await _read_body(request)
    hours = body.get("hours")
    if hours is None:
        raise HTTPException(status_code=422, detail="hours is required")
    hours = _as_float(hours, "hours")
    validate_effort_hours(hours)
    conn = _get_conn()
    try:
        with conn:
            material = db.get_material(conn, material_id)  # 404 if missing
            db.update_material(conn, material_id, effort_hours=hours)
            if material["goal_id"]:
                engine.replan(conn, material["goal_id"], _now_utc())
        return db.get_material(conn, material_id)
    finally:
        conn.close()


@app.post("/api/inbox/files/{material_id}/attach")
async def attach_file(material_id: str, request: Request):
    body = await _read_body(request)
    goal_id = body.get("goal_id")
    if not isinstance(goal_id, str) or not goal_id.strip():
        raise HTTPException(status_code=422, detail="goal_id is required")
    goal_id = goal_id.strip()
    conn = _get_conn()
    try:
        with conn:
            db.get_goal(conn, goal_id)  # 404 if missing — checked before any FK fires
            material = db.get_material(conn, material_id)  # 404 if missing
            if material["status"] != "ready":
                raise HTTPException(
                    status_code=422,
                    detail=f"material is not ready (status={material['status']})",
                )
            old_goal_id = material["goal_id"]
            db.update_material(conn, material_id, goal_id=goal_id)
            goals_to_replan = {goal_id}
            if old_goal_id and old_goal_id != goal_id:
                goals_to_replan.add(old_goal_id)
            for gid in goals_to_replan:
                engine.replan(conn, gid, _now_utc())
        return db.get_material(conn, material_id)
    finally:
        conn.close()


# --------------------------------------------------------------------------- topics

def _topic_out(t: dict[str, Any], members: list[dict[str, Any]]) -> dict[str, Any]:
    """The documented topic shape (docs/API.md)."""
    return {
        "id": t["id"],
        "name": t["name"],
        "goal_id": t["goal_id"],
        "created_at": t["created_at"],
        "file_count": len(members),
        "members": [
            {
                "id": m["id"],
                "filename": m["filename"],
                "status": m["status"],
                "effort_hours": m["effort_hours"],
            }
            for m in members
        ],
    }


@app.get("/api/topics")
def list_topics_endpoint():
    conn = _get_conn()
    try:
        topics = db.list_topics(conn)
        materials = db.list_materials(conn)
        by_topic: dict[str, list[dict[str, Any]]] = {}
        for m in materials:
            if m["topic_id"]:
                by_topic.setdefault(m["topic_id"], []).append(m)
        return [_topic_out(t, by_topic.get(t["id"], [])) for t in topics]
    finally:
        conn.close()


@app.post("/api/topics/{topic_id}/rename")
async def rename_topic(topic_id: str, request: Request):
    body = await _read_body(request)
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=422, detail="name is required")
    name = name.strip()
    conn = _get_conn()
    try:
        with conn:
            db.get_topic(conn, topic_id)  # 404 if missing
            db.update_topic(conn, topic_id, name=name)
        return db.get_topic(conn, topic_id)
    finally:
        conn.close()


@app.post("/api/topics/merge")
async def merge_topics_endpoint(request: Request):
    body = await _read_body(request)
    source_id = body.get("source_id")
    target_id = body.get("target_id")
    if not isinstance(source_id, str) or not isinstance(target_id, str):
        raise HTTPException(
            status_code=422, detail="source_id and target_id must be strings"
        )
    if not source_id or not target_id:
        raise HTTPException(
            status_code=422, detail="source_id and target_id are required"
        )
    if source_id == target_id:
        raise HTTPException(
            status_code=422, detail="source_id and target_id must differ"
        )
    conn = _get_conn()
    try:
        with conn:
            db.get_topic(conn, source_id)  # 404 if missing
            db.get_topic(conn, target_id)  # 404 if missing
            affected: set[str] = set()
            for r in conn.execute(
                "SELECT goal_id FROM materials WHERE topic_id = ?", (source_id,)
            ).fetchall():
                if r["goal_id"]:
                    affected.add(r["goal_id"])
            for t in (db.get_topic(conn, source_id), db.get_topic(conn, target_id)):
                if t["goal_id"]:
                    affected.add(t["goal_id"])
            ingest.merge_topics(
                conn,
                source_topic_id=source_id,
                target_topic_id=target_id,
                now_iso=_now_utc(),
            )
            for gid in affected:
                engine.replan(conn, gid, _now_utc())
            topic_row = db.get_topic(conn, target_id)
            members = [
                m
                for m in db.list_materials(conn)
                if m["topic_id"] == target_id
            ]
            return _topic_out(topic_row, members)
    finally:
        conn.close()


# --------------------------------------------------------------------------- chat

@app.post("/api/chat")
async def chat(request: Request):
    body = await _read_body(request)
    message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        raise HTTPException(status_code=422, detail="message is required")
    conn = _get_conn()
    try:
        settings = parse_settings(db.list_settings(conn))
        client = llm_mod.LLMClient(settings)
        history = db.list_chat_messages(conn)
        context = _build_chat_context(conn, _now_utc())
        now_iso = _now_utc()
        with conn:  # single transaction: tool actions (settings/effort) + messages
            result = client.chat(
                message=message,
                history=[dict(h) for h in history],
                context=context,
                conn=conn,
                now_iso=now_iso,
            )
            reply = result["reply"]
            llm = result.get("llm", "off")
            db.add_chat_message(conn, role="user", content=message)
            db.add_chat_message(conn, role="assistant", content=reply)
        out = {"reply": reply, "llm": llm}
        if result.get("actions"):
            out["actions"] = result["actions"]
        if result.get("llm_error"):
            out["llm_error"] = result["llm_error"]
        return out
    finally:
        conn.close()


@app.get("/api/chat/messages")
def list_chat_messages_endpoint():
    conn = _get_conn()
    try:
        return db.list_chat_messages(conn)
    finally:
        conn.close()


# --------------------------------------------------------------------------- llm diagnostics

def _llm_client_from_settings(conn) -> llm_mod.LLMClient:
    return llm_mod.LLMClient(parse_settings(db.list_settings(conn)))


@app.get("/api/llm/status")
def llm_status():
    """What the app thinks the LLM configuration is, plus the last failure."""
    conn = _get_conn()
    try:
        settings = parse_settings(db.list_settings(conn))
        return {
            "configured": settings.llm_enabled,
            "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "api_key_set": bool(settings.llm_api_key),
            "last_error": llm_mod.last_error(),
        }
    finally:
        conn.close()


@app.post("/api/llm/test")
def llm_test():
    """Send one tiny completion to the configured endpoint (diagnostic).

    Always 200 with ``{ok: bool, ...}``: a failure here is an answer, not a
    server error. ``kind`` is one of auth | not_found | connection | timeout |
    http_error | bad_request | rate_limit | bad_config | not_configured | unknown.
    """
    conn = _get_conn()
    try:
        client = _llm_client_from_settings(conn)
    finally:
        conn.close()
    return client.check_connection()


# --------------------------------------------------------------------------- stats

@app.get("/api/stats")
def stats(goal_id: str | None = None):
    conn = _get_conn()
    try:
        now = _parse_iso_dt(_now_utc())
        today = now.date()
        # by_day covers the trailing 13 days plus today and the next 14, so
        # the week view can show Mon–Sun data and the month grid can shade
        # upcoming days. Jobs outside the window are ignored for by_day/totals.
        start = today - timedelta(days=13)
        end = today + timedelta(days=14)

        goals_rows = db.list_goals(conn)
        if goal_id:
            goals_rows = [g for g in goals_rows if g["id"] == goal_id]
            if not goals_rows:
                raise NotFoundError(f"goal {goal_id} not found")

        by_day: dict[str, dict[str, Any]] = {}
        d = start
        while d <= end:
            by_day[d.isoformat()] = {
                "date": d.isoformat(),
                "planned_min": 0,
                "done_min": 0,
            }
            d += timedelta(days=1)

        total_planned = 0
        total_done = 0
        total_materials = 0
        revisions_due = 0
        per_goal = []
        for g in goals_rows:
            gp = _goal_progress(conn, g["id"])
            jobs = db.jobs_for_goal(conn, g["id"])
            planned_min = 0
            done_min = 0
            rev_due = 0
            for j in jobs:
                start_date = _parse_iso_dt(j["slot_start"]).date()
                if start_date > end or start_date < start:
                    continue
                if j["status"] == "pending":
                    planned_min += int(j["duration_min"])
                    if j["kind"] == "revision":
                        rev_due += 1
                    by_day[start_date.isoformat()]["planned_min"] += int(
                        j["duration_min"]
                    )
                elif j["status"] == "done":
                    done_min += int(j["duration_min"])
                    by_day[start_date.isoformat()]["done_min"] += int(
                        j["duration_min"]
                    )
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
                    "planned_jobs": gp["planned_jobs"],
                    "done_jobs": gp["done_jobs"],
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
            "by_day": list(by_day.values()),
        }
    finally:
        conn.close()


# --------------------------------------------------------------------------- settings

def _as_float(value: Any, key: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{key} must be a number")


def _as_int(value: Any, key: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{key} must be an integer")


def _validate_revision_intervals_days(value: Any) -> list[int]:
    if not isinstance(value, list) or not (1 <= len(value) <= 6):
        raise ValidationError("revision_intervals_days must be a list of 1-6 ints")
    out = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, int) or not (1 <= v <= 60):
            raise ValidationError("revision_intervals_days items must be ints 1-60")
        out.append(v)
    # Rounds are applied in list order; a caller-supplied unsorted list would
    # otherwise schedule round 1 after round 2.
    return sorted(out)


def _validate_ocr_provider(value: Any) -> str:
    if value not in ("off", "cloudflare"):
        raise ValidationError("ocr_provider must be 'off' or 'cloudflare'")
    return value


def _validate_ocr_min_chars(value: Any) -> int:
    v = _as_int(value, "ocr_min_chars")
    if not (1 <= v <= 10000):
        raise ValidationError("ocr_min_chars must be between 1 and 10000")
    return v


# Keys whose values must never leave the server. The web client only needs
# to know whether they are configured (the modal shows an empty field).
_SENSITIVE_SETTING_KEYS = {"llm_api_key", "cf_api_token"}


def _public_setting(row: dict[str, Any]) -> dict[str, Any]:
    """One settings row as the API may expose it (credentials stay write-only)."""
    item = dict(row)
    if item["key"] in _SENSITIVE_SETTING_KEYS:
        item["configured"] = bool(_decode_setting(item["value"]))
        item["value"] = ""
    else:
        try:
            item["value"] = json.loads(item["value"])
        except (json.JSONDecodeError, TypeError):
            pass  # leave the raw string
    return item


@app.get("/api/settings")
def get_settings():
    conn = _get_conn()
    try:
        return [_public_setting(row) for row in db.list_settings(conn)]
    finally:
        conn.close()


@app.put("/api/settings")
async def put_settings(request: Request):
    body = await _read_body(request)
    # Validate every known key BEFORE writing anything (leave DB unchanged on 422).
    parsed: dict[str, Any] = {}
    for key, value in body.items():
        if key == "daily_capacity_hours":
            parsed[key] = validate_capacity_hours(_as_float(value, key))
        elif key == "slot_minutes":
            parsed[key] = validate_slot_minutes(_as_int(value, key))
        elif key == "min_slot_minutes":
            parsed[key] = validate_slot_minutes(_as_int(value, key))
        elif key == "default_effort_hours":
            parsed[key] = validate_effort_hours(_as_float(value, key))
        elif key == "revision_intervals_days":
            parsed[key] = _validate_revision_intervals_days(value)
        elif key == "utc_offset_minutes":
            parsed[key] = validate_utc_offset_minutes(_as_int(value, key))
        elif key == "llm_base_url":
            parsed[key] = validate_llm_base_url(str(value))
        elif key == "llm_model":
            parsed[key] = validate_llm_model(str(value))
        elif key == "llm_api_key":
            parsed[key] = str(value or "").strip()
        elif key == "ocr_provider":
            parsed[key] = _validate_ocr_provider(value)
        elif key == "ocr_min_chars":
            parsed[key] = _validate_ocr_min_chars(value)
        else:
            # Unknown keys (e.g. cf_* credentials) are stored as-is.
            parsed[key] = value
    conn = _get_conn()
    try:
        with conn:
            updated = [
                db.update_setting(conn, k, json.dumps(v), "user")
                for k, v in parsed.items()
            ]
            settings = parse_settings(db.list_settings(conn))
            # A shorter/longer study window changes the plan: recompute now so
            # the calendar never shows jobs outside the configured capacity.
            for goal in db.list_goals(conn):
                if goal["status"] == "open":
                    engine.replan(conn, goal["id"], _now_utc())
        _ = settings  # parsed to validate the stored configuration
        return [_public_setting(row) for row in updated]
    finally:
        conn.close()


@app.get("/api/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
