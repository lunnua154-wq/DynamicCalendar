"""DynaCalendar FastAPI app — serves the 3 pages and the JSON API.

Routes mirror docs/API.md (M0–M3). The database path defaults to
``data/dynacalendar.db`` (overridable via ``DYNACALENDAR_DB``).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, engine, ics_sync, llm as llm_mod
from .config import DEFAULT_SETTINGS, parse_settings
from .errors import DynaCalendarError

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
DIST_DIR = WEB_DIR / "dist"
DEFAULT_DB_PATH = os.environ.get("DYNACALENDAR_DB", str(BASE_DIR / "data" / "dynacalendar.db"))


def _asset_dir() -> Path:
    return DIST_DIR if DIST_DIR.exists() else WEB_DIR


def _page(name: str) -> Path:
    p = DIST_DIR / name
    return p if p.exists() else WEB_DIR / name


app = FastAPI(title="DynaCalendar")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(DynaCalendarError)
async def _domain_error_handler(_, exc: DynaCalendarError) -> JSONResponse:
    return JSONResponse({"error": exc.message}, status_code=exc.status_code)


# --------------------------------------------------------------------------- db dependency

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


app.mount("/static", StaticFiles(directory=_asset_dir()), name="static")


# --------------------------------------------------------------------------- calendar

@app.get("/api/events")
def list_events(from_: str | None = None, to: str | None = None):
    conn = _get_conn()
    try:
        events = db.list_events(conn, from_, to)
        # enrich study/revision events with their job + material title
        jobs = {j["event_id"]: j for j in db.list_jobs(conn, from_, to)}
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
                    "revision_round": job["revision_round"],
                    "duration_min": job["duration_min"],
                }
                if not item.get("title") and material.get("filename"):
                    item["title"] = material["filename"]
            enriched.append(item)
        return enriched
    finally:
        conn.close()


@app.post("/api/ics/import")
async def ics_import(file: UploadFile = File(...)):
    conn = _get_conn()
    try:
        result = ics_sync.import_ics(file.file, db_path=DEFAULT_DB_PATH)
        return result
    finally:
        conn.close()


# --------------------------------------------------------------------------- goals

@app.get("/api/goals")
def list_goals():
    conn = _get_conn()
    try:
        return db.list_goals(conn)
    finally:
        conn.close()


@app.post("/api/goals")
def create_goal(title: str = Form(...), deadline: str | None = Form(None),
                exam_event_id: str | None = Form(None)):
    if not deadline:
        raise HTTPException(status_code=422, detail="deadline is required")
    if "T" not in deadline:
        deadline = deadline + "T00:00:00Z"
    conn = _get_conn()
    try:
        with conn:
            goal_id = db.create_goal(
                conn, title=title, deadline=deadline, exam_event_id=exam_event_id
            )
            engine.replan(conn, goal_id, _now_utc())
        return db.get_goal(conn, goal_id)
    finally:
        conn.close()


@app.post("/api/goals/{goal_id}/replan")
def replan_goal(goal_id: str):
    conn = _get_conn()
    try:
        with conn:
            db.get_goal(conn, goal_id)  # 404 if missing
            result = engine.replan(conn, goal_id, _now_utc())
        return result
    finally:
        conn.close()


# --------------------------------------------------------------------------- jobs

@app.get("/api/jobs")
def list_jobs(from_: str | None = None, to: str | None = None):
    conn = _get_conn()
    try:
        jobs = db.list_jobs(conn, from_, to)
        materials = {m["id"]: m for m in db.list_materials(conn)}
        for j in jobs:
            m = materials.get(j["material_id"], {})
            j["material_title"] = m.get("filename")
        return jobs
    finally:
        conn.close()


@app.post("/api/jobs/{job_id}/complete")
def complete_job(job_id: str):
    conn = _get_conn()
    try:
        with conn:  # single atomic transaction
            job = db.get_job(conn, job_id)  # 404 if missing
            db.update_job(conn, job_id, status="done", done_at=_now_utc())
            result = engine.replan(conn, job["goal_id"], _now_utc())
        return {
            "replanned": result["moved_jobs"],
            "revisions_added": result["revisions_added"],
        }
    finally:
        conn.close()


@app.post("/api/jobs/{job_id}/move")
def move_job(job_id: str, slot_start: str = Form(...), slot_end: str = Form(...)):
    conn = _get_conn()
    try:
        with conn:
            job = db.get_job(conn, job_id)  # 404 if missing
            new_start = datetime.strptime(slot_start, "%Y-%m-%dT%H:%M:%SZ")
            new_end = datetime.strptime(slot_end, "%Y-%m-%dT%H:%M:%SZ")
            # veto: must not overlap any external (locked) event
            for e in db.list_events(conn):
                if e["kind"] != "external" or not e["locked"]:
                    continue
                es = datetime.strptime(e["dtstart"], "%Y-%m-%dT%H:%M:%SZ")
                ee = datetime.strptime(e["dtend"], "%Y-%m-%dT%H:%M:%SZ")
                if new_start < ee and es < new_end:
                    raise ConflictError(
                        f"can't schedule over external event: {e['title']}"
                    )
            db.update_event(conn, job["event_id"], dtstart=slot_start, dtend=slot_end)
            db.update_job(conn, job_id)
        return db.get_job(conn, job_id)
    finally:
        conn.close()


@app.post("/api/jobs/{job_id}/skip")
def skip_job(job_id: str):
    conn = _get_conn()
    try:
        with conn:
            db.get_job(conn, job_id)  # 404 if missing
            db.update_job(conn, job_id, status="skipped")
            engine.replan(conn, db.get_job(conn, job_id)["goal_id"], _now_utc())
        return db.get_job(conn, job_id)
    finally:
        conn.close()


# --------------------------------------------------------------------------- inbox

@app.post("/api/inbox/upload")
async def inbox_upload(files: list[UploadFile] = File(...)):
    """Upload + ingest files (M0 offline path): save, hash de-dup, convert to
    markdown, tag (offline fallback), create material + topic."""
    from markitdown import MarkItDown

    import hashlib

    conn = _get_conn()
    try:
        settings = parse_settings(db.list_settings(conn))
        client = llm_mod.LLMClient(settings)
        converter = MarkItDown()

        materials_dir = Path(DEFAULT_DB_PATH).parent / "materials"
        materials_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for upload in files:
            filename = upload.filename or "untitled"
            raw = await upload.read()
            content_hash = hashlib.sha256(raw).hexdigest()

            existing = db.find_material_by_hash(conn, content_hash)
            if existing is not None:
                results.append(
                    {
                        "filename": filename,
                        "status": "duplicate",
                        "material_id": existing["id"],
                        "reason": "identical content already ingested",
                    }
                )
                continue

            # save original file
            material_id = db.new_id()
            original_path = materials_dir / f"{material_id}_{filename}"
            original_path.write_bytes(raw)

            # convert to markdown
            try:
                md_result = converter.convert(original_path)
                markdown_text = getattr(md_result, "textcontent", None) or str(md_result)
            except Exception as exc:
                markdown_text = f"# {filename}\n\n(convert error: {exc})"

            markdown_path = materials_dir / f"{material_id}.md"
            markdown_path.write_text(markdown_text, encoding="utf-8")

            # tag (offline fallback when no LLM key)
            tagged = client.tag(markdown=markdown_text, filename=filename)
            tags_json = json.dumps(tagged["tags"])

            # topic: each file its own topic in offline mode (cluster fallback)
            topic_id = db.create_topic(conn, goal_id=None, name=tagged["tags"][0] if tagged["tags"] else filename)

            db.create_material(
                conn,
                goal_id=None,
                topic_id=topic_id,
                filename=filename,
                original_path=str(original_path),
                markdown_path=str(markdown_path),
                content_hash=content_hash,
                summary=tagged["summary"],
                tags=tags_json,
                effort_hours=tagged["effort_hours"],
                status="tagged",
            )
            conn.commit()
            results.append(
                {
                    "filename": filename,
                    "status": "ready",
                    "material_id": material_id,
                    "topic_id": topic_id,
                    "summary": tagged["summary"],
                    "tags": tagged["tags"],
                    "effort_hours": tagged["effort_hours"],
                }
            )
        return results
    finally:
        conn.close()


@app.get("/api/inbox/files")
def list_inbox_files():
    conn = _get_conn()
    try:
        materials = db.list_materials(conn)
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
                }
            )
        return out
    finally:
        conn.close()


@app.post("/api/inbox/files/{material_id}/dismiss")
def dismiss_material(material_id: str, action: str = Form(...)):
    conn = _get_conn()
    try:
        with conn:
            if action not in ("duplicate", "superseded"):
                raise HTTPException(status_code=422, detail="action must be duplicate or superseded")
            db.update_material(conn, material_id, status=action)
        return db.get_material(conn, material_id)
    finally:
        conn.close()


@app.post("/api/inbox/files/{material_id}/effort")
def set_effort(material_id: str, hours: float = Form(...)):
    from .config import validate_effort_hours
    validate_effort_hours(hours)
    conn = _get_conn()
    try:
        with conn:
            db.update_material(conn, material_id, effort_hours=hours)
        return db.get_material(conn, material_id)
    finally:
        conn.close()


# --------------------------------------------------------------------------- chat

@app.post("/api/chat")
def chat(message: str = Form(...)):
    conn = _get_conn()
    try:
        settings = parse_settings(db.list_settings(conn))
        client = llm_mod.LLMClient(settings)
        history = db.list_chat_messages(conn)
        db.add_chat_message(conn, role="user", content=message)
        result = client.chat(message=message, history=[dict(h) for h in history])
        db.add_chat_message(
            conn, role="assistant", content=result["reply"]
        )
        conn.commit()
        return {"reply": result["reply"], "llm": result["llm"]}
    finally:
        conn.close()


# --------------------------------------------------------------------------- settings

@app.get("/api/settings")
def get_settings():
    conn = _get_conn()
    try:
        return db.list_settings(conn)
    finally:
        conn.close()


@app.put("/api/settings")
def put_settings(payload: dict[str, Any]):
    conn = _get_conn()
    try:
        updated = []
        with conn:
            for key, value in payload.items():
                if key == "daily_capacity_hours":
                    from .config import validate_capacity_hours
                    validate_capacity_hours(float(value))
                elif key == "slot_minutes":
                    from .config import validate_slot_minutes
                    validate_slot_minutes(int(value))
                elif key == "default_effort_hours":
                    from .config import validate_effort_hours
                    validate_effort_hours(float(value))
                updated.append(db.update_setting(conn, key, json.dumps(value), "user"))
        return updated
    finally:
        conn.close()


@app.get("/api/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
