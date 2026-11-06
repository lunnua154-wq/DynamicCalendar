"""Ingest pipeline tests (M1) — hash de-dup, conversion, tagging, clustering."""

from app import db, ingest
from app.config import Settings

from conftest import SAMPLES


def _settings(**overrides) -> Settings:
    base = dict(
        daily_capacity_hours=4.0,
        slot_minutes=50,
        min_slot_minutes=30,
        revision_intervals_days=[1, 3, 7, 14],
        llm_base_url="",
        llm_model="",
        llm_api_key="",
        default_effort_hours=1.0,
    )
    base.update(overrides)
    return Settings(**base)


class StubClient:
    """Deterministic stand-in for LLMClient (no network, no OpenAI SDK)."""

    def __init__(self, relation="new", topic_id=None, topic_name=None, effort=2.0):
        self.relation = relation
        self.topic_id = topic_id
        self.topic_name = topic_name
        self.effort = effort
        self.tags: list[str] = []

    def tag(self, *, markdown, filename):
        stem = filename.rsplit(".", 1)[0]
        self.tags.append(stem)
        return {
            "summary": f"Summary of {filename}",
            "tags": [stem],
            "topic": stem,
            "effort_hours": self.effort,
        }

    def cluster(self, *, content_hash, filename, existing_topics, existing_material_hashes, effort_hours=None):
        if content_hash in existing_material_hashes:
            return {"relation": "duplicate", "topic_id": None, "topic_name": None, "reason": "dup"}
        return {
            "relation": self.relation,
            "topic_id": self.topic_id,
            "topic_name": self.topic_name or filename.rsplit(".", 1)[0],
            "reason": "stub",
        }


def _ingest(conn, data_dir, name: str, raw: bytes, client=None):
    stored = ingest.store_upload(
        conn,
        raw=raw,
        filename=name,
        goal_id=None,
        originals_dir=data_dir / "originals",
    )
    conn.commit()
    if stored["status"] != "processing":
        return stored, db.get_material(conn, stored["material_id"])
    material = ingest.process_material(
        conn,
        stored["material_id"],
        markdown_dir=data_dir / "markdown",
        client=client or StubClient(),
        settings=_settings(),
        now_iso="2026-11-05T10:00:00Z",
    )
    conn.commit()
    return stored, material


def test_sample_markdown_ingests_and_converts(conn, data_dir):
    raw = (SAMPLES / "materials" / "math_chapter3_limits.md").read_bytes()
    stored, material = _ingest(conn, data_dir, "math_chapter3_limits.md", raw)
    assert stored["status"] == "processing"
    assert material["status"] == "ready"
    assert material["effort_hours"] == 2.0
    assert material["summary"] == "Summary of math_chapter3_limits.md"
    assert material["topic_id"] is not None
    assert material["topic_name"] == "math_chapter3_limits"
    markdown = (data_dir / "markdown" / f"{material['id']}.md").read_text(encoding="utf-8")
    assert "Limits" in markdown


def test_hash_is_stable_across_runs(conn, data_dir):
    raw = (SAMPLES / "materials" / "problem_set_3.md").read_bytes()
    assert ingest.content_hash(raw) == ingest.content_hash(raw)
    stored, material = _ingest(conn, data_dir, "problem_set_3.md", raw)
    assert material["content_hash"] == ingest.content_hash(raw)


def test_duplicate_drop_is_flagged(conn, data_dir):
    raw = (SAMPLES / "materials" / "problem_set_3.md").read_bytes()
    _, first = _ingest(conn, data_dir, "problem_set_3.md", raw)
    stored, second = _ingest(conn, data_dir, "problem_set_3.md", raw)
    assert stored["status"] == "duplicate"
    assert second["status"] == "duplicate"
    assert second["duplicate_of"] == first["id"]
    assert second["topic_id"] == first["topic_id"]
    # no new topic was created for the duplicate
    assert len(db.list_topics(conn)) == 1


def test_unsupported_format_errors_but_keeps_file(conn, data_dir):
    stored, material = _ingest(conn, data_dir, "notes.xyz", b"binary-ish")
    assert material["status"] == "error"
    assert "unsupported format (.xyz)" in material["status_reason"]
    assert (data_dir / "originals" / f"{material['id']}.xyz").exists()


def test_version_update_supersedes_previous(conn, data_dir):
    v1 = (SAMPLES / "materials" / "math_chapter3_limits.md").read_bytes()
    _, first = _ingest(conn, data_dir, "math_chapter3_limits.md", v1)
    v2 = (SAMPLES / "materials" / "math_chapter3_limits_v2.md").read_bytes()
    client = StubClient(relation="version", topic_id=first["topic_id"])
    _, second = _ingest(conn, data_dir, "math_chapter3_limits_v2.md", v2, client=client)
    assert second["status"] == "ready"
    assert second["topic_id"] == first["topic_id"]
    old = db.get_material(conn, first["id"])
    assert old["status"] == "superseded"
    assert old["superseded_by"] == second["id"]


def test_offline_cluster_creates_one_topic_per_file(conn, data_dir):
    for name in (
        "math_chapter3_limits.md",
        "math_chapter3_limits_v2.md",
        "problem_set_3.md",
        "cs_lecture08_graphs.md",
    ):
        raw = (SAMPLES / "materials" / name).read_bytes()
        _, material = _ingest(conn, data_dir, name, raw)
        assert material["status"] == "ready", material
    topics = db.list_topics(conn)
    assert len(topics) == 4
    assert all(t["file_count"] == 1 for t in topics)


def test_processing_twice_is_a_noop(conn, data_dir):
    raw = (SAMPLES / "materials" / "cs_lecture08_graphs.md").read_bytes()
    _, material = _ingest(conn, data_dir, "cs_lecture08_graphs.md", raw)
    again = ingest.process_material(
        conn,
        material["id"],
        markdown_dir=data_dir / "markdown",
        client=StubClient(),
        settings=_settings(),
        now_iso="2026-11-05T10:00:00Z",
    )
    assert again["status"] == "ready"
    assert again["topic_id"] == material["topic_id"]


def test_attach_to_goal_schedules_work(conn, data_dir):
    raw = (SAMPLES / "materials" / "problem_set_3.md").read_bytes()
    _, material = _ingest(conn, data_dir, "problem_set_3.md", raw)
    goal_id = db.create_goal(conn, title="G1", deadline="2026-12-18T00:00:00Z")
    conn.commit()
    ingest.attach_to_goal(conn, material["id"], goal_id, now_iso="2026-11-05T10:00:00Z")
    conn.commit()
    jobs = db.jobs_for_goal(conn, goal_id)
    assert jobs, "attaching a ready material should schedule learn jobs"
    assert all(j["material_id"] == material["id"] for j in jobs)


def test_merge_topics_moves_members(conn, data_dir):
    raw = (SAMPLES / "materials" / "problem_set_3.md").read_bytes()
    _, material = _ingest(conn, data_dir, "problem_set_3.md", raw)
    other = db.create_topic(conn, name="Other")
    conn.commit()
    merged = ingest.merge_topics(
        conn, source_topic_id=material["topic_id"], target_topic_id=other,
        now_iso="2026-11-05T10:00:00Z",
    )
    conn.commit()
    assert merged["id"] == other
    assert db.get_material(conn, material["id"])["topic_id"] == other
    assert [t["id"] for t in db.list_topics(conn)] == [other]
