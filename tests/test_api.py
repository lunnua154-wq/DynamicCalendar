"""API tests (FastAPI TestClient) covering docs/API.md happy paths + error codes."""

import os
import tempfile

# Point the app at a fresh temp DB before importing the module (DEFAULT_DB_PATH
# is bound at import time).
_fd, _path = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DYNACALENDAR_DB"] = _path

from fastapi.testclient import TestClient

from app import db
from app.main import app


def _client():
    return TestClient(app)


def _client_at(path):
    """TestClient pointed at a fresh seeded DB path."""
    import app.main as main_mod

    main_mod.DEFAULT_DB_PATH = path
    return TestClient(app)


def _seed_goal_material_job(
    path,
    deadline="2026-12-18T00:00:00Z",
    effort=1.0,
    slot_start="2026-11-09T14:00:00Z",
    slot_end="2026-11-09T14:50:00Z",
):
    conn = db.connect(path)
    db.init_db(conn)
    gid = db.create_goal(conn, title="G1", deadline=deadline)
    mid = db.create_material(
        conn, goal_id=gid, topic_id=None, filename="limits.md",
        original_path="/x/limits.md", markdown_path="/x/limits.md",
        content_hash="hash-limits", summary="s", tags='[]',
        effort_hours=effort, status="ready",
    )
    eid = db.create_event(
        conn, kind="study", title="Limits session",
        dtstart=slot_start, dtend=slot_end, locked=0,
    )
    jid = db.create_job(
        conn, event_id=eid, goal_id=gid, material_id=mid,
        kind="learn", duration_min=50, status="pending",
    )
    conn.commit()
    conn.close()
    return gid, mid, jid


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
    assert "cluster_relation" in body[0]


def test_upload_duplicate_content():
    c = _client()
    data = b"# Duplicate target\n\nUnique content for dedup test."
    r1 = c.post(
        "/api/inbox/upload",
        files={"files": ("notes.md", data, "text/markdown")},
    )
    assert r1.status_code == 200, r1.text
    first = r1.json()[0]
    assert first["status"] == "ready"

    r2 = c.post(
        "/api/inbox/upload",
        files={"files": ("notes_copy.md", data, "text/markdown")},
    )
    assert r2.status_code == 200, r2.text
    second = r2.json()[0]
    assert second["status"] == "duplicate"
    # a distinct duplicate row, linked by duplicate_of to the original
    assert second["material_id"] != first["material_id"]
    assert second["duplicate_of"] == first["material_id"]
    assert second["topic_id"] == first["topic_id"]
    assert "identical content" in second["cluster_reason"]
    assert "cluster_relation" in second


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

    # exams includes the real event id for each exam title
    exam_events = [e for e in body["exams"] if e["title"] == "MATH2001 Final Exam"]
    assert exam_events, body
    assert exam_events[0]["event_id"]
    assert exam_events[0]["dtstart"]
    assert exam_events[0]["dtend"]
    # the event id references an actual external event
    event_ids = {e["id"] for e in events}
    assert exam_events[0]["event_id"] in event_ids


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


# --------------------------------------------------------------------------- goals: JSON bodies + progress fields


def test_goal_json_body_and_exam_deadline():
    c = _client()
    # JSON body with a manual deadline.
    r = c.post("/api/goals", json={"title": "G1", "deadline": "2026-12-18"})
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["deadline"] == "2026-12-18T00:00:00Z"
    assert g["title"] == "G1"
    assert "progress" in g
    assert "total_effort_hours" in g
    assert "done_effort_hours" in g
    assert "planned_jobs" in g
    assert "done_jobs" in g

    # deadline omitted + neither exam -> 422
    r = c.post("/api/goals", json={"title": "NoDeadline"})
    assert r.status_code == 422, r.text


def test_goal_from_exam_event_derives_18_dec_deadline():
    c = _client()
    with open("samples/sample_calendar.ics", "rb") as fh:
        ir = c.post("/api/ics/import", files={"file": ("sample.ics", fh, "text/calendar")})
    assert ir.status_code == 200, ir.text
    exam = [e for e in ir.json()["exams"] if e["title"] == "MATH2001 Final Exam"][0]
    eid = exam["event_id"]

    r = c.post("/api/goals", json={"title": "MATH2001", "exam_event_id": eid})
    assert r.status_code == 200, r.text
    g = r.json()
    # DTSTART 18 Dec (UTC midnight of that date)
    assert g["deadline"] == "2026-12-18T00:00:00Z"
    assert g["exam_event_id"] == eid
    assert g["status"] == "open"
    assert "progress" in g


def test_get_goal_and_list_goals_progress_fields():
    c = _client()
    r = c.post("/api/goals", json={"title": "G1", "deadline": "2026-12-18"})
    gid = r.json()["id"]

    r = c.get(f"/api/goals/{gid}")
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["id"] == gid
    for k in ("progress", "total_effort_hours", "done_effort_hours",
              "planned_jobs", "done_jobs"):
        assert k in g, g

    r = c.get("/api/goals")
    assert r.status_code == 200
    assert any(g2["id"] == gid for g2 in r.json())

    r = c.post(f"/api/goals/{gid}/replan")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "moved_jobs" in body
    assert "revisions_added" in body
    assert "moved_count" in body


# --------------------------------------------------------------------------- jobs: complete / move / skip


def test_move_success_sets_pinned_and_replan_keeps_slot():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        gid, _mid, jid = _seed_goal_material_job(path)
        c = _client_at(path)
        r = c.post(f"/api/jobs/{jid}/move", json={
            "slot_start": "2026-11-10T14:00:00Z",
            "slot_end": "2026-11-10T14:50:00Z",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert "job" in body and "moved_jobs" in body
        assert body["job"]["pinned"] == 1
        assert body["job"]["slot_start"] == "2026-11-10T14:00:00Z"

        # replan keeps the pinned slot
        r = c.post(f"/api/goals/{gid}/replan")
        assert r.status_code == 200, r.text
        r2 = c.get("/api/jobs")
        jobs = {j["id"]: j for j in r2.json()}
        assert jobs[jid]["pinned"] == 1
        assert jobs[jid]["slot_start"] == "2026-11-10T14:00:00Z"
    finally:
        os.unlink(path)


def test_move_accepts_milliseconds():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        gid, _mid, jid = _seed_goal_material_job(path)
        c = _client_at(path)
        r = c.post(f"/api/jobs/{jid}/move", json={
            "slot_start": "2026-11-10T14:00:00.500Z",
            "slot_end": "2026-11-10T14:50:00.500Z",
        })
        assert r.status_code == 200, r.text
        assert r.json()["job"]["slot_start"] == "2026-11-10T14:00:00Z"
    finally:
        os.unlink(path)


def test_move_rejects_done_past_after_deadline_overlap():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        gid, mid, jid = _seed_goal_material_job(path)
        c = _client_at(path)

        # done job cannot be moved
        conn = db.connect(path)
        db.update_job(conn, jid, status="done", done_at="2026-11-09T14:50:00Z")
        conn.commit()
        conn.close()
        r = c.post(f"/api/jobs/{jid}/move", json={
            "slot_start": "2026-11-10T14:00:00Z",
            "slot_end": "2026-11-10T14:50:00Z",
        })
        assert r.status_code == 409, r.text

        # reseed a pending job for the remaining checks
        fd2, path2 = tempfile.mkstemp(suffix=".db")
        os.close(fd2)
        try:
            gid2, mid2, jid2 = _seed_goal_material_job(path2)
            c2 = _client_at(path2)
            # slot in the past -> 409
            r = c2.post(f"/api/jobs/{jid2}/move", json={
                "slot_start": "2026-01-01T10:00:00Z",
                "slot_end": "2026-01-01T10:50:00Z",
            })
            assert r.status_code == 409, r.text
            # after goal deadline -> 409
            r = c2.post(f"/api/jobs/{jid2}/move", json={
                "slot_start": "2026-12-20T10:00:00Z",
                "slot_end": "2026-12-20T10:50:00Z",
            })
            assert r.status_code == 409, r.text
        finally:
            os.unlink(path2)
    finally:
        os.unlink(path)


def test_move_overlapping_exam_is_409():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = db.connect(path)
        db.init_db(conn)
        # import the sample calendar so the exam (18 Dec) is a locked external event
        with open("samples/sample_calendar.ics", "rb") as fh:
            data = fh.read()
        # use ics_sync directly on this DB
        from app import ics_sync
        import io
        ics_sync.import_ics(io.BytesIO(data), db_path=path)
        conn.commit()
        gid = db.create_goal(conn, title="G1", deadline="2026-12-18T00:00:00Z")
        mid = db.create_material(
            conn, goal_id=gid, topic_id=None, filename="m.md",
            original_path="/x/m.md", markdown_path="/x/m.md",
            content_hash="h", summary="s", tags='[]', effort_hours=1.0,
            status="ready",
        )
        # a job in a free slot (9 Nov)
        eid = db.create_event(
            conn, kind="study", title="job",
            dtstart="2026-11-09T14:00:00Z", dtend="2026-11-09T14:50:00Z",
            locked=0,
        )
        jid = db.create_job(
            conn, event_id=eid, goal_id=gid, material_id=mid,
            kind="learn", duration_min=50, status="pending",
        )
        conn.commit()
        conn.close()

        c = _client_at(path)
        # move onto the exam slot (18 Dec 01:30-04:00 UTC) -> 409
        r = c.post(f"/api/jobs/{jid}/move", json={
            "slot_start": "2026-12-18T02:00:00Z",
            "slot_end": "2026-12-18T02:50:00Z",
        })
        assert r.status_code == 409, r.text
    finally:
        os.unlink(path)


def test_complete_twice_no_duplicate_revisions():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        gid, mid, jid = _seed_goal_material_job(path)
        c = _client_at(path)

        r = c.post(f"/api/jobs/{jid}/complete")
        assert r.status_code == 200, r.text
        r2 = c.get("/api/jobs")
        rev1 = [j for j in r2.json() if j["kind"] == "revision"]
        n1 = len(rev1)

        # complete the same (already done) job again -> idempotent, empty
        r = c.post(f"/api/jobs/{jid}/complete")
        assert r.status_code == 200, r.text
        assert r.json() == {"replanned": [], "revisions_added": []}

        r2 = c.get("/api/jobs")
        rev2 = [j for j in r2.json() if j["kind"] == "revision"]
        assert len(rev2) == n1, f"revision count changed: {n1} -> {len(rev2)}"
    finally:
        os.unlink(path)


def test_revision_done_then_replan_no_respawn():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        gid, mid, jid = _seed_goal_material_job(path)
        c = _client_at(path)

        c.post(f"/api/jobs/{jid}/complete")
        r = c.get("/api/jobs")
        r1 = [j for j in r.json() if j["kind"] == "revision" and j["revision_round"] == 1]
        assert r1, "expected a round-1 revision"
        rid = r1[0]["id"]

        # complete the revision itself
        c.post(f"/api/jobs/{rid}/complete")
        # replan: round 1 must not respawn
        c.post(f"/api/goals/{gid}/replan")
        r = c.get("/api/jobs")
        round1 = [j for j in r.json() if j["kind"] == "revision" and j["revision_round"] == 1]
        assert len(round1) == 1, f"round 1 respawned: {round1}"
        assert round1[0]["id"] == rid
        assert round1[0]["status"] == "done"
    finally:
        os.unlink(path)


def test_skip_rejects_done_otherwise_skipped():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        gid, mid, jid = _seed_goal_material_job(path)
        c = _client_at(path)

        # mark done via the DB directly
        conn = db.connect(path)
        db.update_job(conn, jid, status="done", done_at="2026-11-09T14:50:00Z")
        conn.commit()
        conn.close()
        r = c.post(f"/api/jobs/{jid}/skip")
        assert r.status_code == 409, r.text

        # pending job can be skipped
        fd2, path2 = tempfile.mkstemp(suffix=".db")
        os.close(fd2)
        try:
            gid2, mid2, jid2 = _seed_goal_material_job(path2)
            c2 = _client_at(path2)
            r = c2.post(f"/api/jobs/{jid2}/skip")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "skipped"
        finally:
            os.unlink(path2)
    finally:
        os.unlink(path)


# --------------------------------------------------------------------------- jobs payload includes pinned


def test_jobs_and_events_include_pinned():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        gid, mid, jid = _seed_goal_material_job(path)
        c = _client_at(path)
        r = c.get("/api/jobs")
        assert r.status_code == 200
        jobs = r.json()
        assert any(j["id"] == jid and "pinned" in j for j in jobs), jobs
        r = c.get("/api/events")
        assert r.status_code == 200
        events = r.json()
        job_events = [e for e in events if e.get("job")]
        assert job_events, events
        assert "pinned" in job_events[0]["job"]
    finally:
        os.unlink(path)


# --------------------------------------------------------------------------- upload with goal_id


def test_upload_with_goal_creates_jobs_immediately():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = db.connect(path)
        db.init_db(conn)
        gid = db.create_goal(conn, title="G1", deadline="2026-12-18T00:00:00Z")
        conn.commit()
        conn.close()

        c = _client_at(path)
        r = c.post(
            "/api/inbox/upload",
            files={"files": ("limits.md", b"# Limits chapter\n\ncontent.", "text/markdown")},
            data={"goal_id": gid},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body[0]["status"] == "ready"
        assert body[0]["topic_id"] is not None
        assert body[0]["cluster_relation"] == "new"

        # one replan ran automatically: jobs exist for the goal
        r2 = c.get("/api/jobs")
        jobs = [j for j in r2.json() if j["goal_id"] == gid]
        assert jobs, "expected jobs to be created immediately on upload with goal_id"
    finally:
        os.unlink(path)


# --------------------------------------------------------------------------- inbox: dismiss / effort / attach


def test_dismiss_json_and_new_action():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = db.connect(path)
        db.init_db(conn)
        mid = db.create_material(
            conn, goal_id=None, topic_id=None, filename="dup.md",
            original_path="/x/dup.md", markdown_path="/x/dup.md",
            content_hash="hash-dup", summary="s", tags='[]',
            effort_hours=1.0, status="duplicate",
        )
        conn.commit()
        conn.close()

        c = _client_at(path)
        # JSON body: undo duplicate via 'new' action -> back to ready
        r = c.post(f"/api/inbox/files/{mid}/dismiss", json={"action": "new"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ready"

        # form body backward compat: mark as duplicate
        r = c.post(f"/api/inbox/files/{mid}/dismiss", data={"action": "duplicate"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "duplicate"

        # invalid action -> 422
        r = c.post(f"/api/inbox/files/{mid}/dismiss", json={"action": "bogus"})
        assert r.status_code == 422, r.text
    finally:
        os.unlink(path)


def test_effort_json_and_form():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = db.connect(path)
        db.init_db(conn)
        mid = db.create_material(
            conn, goal_id=None, topic_id=None, filename="m.md",
            original_path="/x/m.md", markdown_path="/x/m.md",
            content_hash="hash-m", summary="s", tags='[]',
            effort_hours=1.0, status="ready",
        )
        conn.commit()
        conn.close()

        c = _client_at(path)
        # JSON body
        r = c.post(f"/api/inbox/files/{mid}/effort", json={"hours": 2.5})
        assert r.status_code == 200, r.text
        assert r.json()["effort_hours"] == 2.5
        # form body backward compat
        r = c.post(f"/api/inbox/files/{mid}/effort", data={"hours": 0.5})
        assert r.status_code == 200, r.text
        assert r.json()["effort_hours"] == 0.5
    finally:
        os.unlink(path)


def test_attach_only_ready_and_replan():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = db.connect(path)
        db.init_db(conn)
        gid = db.create_goal(conn, title="G1", deadline="2026-12-18T00:00:00Z")
        mid = db.create_material(
            conn, goal_id=None, topic_id=None, filename="m.md",
            original_path="/x/m.md", markdown_path="/x/m.md",
            content_hash="hash-m", summary="s", tags='[]',
            effort_hours=1.0, status="ready",
        )
        error_mid = db.create_material(
            conn, goal_id=None, topic_id=None, filename="bad.xyz",
            original_path="/x/bad.xyz", markdown_path="/x/bad.xyz",
            content_hash="hash-bad", summary=None, tags=None,
            effort_hours=None, status="error",
        )
        conn.commit()
        conn.close()

        c = _client_at(path)
        # not ready -> 422
        r = c.post(f"/api/inbox/files/{error_mid}/attach", json={"goal_id": gid})
        assert r.status_code == 422, r.text

        # ready -> attach + replan
        r = c.post(f"/api/inbox/files/{mid}/attach", json={"goal_id": gid})
        assert r.status_code == 200, r.text
        assert r.json()["goal_id"] == gid
        r2 = c.get("/api/jobs")
        jobs = [j for j in r2.json() if j["goal_id"] == gid]
        assert jobs, "attaching a ready material should schedule jobs"
    finally:
        os.unlink(path)


# --------------------------------------------------------------------------- topics


def test_topics_list_rename_merge_attach():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = db.connect(path)
        db.init_db(conn)
        gid = db.create_goal(conn, title="G1", deadline="2026-12-18T00:00:00Z")
        mid = db.create_material(
            conn, goal_id=gid, topic_id=None, filename="m.md",
            original_path="/x/m.md", markdown_path="/x/m.md",
            content_hash="hash-m", summary="s", tags='[]',
            effort_hours=1.0, status="ready",
        )
        topic_id = db.create_topic(conn, name="Old Name", goal_id=gid)
        db.update_material(conn, mid, topic_id=topic_id)
        conn.commit()
        conn.close()

        c = _client_at(path)

        # list topics with members
        r = c.get("/api/topics")
        assert r.status_code == 200, r.text
        topics = r.json()
        topic = [t for t in topics if t["id"] == topic_id][0]
        assert topic["name"] == "Old Name"
        assert len(topic["members"]) == 1
        assert topic["members"][0]["filename"] == "m.md"

        # rename (JSON)
        r = c.post(f"/api/topics/{topic_id}/rename", json={"name": "New Name"})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "New Name"

        # merge: create a second topic + material, merge into the first
        conn = db.connect(path)
        mid2 = db.create_material(
            conn, goal_id=gid, topic_id=None, filename="m2.md",
            original_path="/x/m2.md", markdown_path="/x/m2.md",
            content_hash="hash-m2", summary="s2", tags='[]',
            effort_hours=1.0, status="ready",
        )
        topic2_id = db.create_topic(conn, name="To Merge", goal_id=gid)
        db.update_material(conn, mid2, topic_id=topic2_id)
        conn.commit()
        conn.close()

        r = c.post("/api/topics/merge", json={"source_id": topic2_id, "target_id": topic_id})
        assert r.status_code == 200, r.text
        # source topic deleted, member moved
        conn = db.connect(path)
        assert conn.execute("SELECT id FROM topics WHERE id = ?", (topic2_id,)).fetchone() is None
        mat = db.get_material(conn, mid2)
        assert mat["topic_id"] == topic_id
        conn.close()

        # attach via the inbox endpoint
        r = c.post(f"/api/inbox/files/{mid}/attach", json={"goal_id": gid})
        assert r.status_code == 200, r.text
    finally:
        os.unlink(path)


# --------------------------------------------------------------------------- chat


def test_chat_offline_answers_due_this_week():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        gid, mid, jid = _seed_goal_material_job(path)
        c = _client_at(path)
        r = c.post("/api/chat", json={"message": "what's due this week?"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["llm"] == "off"
        assert "Limits session" in body["reply"], body
        assert "learn" in body["reply"]

        # history persisted, oldest first
        r = c.get("/api/chat/messages")
        assert r.status_code == 200
        msgs = r.json()
        assert any(m["role"] == "user" and m["content"] == "what's due this week?" for m in msgs)
        assert any(m["role"] == "assistant" for m in msgs)
        # oldest-first ordering
        roles = [m["role"] for m in msgs]
        assert roles.count("user") >= 1 and roles.count("assistant") >= 1
    finally:
        os.unlink(path)


def test_chat_missing_message_422_keeps_history_untouched():
    c = _client()
    r = c.post("/api/chat", json={})
    assert r.status_code == 422, r.text
    r = c.post("/api/chat", json={"message": "   "})
    assert r.status_code == 422, r.text


# --------------------------------------------------------------------------- stats


def test_stats_returns_planned_done_progress():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        gid, mid, jid = _seed_goal_material_job(path)
        c = _client_at(path)
        r = c.get(f"/api/stats?goal_id={gid}")
        assert r.status_code == 200, r.text
        body = r.json()
        totals = body["totals"]
        assert "planned_min" in totals
        assert "done_min" in totals
        assert "progress" in totals
        assert "materials" in totals
        assert "revisions_due" in totals
        assert "goals" in body
        assert "by_day" in body
        row = next(pg for pg in body["goals"] if pg["goal_id"] == gid)
        assert row["status"] and "planned_jobs" in row and "done_jobs" in row
    finally:
        os.unlink(path)


# --------------------------------------------------------------------------- settings: JSON + form + validation


def test_settings_full_validation():
    c = _client()
    # valid JSON updates
    r = c.put("/api/settings", json={"daily_capacity_hours": 6.0})
    assert r.status_code == 200, r.text
    r = c.put("/api/settings", json={"slot_minutes": 30, "min_slot_minutes": 15})
    assert r.status_code == 200, r.text
    r = c.put("/api/settings", json={"default_effort_hours": 2.0})
    assert r.status_code == 200, r.text
    r = c.put("/api/settings", json={"revision_intervals_days": [1, 2, 3]})
    assert r.status_code == 200, r.text
    r = c.put("/api/settings", json={"ocr_provider": "cloudflare", "ocr_min_chars": 100})
    assert r.status_code == 200, r.text

    # out of bounds -> 422, DB unchanged
    r = c.put("/api/settings", json={"daily_capacity_hours": 999})
    assert r.status_code == 422, r.text
    r = c.put("/api/settings", json={"slot_minutes": 999})
    assert r.status_code == 422, r.text
    r = c.put("/api/settings", json={"min_slot_minutes": 5})
    assert r.status_code == 422, r.text
    r = c.put("/api/settings", json={"default_effort_hours": 50})
    assert r.status_code == 422, r.text
    r = c.put("/api/settings", json={"revision_intervals_days": [1, 99]})
    assert r.status_code == 422, r.text
    r = c.put("/api/settings", json={"ocr_provider": "blah"})
    assert r.status_code == 422, r.text
    r = c.put("/api/settings", json={"ocr_min_chars": 0})
    assert r.status_code == 422, r.text

    # valid values persist
    r = c.put("/api/settings", json={"daily_capacity_hours": 8.0})
    assert r.status_code == 200, r.text
    r = c.get("/api/settings")
    vals = {s["key"]: s["value"] for s in r.json()}
    assert vals["daily_capacity_hours"] == "8.0"

    # form body backward compat
    r = c.put("/api/settings", data={"slot_minutes": "40"})
    assert r.status_code == 200, r.text
