"""API tests (FastAPI TestClient) covering docs/API.md happy paths + error codes."""

import os
import tempfile

# Point the app at a fresh temp DB before importing the module (DEFAULT_DB_PATH
# is bound at import time).
_fd, _path = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DYNACALENDAR_DB"] = _path

from fastapi.testclient import TestClient

from app.main import app


def _client():
    return TestClient(app)


def test_health():
    c = _client()
    r = c.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_goals_crud_and_replan():
    c = _client()
    r = c.post("/api/goals", data={"title": "G1", "deadline": "2026-12-18"})
    assert r.status_code == 200, r.text
    body = r.json()
    gid = body["id"]
    assert body["status"] == "open"
    assert body["deadline"] == "2026-12-18T00:00:00Z"

    r = c.get("/api/goals")
    assert r.status_code == 200
    assert any(g["id"] == gid for g in r.json())

    r = c.post(f"/api/goals/{gid}/replan")
    assert r.status_code == 200, r.text
    assert "moved_jobs" in r.json()
    assert "revisions_added" in r.json()


def test_404_on_missing_goal():
    c = _client()
    r = c.post("/api/goals/nope/replan")
    assert r.status_code == 404, r.text


def test_settings_bounds_422():
    c = _client()
    r = c.put("/api/settings", json={"daily_capacity_hours": 999})
    assert r.status_code == 422, r.text
    r = c.put("/api/settings", json={"slot_minutes": 999})
    assert r.status_code == 422, r.text

    # valid update persists
    r = c.put("/api/settings", json={"daily_capacity_hours": 6.0})
    assert r.status_code == 200, r.text
    r = c.get("/api/settings")
    vals = {s["key"]: s["value"] for s in r.json()}
    assert vals["daily_capacity_hours"] == "6.0"


def test_upload_without_files_422():
    c = _client()
    r = c.post("/api/inbox/upload")
    assert r.status_code == 422, r.text


def test_chat_accepts_json_body():
    c = _client()
    r = c.post("/api/chat", json={"message": "what's due this week?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "reply" in body
    assert body["llm"] in ("on", "off")


def test_chat_missing_message_422():
    c = _client()
    r = c.post("/api/chat", json={})
    assert r.status_code == 422, r.text
    r = c.post("/api/chat", json={"message": "   "})
    assert r.status_code == 422, r.text


def test_upload_returns_ready_offline():
    c = _client()
    r = c.post(
        "/api/inbox/upload",
        files={"files": ("notes.md", b"# Notes\n\nSome study notes.", "text/markdown")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1
    assert body[0]["status"] == "ready"
    assert body[0]["material_id"]
    assert body[0]["filename"] == "notes.md"


def test_ics_import_roundtrip():
    c = _client()
    with open("samples/sample_calendar.ics", "rb") as fh:
        r = c.post("/api/ics/import", files={"file": ("sample.ics", fh, "text/calendar")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"] == 10
    assert body["exams_found"] == ["MATH2001 Final Exam"]

    r = c.get("/api/events")
    assert r.status_code == 200
    events = r.json()
    assert len(events) == 10
    assert all(e["kind"] == "external" and e["locked"] == 1 for e in events)


def test_complete_job_atomic_endpoint():
    """Drive the full atomic tick through the API using a seeded temp DB."""
    import os
    import tempfile

    from app import db as _db
    from app.main import app as _app

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = _db.connect(path)
        _db.init_db(conn)
        gid = _db.create_goal(conn, title="G1", deadline="2026-12-18T00:00:00Z")
        mid = _db.create_material(
            conn, goal_id=gid, topic_id=None, filename="m.md",
            original_path="/x/m.md", markdown_path="/x/m.md",
            content_hash="h", summary="s", tags='[]', effort_hours=1.0,
            status="tagged",
        )
        eid = _db.create_event(
            conn, kind="study", title="t",
            dtstart="2026-11-03T14:00:00Z", dtend="2026-11-03T15:00:00Z",
            locked=0,
        )
        jid = _db.create_job(
            conn, event_id=eid, goal_id=gid, material_id=mid,
            kind="learn", duration_min=60, status="pending",
        )
        conn.commit()
        conn.close()

        # Point the running app at this DB by monkeypatching the default path.
        import app.main as main_mod
        main_mod.DEFAULT_DB_PATH = path
        c = TestClient(_app)
        r = c.post(f"/api/jobs/{jid}/complete")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "replanned" in body
        assert "revisions_added" in body
        # job should now be done
        r2 = c.get(f"/api/jobs?from=2026-11-03&to=2026-11-04")
        assert r2.status_code == 200
        jobs = r2.json()
        done = [j for j in jobs if j["id"] == jid]
        assert done and done[0]["status"] == "done"
    finally:
        os.unlink(path)
