"""SQLite data layer (stdlib ``sqlite3``, no ORM).

All timestamps are stored as UTC ISO-8601 strings. All queries are
parameterized — never f-string SQL. IDs are UUID4 text.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DEFAULT_SETTINGS, HOST_OFFSET_SETTING, host_utc_offset_minutes
from .errors import ConflictError, NotFoundError

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id            TEXT PRIMARY KEY,
  kind          TEXT NOT NULL CHECK (kind IN ('external','study','revision')),
  ics_uid       TEXT NULL,
  ics_rrule     TEXT NULL,
  source        TEXT NULL,
  title         TEXT NOT NULL,
  dtstart       TEXT NOT NULL,
  dtend         TEXT NOT NULL,
  all_day       INTEGER NOT NULL DEFAULT 0,
  venue         TEXT NULL,
  description   TEXT NULL,
  locked        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS goals (
  id             TEXT PRIMARY KEY,
  title          TEXT NOT NULL,
  deadline       TEXT NOT NULL,
  exam_event_id  TEXT NULL REFERENCES events(id),
  status         TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','archived')),
  created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
  id         TEXT PRIMARY KEY,
  goal_id    TEXT NULL REFERENCES goals(id),   -- NULL = unattached (no goal yet)
  name       TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS materials (
  id             TEXT PRIMARY KEY,
  goal_id        TEXT NULL REFERENCES goals(id),
  topic_id       TEXT NULL REFERENCES topics(id),
  filename       TEXT NOT NULL,
  original_path  TEXT NOT NULL,
  markdown_path  TEXT NOT NULL,
  content_hash   TEXT NOT NULL,
  summary        TEXT NULL,
  tags           TEXT NULL,
  effort_hours   REAL NULL,
  status         TEXT NOT NULL DEFAULT 'new'
                 CHECK (status IN ('new','processing','ready','tagged','duplicate','superseded','error')),
  duplicate_of   TEXT NULL REFERENCES materials(id),
  superseded_by  TEXT NULL REFERENCES materials(id),  -- set when a newer version replaces this
  status_reason  TEXT NULL,                           -- reason for error/superseded status
  created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id             TEXT PRIMARY KEY,
  event_id       TEXT NOT NULL REFERENCES events(id),
  goal_id        TEXT NOT NULL REFERENCES goals(id),
  material_id    TEXT NOT NULL REFERENCES materials(id),
  kind           TEXT NOT NULL CHECK (kind IN ('learn','revision')),
  duration_min   INTEGER NOT NULL,
  status         TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','done','skipped','conflicted')),
  done_at        TEXT NULL,
  revision_round INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL,
  pinned         INTEGER NOT NULL DEFAULT 0,
  parent_job_id  TEXT NULL REFERENCES jobs(id),
  placement_source TEXT NOT NULL DEFAULT 'engine',
  placement_note TEXT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL,
  updated_by  TEXT NOT NULL DEFAULT 'default'
              CHECK (updated_by IN ('default','user','llm')),
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id          TEXT PRIMARY KEY,
  role        TEXT NOT NULL CHECK (role IN ('user','assistant')),
  content     TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_annotations (
  event_id    TEXT PRIMARY KEY REFERENCES events(id),
  subject     TEXT NOT NULL DEFAULT 'general',
  fatigue     INTEGER NOT NULL DEFAULT 0 CHECK (fatigue BETWEEN 0 AND 3),
  source      TEXT NOT NULL DEFAULT 'heuristic'
              CHECK (source IN ('heuristic','llm','user')),
  note        TEXT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS constraints (
  id          TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,
  params      TEXT NOT NULL,
  enabled     INTEGER NOT NULL DEFAULT 1,
  hard        INTEGER NOT NULL DEFAULT 0,
  source      TEXT NOT NULL DEFAULT 'user' CHECK (source IN ('user','llm')),
  label       TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_event_id ON jobs(event_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_ics_uid ON events(ics_uid) WHERE ics_uid IS NOT NULL;
"""


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    return uuid.uuid4().hex


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate_jobs_columns(conn)
    _seed_default_settings(conn)
    conn.commit()


def _migrate_jobs_columns(conn: sqlite3.Connection) -> None:
    """Light migrations for existing databases created before the ``pinned`` /
    ``parent_job_id`` / placement columns existed. Safe to run on a fresh DB
    (columns are already present via SCHEMA) and on an old DB (columns are
    added in place)."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "pinned" not in existing:
        conn.execute("ALTER TABLE jobs ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
    if "parent_job_id" not in existing:
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN parent_job_id TEXT NULL REFERENCES jobs(id)"
        )
    if "placement_source" not in existing:
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN placement_source TEXT NOT NULL DEFAULT 'engine'"
        )
    if "placement_note" not in existing:
        conn.execute("ALTER TABLE jobs ADD COLUMN placement_note TEXT NULL")


def _seed_default_settings(conn: sqlite3.Connection) -> None:
    seeded = dict(DEFAULT_SETTINGS)
    # Local clock offset: seeded once from the host, then a normal dynamic
    # setting, so the engine stays a pure function of (db, settings, now).
    seeded.setdefault(HOST_OFFSET_SETTING, str(host_utc_offset_minutes()))
    for key, value in seeded.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value, updated_by, updated_at)"
            " VALUES (?, ?, 'default', ?)",
            (key, value, now_utc()),
        )


# ---------------------------------------------------------------- events

def list_events(
    conn: sqlite3.Connection, from_iso: str | None = None, to_iso: str | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM events"
    params: list[Any] = []
    if from_iso is not None or to_iso is not None:
        clauses = []
        if from_iso is not None:
            clauses.append("dtend > ?")
            params.append(from_iso)
        if to_iso is not None:
            clauses.append("dtstart < ?")
            params.append(to_iso)
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY dtstart, title, id"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_event(conn: sqlite3.Connection, event_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"event {event_id} not found")
    return dict(row)


def create_event(
    conn: sqlite3.Connection,
    *,
    kind: str,
    title: str,
    dtstart: str,
    dtend: str,
    all_day: int = 0,
    venue: str | None = None,
    description: str | None = None,
    locked: int = 0,
    ics_uid: str | None = None,
    ics_rrule: str | None = None,
    source: str | None = None,
) -> str:
    event_id = new_id()
    conn.execute(
        """
        INSERT INTO events
          (id, kind, ics_uid, ics_rrule, source, title, dtstart, dtend,
           all_day, venue, description, locked)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            kind,
            ics_uid,
            ics_rrule,
            source,
            title,
            dtstart,
            dtend,
            all_day,
            venue,
            description,
            locked,
        ),
    )
    return event_id


def update_event(
    conn: sqlite3.Connection, event_id: str, **fields: Any
) -> dict[str, Any]:
    if not fields:
        return get_event(conn, event_id)
    get_event(conn, event_id)  # raises if missing
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [event_id]
    conn.execute(f"UPDATE events SET {cols} WHERE id = ?", values)
    return get_event(conn, event_id)


def delete_event(conn: sqlite3.Connection, event_id: str) -> None:
    get_event(conn, event_id)  # raises if missing
    conn.execute("DELETE FROM events WHERE id = ?", (event_id,))


# ---------------------------------------------------------------- goals

def list_goals(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM goals ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]


def get_goal(conn: sqlite3.Connection, goal_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"goal {goal_id} not found")
    return dict(row)


def create_goal(
    conn: sqlite3.Connection,
    *,
    title: str,
    deadline: str,
    exam_event_id: str | None = None,
) -> str:
    goal_id = new_id()
    conn.execute(
        "INSERT INTO goals(id, title, deadline, exam_event_id, status, created_at)"
        " VALUES (?, ?, ?, ?, 'open', ?)",
        (goal_id, title, deadline, exam_event_id, now_utc()),
    )
    return goal_id


# ---------------------------------------------------------------- jobs

def list_jobs(
    conn: sqlite3.Connection, from_iso: str | None = None, to_iso: str | None = None
) -> list[dict[str, Any]]:
    sql = """
      SELECT j.*, e.title AS event_title, e.dtstart AS slot_start, e.dtend AS slot_end,
             e.kind AS event_kind
      FROM jobs j JOIN events e ON e.id = j.event_id
    """
    params: list[Any] = []
    clauses = []
    if from_iso is not None:
        clauses.append("e.dtend > ?")
        params.append(from_iso)
    if to_iso is not None:
        clauses.append("e.dtstart < ?")
        params.append(to_iso)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY e.dtstart, e.title, e.id"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_job(conn: sqlite3.Connection, job_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT j.*, e.title AS event_title, e.dtstart AS slot_start, e.dtend AS slot_end
        FROM jobs j JOIN events e ON e.id = j.event_id
        WHERE j.id = ?
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"job {job_id} not found")
    return dict(row)


def create_job(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    goal_id: str,
    material_id: str,
    kind: str,
    duration_min: int,
    status: str = "pending",
    revision_round: int = 0,
    pinned: int = 0,
    parent_job_id: str | None = None,
    placement_source: str = "engine",
    placement_note: str | None = None,
) -> str:
    job_id = new_id()
    conn.execute(
        """
        INSERT INTO jobs
          (id, event_id, goal_id, material_id, kind, duration_min, status,
           revision_round, created_at, pinned, parent_job_id,
           placement_source, placement_note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            event_id,
            goal_id,
            material_id,
            kind,
            duration_min,
            status,
            revision_round,
            now_utc(),
            pinned,
            parent_job_id,
            placement_source,
            placement_note,
        ),
    )
    return job_id


def update_job(conn: sqlite3.Connection, job_id: str, **fields: Any) -> dict[str, Any]:
    if not fields:
        return get_job(conn, job_id)
    get_job(conn, job_id)  # raises if missing
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [job_id]
    conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", values)
    return get_job(conn, job_id)


def jobs_for_goal(conn: sqlite3.Connection, goal_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT j.*, e.dtstart AS slot_start, e.dtend AS slot_end
        FROM jobs j JOIN events e ON e.id = j.event_id
        WHERE j.goal_id = ?
        ORDER BY e.dtstart, j.id
        """,
        (goal_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- settings

def list_settings(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM settings ORDER BY key").fetchall()
    return [dict(r) for r in rows]


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]


def update_setting(
    conn: sqlite3.Connection, key: str, value: str, updated_by: str = "user"
) -> dict[str, Any]:
    conn.execute(
        """
        INSERT INTO settings(key, value, updated_by, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
          value = excluded.value,
          updated_by = excluded.updated_by,
          updated_at = excluded.updated_at
        """,
        (key, value, updated_by, now_utc()),
    )
    row = conn.execute(
        "SELECT key, value, updated_by FROM settings WHERE key = ?", (key,)
    ).fetchone()
    return dict(row)  # type: ignore[return-value]


# ---------------------------------------------------------------- materials & topics

def create_topic(
    conn: sqlite3.Connection, *, name: str, goal_id: str | None = None
) -> str:
    topic_id = new_id()
    conn.execute(
        "INSERT INTO topics(id, goal_id, name, created_at) VALUES (?, ?, ?, ?)",
        (topic_id, goal_id, name, now_utc()),
    )
    return topic_id


def create_material(
    conn: sqlite3.Connection,
    *,
    material_id: str | None = None,
    goal_id: str | None,
    topic_id: str | None,
    filename: str,
    original_path: str,
    markdown_path: str,
    content_hash: str,
    summary: str | None,
    tags: str | None,
    effort_hours: float | None,
    status: str = "new",
    duplicate_of: str | None = None,
) -> str:
    material_id = material_id or new_id()
    conn.execute(
        """
        INSERT INTO materials
          (id, goal_id, topic_id, filename, original_path, markdown_path,
           content_hash, summary, tags, effort_hours, status, duplicate_of, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            material_id,
            goal_id,
            topic_id,
            filename,
            original_path,
            markdown_path,
            content_hash,
            summary,
            tags,
            effort_hours,
            status,
            duplicate_of,
            now_utc(),
        ),
    )
    return material_id


def list_materials(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM materials ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def list_topics(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT t.id, t.goal_id, t.name, t.created_at,
               COUNT(m.id) AS file_count
        FROM topics t
        LEFT JOIN materials m ON m.topic_id = t.id
        GROUP BY t.id, t.goal_id, t.name, t.created_at
        ORDER BY t.created_at DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_topic(conn: sqlite3.Connection, topic_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"topic {topic_id} not found")
    return dict(row)


def update_topic(
    conn: sqlite3.Connection, topic_id: str, **fields: Any
) -> dict[str, Any]:
    if not fields:
        return get_topic(conn, topic_id)
    get_topic(conn, topic_id)  # raises if missing
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [topic_id]
    conn.execute(f"UPDATE topics SET {cols} WHERE id = ?", values)
    return get_topic(conn, topic_id)


def get_material(conn: sqlite3.Connection, material_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM materials WHERE id = ?", (material_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError(f"material {material_id} not found")
    return dict(row)


def update_material(
    conn: sqlite3.Connection, material_id: str, **fields: Any
) -> dict[str, Any]:
    if not fields:
        return get_material(conn, material_id)
    get_material(conn, material_id)  # raises if missing
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [material_id]
    conn.execute(f"UPDATE materials SET {cols} WHERE id = ?", values)
    return get_material(conn, material_id)


def find_material_by_hash(conn: sqlite3.Connection, content_hash: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM materials WHERE content_hash = ? "
        "ORDER BY created_at ASC LIMIT 1",
        (content_hash,),
    ).fetchone()
    return None if row is None else dict(row)


def find_original_material_by_hash(
    conn: sqlite3.Connection, content_hash: str
) -> dict[str, Any] | None:
    """Return the root (non-duplicate) material for a content hash.

    Prefers the earliest non-duplicate row; if every row with this hash is a
    duplicate, follows the ``duplicate_of`` chain back to the original so a
    new duplicate is always linked to the original (never to another duplicate).
    """
    row = conn.execute(
        "SELECT * FROM materials WHERE content_hash = ? AND status != 'duplicate'"
        " ORDER BY created_at ASC LIMIT 1",
        (content_hash,),
    ).fetchone()
    if row is not None:
        return dict(row)
    row = conn.execute(
        "SELECT * FROM materials WHERE content_hash = ? ORDER BY created_at ASC LIMIT 1",
        (content_hash,),
    ).fetchone()
    if row is None:
        return None
    cur = dict(row)
    while cur.get("duplicate_of"):
        parent = conn.execute(
            "SELECT * FROM materials WHERE id = ?", (cur["duplicate_of"],)
        ).fetchone()
        if parent is None:
            break
        cur = dict(parent)
    return cur



# ---------------------------------------------------------------- chat

def add_chat_message(
    conn: sqlite3.Connection, *, role: str, content: str
) -> str:
    message_id = new_id()
    conn.execute(
        "INSERT INTO chat_messages(id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (message_id, role, content, now_utc()),
    )
    return message_id


def list_chat_messages(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM chat_messages ORDER BY created_at, rowid"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- annotations

def upsert_annotation(
    conn: sqlite3.Connection,
    event_id: str,
    *,
    subject: str,
    fatigue: int,
    source: str,
    note: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO event_annotations(event_id, subject, fatigue, source, note, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO UPDATE SET
          subject = excluded.subject,
          fatigue = excluded.fatigue,
          source = excluded.source,
          note = excluded.note,
          updated_at = excluded.updated_at
        """,
        (event_id, subject, int(fatigue), source, note, now_utc()),
    )


def annotated_event_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT event_id FROM event_annotations").fetchall()
    return {row["event_id"] for row in rows}


def list_annotations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM event_annotations").fetchall()
    return [dict(r) for r in rows]


def delete_annotations_except(
    conn: sqlite3.Connection, event_ids: set[str]
) -> None:
    """Drop annotations for events that no longer exist (after a re-import
    removes an external). Never touches constraint data."""
    if event_ids:
        placeholders = ", ".join("?" for _ in event_ids)
        conn.execute(
            f"DELETE FROM event_annotations WHERE event_id NOT IN ({placeholders})",
            tuple(event_ids),
        )
    else:
        conn.execute("DELETE FROM event_annotations")


# ---------------------------------------------------------------- constraints

def list_constraints(
    conn: sqlite3.Connection, *, enabled_only: bool = False
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM constraints"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY created_at, id"
    rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def create_constraint(
    conn: sqlite3.Connection,
    *,
    kind: str,
    params: str,  # already-serialised JSON (constraints.serialise_params)
    hard: bool,
    source: str,
    label: str,
    enabled: bool = True,
) -> str:
    constraint_id = new_id()
    conn.execute(
        """
        INSERT INTO constraints(id, kind, params, enabled, hard, source, label, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (constraint_id, kind, params, int(enabled), int(hard), source, label, now_utc()),
    )
    return constraint_id


def delete_constraint(conn: sqlite3.Connection, constraint_id: str) -> bool:
    cur = conn.execute("DELETE FROM constraints WHERE id = ?", (constraint_id,))
    return cur.rowcount > 0


def set_constraint_enabled(
    conn: sqlite3.Connection, constraint_id: str, enabled: bool
) -> bool:
    cur = conn.execute(
        "UPDATE constraints SET enabled = ? WHERE id = ?", (int(enabled), constraint_id)
    )
    return cur.rowcount > 0
