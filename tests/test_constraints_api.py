"""REST tests for /api/constraints, /api/events/annotate and the new settings keys."""

import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMP_DIRS: list[Path] = []


def _temp_db() -> Path:
    base = _REPO_ROOT / "data" / "_test" / uuid.uuid4().hex
    base.mkdir(parents=True, exist_ok=True)
    _TEMP_DIRS.append(base)
    return base / "test.db"


os.environ["DYNACALENDAR_DB"] = str(_temp_db())

from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _cleanup_temp_dirs():
    yield
    for d in _TEMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)


def _client_at(path):
    import app.main as main_mod

    main_mod.DEFAULT_DB_PATH = path
    return TestClient(app)


def _seed_open_goal(path, effort=1.0):
    deadline = (datetime.now(timezone.utc) + timedelta(days=60)).strftime("%Y-%m-%dT00:00:00Z")
    conn = db.connect(path)
    db.init_db(conn)
    gid = db.create_goal(conn, title="G1", deadline=deadline)
    db.create_material(
        conn, goal_id=gid, topic_id=None, filename="limits.md",
        original_path="/x", markdown_path="/x",
        content_hash="hash-" + uuid.uuid4().hex[:8], summary="s",
        tags='["math"]', effort_hours=effort, status="tagged",
    )
    conn.commit()
    conn.close()
    return gid


def test_constraints_crud_happy_path():
    path = _temp_db()
    _seed_open_goal(path)
    client = _client_at(path)

    r = client.get("/api/constraints")
    assert r.status_code == 200
    assert r.json() == []

    r = client.post(
        "/api/constraints",
        json={
            "kind": "subject_gap",
            "params": {"subject": "math", "gap_minutes": 45},
            "label": "No math right after math",
        },
    )
    assert r.status_code == 201
    body = r.json()
    cid = body["constraint"]["id"]
    assert body["constraint"]["kind"] == "subject_gap"
    assert body["constraint"]["params"] == {"subject": "math", "gap_minutes": 45}
    assert body["constraint"]["hard"] is False
    assert body["replanned_goals"] == 1

    r = client.get("/api/constraints")
    assert [c["id"] for c in r.json()] == [cid]

    r = client.delete(f"/api/constraints/{cid}")
    assert r.status_code == 200
    assert r.json()["deleted"] == cid

    r = client.get("/api/constraints")
    assert r.json() == []


def test_create_constraint_fractional_param_422():
    path = _temp_db()
    client = _client_at(path)
    r = client.post(
        "/api/constraints",
        json={"kind": "post_fatigue_buffer", "params": {"min_fatigue": 2.9}},
    )
    assert r.status_code == 422


def test_create_constraint_interleave_hard_422():
    path = _temp_db()
    client = _client_at(path)
    r = client.post(
        "/api/constraints",
        json={"kind": "interleave", "params": {"max_consecutive": 2}, "hard": True},
    )
    assert r.status_code == 422


def test_create_constraint_invalid_kind_422():
    path = _temp_db()
    client = _client_at(path)
    r = client.post("/api/constraints", json={"kind": "yolo", "params": {}})
    assert r.status_code == 422


def test_create_constraint_out_of_range_param_422():
    path = _temp_db()
    client = _client_at(path)
    r = client.post(
        "/api/constraints",
        json={"kind": "subject_gap", "params": {"subject": "math", "gap_minutes": 9999}},
    )
    assert r.status_code == 422


def test_create_constraint_missing_subject_422():
    path = _temp_db()
    client = _client_at(path)
    r = client.post("/api/constraints", json={"kind": "subject_gap", "params": {"gap_minutes": 60}})
    assert r.status_code == 422


def test_delete_unknown_constraint_404():
    path = _temp_db()
    client = _client_at(path)
    r = client.delete("/api/constraints/does-not-exist")
    assert r.status_code == 404


def test_settings_smart_placement_validation():
    path = _temp_db()
    client = _client_at(path)
    r = client.put("/api/settings", json={"smart_placement": "bogus"})
    assert r.status_code == 422
    r = client.put("/api/settings", json={"smart_placement": "off"})
    assert r.status_code == 200
    r = client.put("/api/settings", json={"llm_placement_mode": "choose"})
    assert r.status_code == 422
    r = client.put("/api/settings", json={"llm_placement_mode": "rank"})
    assert r.status_code == 200


def test_annotate_endpoint_force_preserves_user_annotations():
    path = _temp_db()
    conn = db.connect(path)
    db.init_db(conn)
    eid = db.create_event(
        conn, kind="external", title="THE HARDEST LECTURE EVER",
        dtstart="2026-11-05T08:00:00Z", dtend="2026-11-05T12:00:00Z",
        locked=1, ics_uid="uid-user-preserved",
    )
    db.upsert_annotation(conn, eid, subject="cs", fatigue=2, source="user", note="mine")
    conn.commit()
    conn.close()
    client = _client_at(path)
    r = client.post("/api/events/annotate", json={"force": True})
    assert r.status_code == 200
    check = db.connect(path)
    row = check.execute(
        "SELECT source, fatigue, note FROM event_annotations WHERE event_id=?", (eid,)
    ).fetchone()
    check.close()
    assert row["source"] == "user"
    assert row["fatigue"] == 2
    assert row["note"] == "mine"


def test_annotate_endpoint_marks_externals():
    path = _temp_db()
    conn = db.connect(path)
    db.init_db(conn)
    db.create_event(
        conn, kind="external", title="MATH2001 Lecture",
        dtstart="2026-11-10T08:00:00Z", dtend="2026-11-10T12:00:00Z",
        locked=1, ics_uid="uid-api-ann",
    )
    conn.commit()
    conn.close()
    client = _client_at(path)
    r = client.post("/api/events/annotate", json={})
    assert r.status_code == 200
    assert r.json()["annotated"] == 1
    r = client.post("/api/events/annotate", json={})
    assert r.json()["annotated"] == 0
