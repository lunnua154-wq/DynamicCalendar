"""Ingest pipeline: store uploads, convert to markdown, tag/cluster.

M0 file-drop path (P3 inbox):
  store_upload()   -> save original, hash de-dup, create 'processing' material
  process_material() -> convert (markitdown), tag (llm.tag), cluster (llm.cluster),
                        finalize as 'ready'/'error'/'duplicate'
  attach_to_goal() -> link a ready material to a goal and replan
  merge_topics()   -> move all materials from one topic to another
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import db, engine, ocr
from .config import Settings


def content_hash(raw: bytes) -> str:
    """SHA-256 of the raw file bytes — deterministic de-dup key."""
    return hashlib.sha256(raw).hexdigest()


def store_upload(
    conn,
    *,
    raw: bytes,
    filename: str,
    goal_id: str | None,
    originals_dir: Path,
) -> dict[str, Any]:
    """Save an uploaded file, de-dup by content hash, create a 'processing' material.

    Returns:
        {"status": "processing", "material_id": <id>}   — new file
        {"status": "duplicate", "material_id": <id>, "duplicate_of": <id>}  — already ingested
    """
    ch = content_hash(raw)
    existing = db.find_material_by_hash(conn, ch)
    if existing is not None:
        # Link to the root ORIGINAL (never to another duplicate) so the
        # duplicate_of chain always terminates at the first ingested copy.
        original = db.find_original_material_by_hash(conn, ch)
        original_id = original["id"] if original is not None else existing["id"]
        # Create a distinct duplicate material record referencing the original.
        dup_id = db.new_id()
        dup_original = originals_dir / f"{dup_id}_{filename}"
        dup_original.write_bytes(raw)
        topic_id = original["topic_id"] if original is not None else existing["topic_id"]
        material_id = db.create_material(
            conn,
            goal_id=original["goal_id"] if original is not None else existing["goal_id"],
            topic_id=topic_id,
            filename=filename,
            original_path=str(dup_original),
            markdown_path=existing["markdown_path"],
            content_hash=ch,
            summary=existing["summary"],
            tags=existing["tags"],
            effort_hours=existing["effort_hours"],
            status="duplicate",
            duplicate_of=original_id,
        )
        conn.commit()
        return {
            "status": "duplicate",
            "material_id": material_id,
            "duplicate_of": original_id,
        }

    originals_dir.mkdir(parents=True, exist_ok=True)
    material_id = db.new_id()
    suffix = Path(filename).suffix
    original_path = originals_dir / f"{material_id}{suffix}"
    original_path.write_bytes(raw)

    material_id = db.create_material(
        conn,
        material_id=material_id,
        goal_id=goal_id,
        topic_id=None,
        filename=filename,
        original_path=str(original_path),
        markdown_path="",
        content_hash=ch,
        summary=None,
        tags=None,
        effort_hours=None,
        status="processing",
    )
    conn.commit()

    return {"status": "processing", "material_id": material_id}


def process_material(
    conn,
    material_id: str,
    *,
    markdown_dir: Path,
    client: Any,
    settings: Settings,
    now_iso: str,
) -> dict[str, Any]:
    """Convert, tag, cluster, and finalize a 'processing' material.

    Returns the material row (dict) plus a non-persisted ``topic_name`` key.
    Idempotent: re-processing a non-processing material is a no-op.
    """
    material = db.get_material(conn, material_id)
    if material["status"] != "processing":
        return dict(material)

    filename = material["filename"]
    original_path = Path(material["original_path"])
    ch = material["content_hash"]

    # --- validate file type -------------------------------------------------
    SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt", ".html", ".htm",
                            ".pdf", ".docx", ".pptx", ".csv", ".json", ".xml"}
    if original_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        db.update_material(
            conn,
            material_id,
            status="error",
            status_reason=f"unsupported format ({original_path.suffix})",
        )
        conn.commit()
        return dict(db.get_material(conn, material_id))

    # --- convert to markdown ------------------------------------------------
    try:
        from markitdown import MarkItDown

        converter = MarkItDown()
        md_result = converter.convert(str(original_path))
        markdown_text = getattr(md_result, "textcontent", None) or str(md_result)
    except Exception as exc:
        db.update_material(
            conn,
            material_id,
            status="error",
            status_reason=f"convert failed: {exc}",
        )
        conn.commit()
        return dict(db.get_material(conn, material_id))

    # --- optional OCR for scanned PDFs --------------------------------------
    if (
        original_path.suffix.lower() == ".pdf"
        and len(markdown_text or "") < settings.ocr_min_chars
    ):
        try:
            ocr_text = ocr.ocr_pdf(str(original_path), settings)
        except Exception:  # never fail ingest on OCR
            ocr_text = None
        if ocr_text:
            markdown_text = ocr_text

    markdown_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = markdown_dir / f"{material_id}.md"
    markdown_path.write_text(markdown_text, encoding="utf-8")

    # --- tag ----------------------------------------------------------------
    tagged = client.tag(markdown=markdown_text, filename=filename)
    tags_json = json.dumps(tagged.get("tags", []))
    effort = tagged.get("effort_hours", settings.default_effort_hours)
    summary = tagged.get("summary", f"Material imported from {filename}.")

    # --- cluster ------------------------------------------------------------
    existing_topics = db.list_topics(conn)
    existing_hashes = [
        m["content_hash"]
        for m in db.list_materials(conn)
        if m["id"] != material_id   # exclude self (just created as processing)
    ]
    clustered = client.cluster(
        content_hash=ch,
        filename=filename,
        existing_topics=existing_topics,
        existing_material_hashes=existing_hashes,
        markdown=markdown_text,
        effort_hours=effort,
    )

    # The LLM may return a topic_id that isn't in the existing set: ignore it.
    topic_ids = {t["id"] for t in existing_topics}
    if clustered.get("topic_id") is not None and clustered["topic_id"] not in topic_ids:
        clustered = dict(clustered)
        clustered["topic_id"] = None

    relation = clustered.get("relation", "new")
    topic_id: str | None = None
    topic_name: str | None = None

    if relation == "duplicate":
        # Duplicate of an existing material: share its topic.
        dupe_material_id = clustered.get("topic_id")
        if dupe_material_id is not None:
            dupe = db.get_material(conn, dupe_material_id)
            topic_id = dupe["topic_id"]
            if topic_id is not None:
                topic_row = conn.execute(
                    "SELECT name FROM topics WHERE id = ?", (topic_id,)
                ).fetchone()
                topic_name = topic_row["name"] if topic_row else None
        # mark this material as a duplicate
        db.update_material(
            conn,
            material_id,
            status="duplicate",
            duplicate_of=dupe_material_id,
            topic_id=topic_id,
            summary=summary,
            tags=tags_json,
            effort_hours=effort,
            markdown_path=str(markdown_path),
        )
    elif relation == "version":
        # New version of an existing material: supersede the latest member of
        # the given topic.
        prev_topic_id = clustered.get("topic_id")
        if prev_topic_id is not None:
            prev_material = conn.execute(
                """
                SELECT * FROM materials
                WHERE topic_id = ? AND status != 'superseded'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (prev_topic_id,),
            ).fetchone()
            if prev_material is not None:
                db.update_material(
                    conn,
                    prev_material["id"],
                    status="superseded",
                    superseded_by=material_id,
                    status_reason=f"replaced by {filename}",
                )
            topic_id = prev_topic_id
            topic_name = clustered.get("topic_name", filename)
        else:
            topic_id = db.create_topic(
                conn, name=clustered.get("topic_name", filename), goal_id=material["goal_id"]
            )
            topic_name = clustered.get("topic_name", filename)
    else:
        # "new": reuse an existing topic id if given, otherwise create one.
        topic_id = clustered.get("topic_id")
        if topic_id is None:
            topic_name = clustered.get(
                "topic_name", tagged.get("topic", filename.rsplit(".", 1)[0])
            )
            topic_id = db.create_topic(conn, name=topic_name, goal_id=material["goal_id"])
        else:
            topic_row = conn.execute(
                "SELECT name FROM topics WHERE id = ?", (topic_id,)
            ).fetchone()
            topic_name = topic_row["name"] if topic_row else None

    db.update_material(
        conn,
        material_id,
        markdown_path=str(markdown_path),
        summary=summary,
        tags=tags_json,
        effort_hours=effort,
        status="ready",
        topic_id=topic_id,
    )
    conn.commit()

    result = dict(db.get_material(conn, material_id))
    result["topic_name"] = topic_name
    result["cluster_relation"] = relation
    result["cluster_reason"] = str(clustered.get("reason", ""))
    return result


def attach_to_goal(
    conn,
    material_id: str,
    goal_id: str,
    *,
    now_iso: str,
) -> dict[str, Any]:
    """Link a ready material to a goal and (re)plan its study slots."""
    material = db.get_material(conn, material_id)
    if material["status"] != "ready":
        raise ValueError(f"material {material_id} is not ready (status={material['status']})")

    db.update_material(conn, material_id, goal_id=goal_id)
    conn.commit()
    result = engine.replan(conn, goal_id, now_iso)
    conn.commit()
    return result


def merge_topics(
    conn,
    *,
    source_topic_id: str,
    target_topic_id: str,
    now_iso: str,
) -> dict[str, Any]:
    """Move all materials from ``source_topic_id`` into ``target_topic_id``.

    Deletes the source topic. Returns the target topic row.
    """
    conn.execute(
        "UPDATE materials SET topic_id = ? WHERE topic_id = ?",
        (target_topic_id, source_topic_id),
    )
    conn.execute("DELETE FROM topics WHERE id = ?", (source_topic_id,))
    conn.commit()

    row = conn.execute(
        "SELECT * FROM topics WHERE id = ?", (target_topic_id,)
    ).fetchone()
    return dict(row)
