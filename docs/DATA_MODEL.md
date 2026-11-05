# Data model (SQLite, stdlib `sqlite3`, no ORM)

All timestamps stored as **UTC ISO-8601** strings, e.g. `2026-11-04T01:30:00Z`.
IDs are UUID4 text. Schema version: `PRAGMA user_version = 1` (a database with a
different version is rejected with an instruction to delete `data/dynacalendar.db` —
there is no released data to migrate yet).

## Tables

```sql
events
  id            TEXT PRIMARY KEY
  kind          TEXT NOT NULL CHECK (kind IN ('external','study','revision'))
  ics_uid       TEXT NULL          -- external only: source UID (re-import dedup)
  ics_rrule     TEXT NULL          -- external only: raw RRULE if present
  source        TEXT NULL          -- which .ics file it came from
  title         TEXT NOT NULL      -- study/revision: filled in by the API from the material
  dtstart       TEXT NOT NULL      -- UTC ISO-8601
  dtend         TEXT NOT NULL
  all_day       INTEGER NOT NULL DEFAULT 0
  venue         TEXT NULL          -- LOCATION (shown on P1/P2 for external)
  description   TEXT NULL
  categories    TEXT NULL          -- JSON array (CATEGORIES); 'EXAM' drives the banner
  locked        INTEGER NOT NULL DEFAULT 0   -- 1 for external: engine never writes

goals
  id             TEXT PRIMARY KEY
  title          TEXT NOT NULL
  deadline       TEXT NOT NULL
  exam_event_id  TEXT NULL REFERENCES events(id)   -- auto-linked if tagged 'exam'
  status         TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','archived'))
  created_at     TEXT NOT NULL

topics
  id         TEXT PRIMARY KEY
  goal_id    TEXT NULL REFERENCES goals(id)   -- NULL while a topic is unattached
  name       TEXT NOT NULL
  created_at TEXT NOT NULL

materials
  id             TEXT PRIMARY KEY
  goal_id        TEXT NULL REFERENCES goals(id)
  topic_id       TEXT NULL REFERENCES topics(id)
  filename       TEXT NOT NULL
  original_path  TEXT NOT NULL
  markdown_path  TEXT NOT NULL
  content_hash   TEXT NOT NULL                -- sha256; deterministic dedup key
  summary        TEXT NULL                    -- llm.tag() (or fallback)
  tags           TEXT NULL                    -- JSON array of strings
  effort_hours   REAL NULL                    -- llm estimate, user-overridable
  status         TEXT NOT NULL DEFAULT 'processing'
                 CHECK (status IN ('processing','ready','duplicate','superseded','error'))
  status_reason  TEXT NULL                    -- why an error/duplicate happened
  duplicate_of   TEXT NULL REFERENCES materials(id)  -- byte-identical earlier file
  superseded_by  TEXT NULL REFERENCES materials(id)  -- newer version that replaced this
  created_at     TEXT NOT NULL

jobs
  id             TEXT PRIMARY KEY
  event_id       TEXT NOT NULL REFERENCES events(id)   -- 1:1, job IS a calendar event
  goal_id        TEXT NOT NULL REFERENCES goals(id)
  material_id    TEXT NOT NULL REFERENCES materials(id)
  kind           TEXT NOT NULL CHECK (kind IN ('learn','revision'))
  duration_min   INTEGER NOT NULL
  status         TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','done','skipped','conflicted'))
  done_at        TEXT NULL
  revision_round INTEGER NOT NULL DEFAULT 0   -- 0=learn, 1..4=revision round n
  pinned         INTEGER NOT NULL DEFAULT 0   -- 1 = moved by hand: replans keep it
  created_at     TEXT NOT NULL

settings
  key         TEXT PRIMARY KEY            -- e.g. 'daily_capacity_hours'
  value       TEXT NOT NULL               -- JSON value
  updated_by  TEXT NOT NULL DEFAULT 'default'
              CHECK (updated_by IN ('default','user','llm'))
  updated_at  TEXT NOT NULL

chat_messages
  id          TEXT PRIMARY KEY
  role        TEXT NOT NULL CHECK (role IN ('user','assistant'))
  content     TEXT NOT NULL
  created_at  TEXT NOT NULL
```

## Material lifecycle (M1 ingest)

```
upload ──► processing ──► ready            (converted, tagged, clustered)
                      ├─► duplicate        (identical content_hash already stored)
                      ├─► superseded       (a newer version replaced it)
                      └─► error            (unsupported format / conversion failure)
```

`status_reason` carries the human sentence for `error` and `duplicate` rows. The
Inbox row shows `result` computed by the API (`new topic "X"`, `duplicate of Y`,
`version update of Y`, `unsupported format (.xyz)`).

## Default settings (placeholder "fake time" — dynamic, changeable)

| key | default | bounds |
|---|---|---|
| `daily_capacity_hours` | `4.0` | 0.5 – 12 |
| `slot_minutes` | `50` | 15 – 120 |
| `min_slot_minutes` | `30` | 15 – 120 |
| `revision_intervals_days` | `[1,3,7,14]` | each 1 – 60, ascending and unique |
| `utc_offset_minutes` | host offset at first run (e.g. `480`) | −840 – 840 |
| `llm_base_url` | `""` (→ offline mode) | — |
| `llm_model` | `""` | — |
| `llm_api_key` | `""` | — |
| `default_effort_hours` | `1.0` | 0.1 – 20 |

`utc_offset_minutes` is applied only when the engine lays out a day: the study
window is **08:00–22:00 local**, stored back as UTC. Storage and the API stay UTC;
the browser renders in the viewer's local time.

## ICS mapping (import — the only write path to external events)

| ICS | → | column |
|---|---|---|
| `UID` | → | `ics_uid` (re-import: update in place, skip when unchanged) |
| `SUMMARY` | → | `title` |
| `DTSTART` / `DTEND` | → | UTC via `zoneinfo`; missing `DTEND` → `DURATION`, else +60 min (all-day: +1 day) |
| `LOCATION` | → | `venue` |
| `DESCRIPTION` | → | `description` |
| `CATEGORIES` | → | `categories` (JSON); `EXAM` (any case) surfaces a goal-deadline candidate |
| `RRULE` | → | `ics_rrule` (raw). M0 imports the DTSTART instance only — expansion is post-M4 |

## Rules
- `kind='external'` ⇒ `locked=1`; `db.delete_event` refuses locked rows, so the engine
  has no write path to external events.
- `kind IN ('study','revision')` ⇒ exactly one `jobs` row (`event_id` unique in `jobs`).
- A completed job keeps its event (history) — the calendar shows what actually happened.
- `conflicted` revision rounds are derived state: each replan drops them and re-derives,
  so they never pile up.
- A replan reuses a pending job when the recomputed slot is identical (no ID churn);
  only jobs whose slot actually changed are reported as `moved_jobs`.
- Deletions are soft: materials → `superseded`; jobs → `skipped`; goals → `archived`.
