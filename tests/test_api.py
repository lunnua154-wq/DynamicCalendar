"""API tests (FastAPI TestClient) covering docs/API.md happy paths + error codes."""

import os
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_API_DB = REPO_ROOT / "data" / "_test" / f"api-{uuid.uuid4().hex}.db"
_API_DB.parent.mkdir(parents=True, exist_ok=True)
os.environ["DYNACALENDAR_DB"] = str(_API_DB)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

from conftest import SAMPLES  # noqa: E402

MARKDOWN = b"# Problem Set 3\n\nExercises on limits and continuity.\n"


@pytest.fixture
def client():
    if _API_DB.exists():
        _API_DB.unlink()
    conn = db.connect(_API_DB)
    db.init_db(conn)
    db.update_setting(conn, "utc_offset_minutes", "0")
    conn.commit()
    conn.close()
    yield TestClient(app)


def _upload(client, name="problem_set_3.md", raw=MARKDOWN):
    response = client.post(
        "/api/inbox/upload",
        files=[("files[]", (name, raw, "text/markdown"))],
    )
    assert response.status_code == 200, response.text
    return response.json()[0]


def _goal_with_jobs(client, deadline="2026-12-18"):
    goal = client.post("/api/goals", json={"title": "G1", "deadline": deadline})
    assert goal.status_code == 200, goal.text
    goal_id = goal.json()["id"]
    uploaded = _upload(client)
    attach = client.post(
        f"/api/inbox/files/{uploaded['material_id']}/attach",
        json={"goal_id": goal_id},
    )
    assert attach.status_code == 200, attach.text
    jobs = client.get("/api/jobs").json()
    assert jobs, "expected scheduled learn jobs"
    return goal_id, uploaded, jobs


# --------------------------------------------------------------------------- basics


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_pages_are_served(client):
    for route, marker in (("/", b"DynaCalendar"), ("/week", b"DynaCalendar"), ("/inbox", b"DynaCalendar")):
        response = client.get(route)
        assert response.status_code == 200, route
        assert marker in response.content


# --------------------------------------------------------------------------- goals


def test_goals_crud_and_replan(client):
    response = client.post("/api/goals", json={"title": "G1", "deadline": "2026-12-18"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "open"
    assert body["deadline"] == "2026-12-18T00:00:00Z"
    assert body["progress"] == 0

    listed = client.get("/api/goals")
    assert listed.status_code == 200
    assert any(g["id"] == body["id"] for g in listed.json())

    replan = client.post(f"/api/goals/{body['id']}/replan")
    assert replan.status_code == 200, replan.text
    payload = replan.json()
    assert {"moved_jobs", "revisions_added"} <= set(payload)


def test_goal_requires_deadline(client):
    response = client.post("/api/goals", json={"title": "no deadline"})
    assert response.status_code == 422, response.text


def test_404_on_missing_goal(client):
    response = client.post("/api/goals/nope/replan")
    assert response.status_code == 404, response.text


# --------------------------------------------------------------------------- settings


def test_settings_bounds_422(client):
    assert client.put("/api/settings", json={"daily_capacity_hours": 999}).status_code == 422
    assert client.put("/api/settings", json={"slot_minutes": 999}).status_code == 422
    assert client.put("/api/settings", json={"slot_minutes": 10}).status_code == 422
    assert client.put("/api/settings", json={"revision_intervals_days": [3, 1]}).status_code == 422


def test_settings_unknown_key_422(client):
    response = client.put("/api/settings", json={"not_a_setting": 1})
    assert response.status_code == 422, response.text


def test_settings_update_persists(client):
    response = client.put("/api/settings", json={"daily_capacity_hours": 6.0})
    assert response.status_code == 200, response.text
    values = {row["key"]: row["value"] for row in client.get("/api/settings").json()}
    assert values["daily_capacity_hours"] == "6.0"
    # string settings must round-trip unquoted
    client.put("/api/settings", json={"llm_model": "llama3"})
    values = {row["key"]: row["value"] for row in client.get("/api/settings").json()}
    assert values["llm_model"] == "llama3"


# --------------------------------------------------------------------------- calendar / ICS


def test_ics_import_roundtrip(client):
    with open(SAMPLES / "sample_calendar.ics", "rb") as handle:
        response = client.post(
            "/api/ics/import", files={"file": ("sample.ics", handle, "text/calendar")}
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["imported"] == 10
    assert body["exams_found"] == ["MATH2001 Final Exam"]
    assert body["exam_events"][0]["venue"] == "LT204"

    events = client.get("/api/events").json()
    assert len(events) == 10
    assert all(e["kind"] == "external" and e["locked"] == 1 for e in events)


def test_ics_import_rejects_garbage(client):
    response = client.post(
        "/api/ics/import", files={"file": ("bad.ics", b"nope", "text/calendar")}
    )
    assert response.status_code == 422, response.text
    assert "error" in response.json()


def test_events_range_filter(client):
    with open(SAMPLES / "sample_calendar.ics", "rb") as handle:
        client.post("/api/ics/import", files={"file": ("sample.ics", handle, "text/calendar")})
    november = client.get("/api/events", params={"from": "2026-11-01", "to": "2026-11-07"}).json()
    assert november, "expected November events"
    assert all(e["dtstart"] < "2026-11-08" for e in november)
    december = client.get("/api/events", params={"from": "2026-12-01", "to": "2026-12-31"}).json()
    assert december and all(e["dtstart"] >= "2026-12-01" for e in december)


# --------------------------------------------------------------------------- inbox (M1)


def test_upload_without_files_422(client):
    response = client.post("/api/inbox/upload")
    assert response.status_code == 422, response.text


def test_upload_processes_to_ready(client):
    uploaded = _upload(client)
    assert uploaded["status"] == "processing"

    files = client.get("/api/inbox/files").json()
    assert len(files) == 1
    material = files[0]
    assert material["status"] == "ready", material
    assert material["tags"] == ["problem_set_3"]
    assert material["effort_hours"] == 1.0
    assert material["topic_name"] == "problem_set_3"
    assert "new topic" in material["result"]

    topics = client.get("/api/topics").json()
    assert len(topics) == 1 and topics[0]["file_count"] == 1

    detail = client.get(f"/api/materials/{material['id']}").json()
    assert "Problem Set 3" in detail["markdown"]


def test_upload_duplicate_is_flagged(client):
    _upload(client)
    second = _upload(client)
    assert second["status"] == "duplicate"
    files = client.get("/api/inbox/files").json()
    assert len(files) == 2
    assert len(client.get("/api/topics").json()) == 1


def test_effort_override_bounds(client):
    uploaded = _upload(client)
    material_id = uploaded["material_id"]
    ok = client.post(f"/api/inbox/files/{material_id}/effort", json={"hours": 3.5})
    assert ok.status_code == 200, ok.text
    assert ok.json()["effort_hours"] == 3.5
    bad = client.post(f"/api/inbox/files/{material_id}/effort", json={"hours": 99})
    assert bad.status_code == 422


def test_dismiss_material(client):
    uploaded = _upload(client)
    response = client.post(
        f"/api/inbox/files/{uploaded['material_id']}/dismiss", json={"action": "duplicate"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "duplicate"
    assert (
        client.post(
            f"/api/inbox/files/{uploaded['material_id']}/dismiss", json={"action": "nonsense"}
        ).status_code
        == 422
    )


def test_attach_schedules_jobs(client):
    goal_id, uploaded, jobs = _goal_with_jobs(client)
    goal = [g for g in client.get("/api/goals").json() if g["id"] == goal_id][0]
    assert goal["total_hours"] == 1.0
    assert jobs[0]["material_id"] == uploaded["material_id"]
    assert jobs[0]["status"] == "pending"
    assert jobs[0]["material_title"] == "problem_set_3.md"


def test_topic_rename_and_merge(client):
    _upload(client, name="a.md", raw=b"# A\n")
    _upload(client, name="b.md", raw=b"# B\n")
    topics = client.get("/api/topics").json()
    assert len(topics) == 2
    renamed = client.post(f"/api/topics/{topics[0]['id']}/rename", json={"name": "Merged"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Merged"

    merged = client.post(f"/api/topics/{topics[1]['id']}/merge", json={"target_id": topics[0]["id"]})
    assert merged.status_code == 200, merged.text
    remaining = client.get("/api/topics").json()
    assert len(remaining) == 1 and remaining[0]["file_count"] == 2


# --------------------------------------------------------------------------- jobs


def test_complete_tick_is_atomic_and_immutable(client):
    _, _, jobs = _goal_with_jobs(client)
    job_id = jobs[0]["id"]
    response = client.post(f"/api/jobs/{job_id}/complete")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job"]["status"] == "done"
    assert len(body["revisions_added"]) == 4, body
    assert body["replanned"] == [] or isinstance(body["replanned"], list)

    again = client.post(f"/api/jobs/{job_id}/complete")
    assert again.status_code == 409, again.text


def test_move_job_vetoes_external_events(client):
    _, _, jobs = _goal_with_jobs(client)
    job_id = jobs[0]["id"]
    ics = (
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//EN\n"
        "BEGIN:VEVENT\nUID:blocker@x\nDTSTART:20261201T100000Z\n"
        "DTEND:20261201T110000Z\nSUMMARY:Blocker\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    assert client.post(
        "/api/ics/import", files={"file": ("block.ics", ics.encode(), "text/calendar")}
    ).status_code == 200

    clash = client.post(
        f"/api/jobs/{job_id}/move",
        json={"slot_start": "2026-12-01T10:30:00Z", "slot_end": "2026-12-01T11:20:00Z"},
    )
    assert clash.status_code == 409, clash.text
    assert "error" in clash.json()

    moved = client.post(
        f"/api/jobs/{job_id}/move",
        json={"slot_start": "2026-12-02T09:00:00Z", "slot_end": "2026-12-02T09:50:00Z"},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["job"]["slot_start"] == "2026-12-02T09:00:00Z"

    # the manual move sticks: a replan must not re-derive it away
    client.post(f"/api/goals/{moved.json()['job']['goal_id']}/replan")
    still = [j for j in client.get("/api/jobs").json() if j["id"] == job_id][0]
    assert still["slot_start"] == "2026-12-02T09:00:00Z"


def test_move_rejects_the_past(client):
    _, _, jobs = _goal_with_jobs(client)
    response = client.post(
        f"/api/jobs/{jobs[0]['id']}/move",
        json={"slot_start": "2020-01-01T09:00:00Z", "slot_end": "2020-01-01T09:50:00Z"},
    )
    assert response.status_code == 422, response.text


def test_skip_job(client):
    _, _, jobs = _goal_with_jobs(client)
    job_id = jobs[0]["id"]
    response = client.post(f"/api/jobs/{job_id}/skip")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "skipped"
    assert client.post(f"/api/jobs/{job_id}/skip").status_code == 409


# --------------------------------------------------------------------------- chat


def test_chat_offline_answers_from_calendar(client):
    _goal_with_jobs(client)
    response = client.post("/api/chat", json={"message": "what's due this week?"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["llm"] == "off"
    assert "LLM not configured" in body["reply"]
    assert "Next up" in body["reply"]

    history = client.get("/api/chat").json()
    assert [m["role"] for m in history] == ["user", "assistant"]
