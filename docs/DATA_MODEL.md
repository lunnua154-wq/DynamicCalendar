# Data model (SQLite, stdlib `sqlite3`, no ORM)

All timestamps stored as **UTC ISO-8601** strings, e.g. `2026-11-04T01:30:00Z`.
IDs are UUID4 text.

## Tables

```sql
events
  id            TEXT PRIMARY KEY
  kind          TEXT NOT NULL CHECK (kind IN ('external','study','revision'))
  ics_uid       TEXT NULL          -- external only: source UID (re-import dedup)
  ics_rrule     TEXT NULL          -- external only: raw RRULE if present
  source        TEXT NULL          -- which .ics file it came from
  title         TEXT NOT NULL
  dtstart       TEXT NOT NULL      -- UTC ISO-8601
  dtend         TEXT NOT NULL
  all_day       INTEGER NOT NULL DEFAULT 0
  venue         TEXT NULL          -- LOCATION (shown on P1/P2 for external)
  description   TEXT NULL
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
  goal_id    TEXT NULL REFERENCES goals(id)   -- NULL = unattached topic
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
  status         TEXT NOT NULL DEFAULT 'new'
                 CHECK (status IN ('new','processing','ready','tagged','duplicate','superseded','error'))
  duplicate_of   TEXT NULL REFERENCES materials(id)
  superseded_by  TEXT NULL REFERENCES materials(id)   -- set when a newer version replaces this
  status_reason  TEXT NULL                            -- reason for error/superseded status
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
  parent_job_id  TEXT NULL REFERENCES jobs(id) -- revision -> the completed learn job
  pinned         INTEGER NOT NULL DEFAULT 0    -- 1 = user dragged it; replan keeps it
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

## Default settings (placeholder "fake time" — dynamic, changeable)

| key | default | bounds |
|---|---|---|
| `daily_capacity_hours` | `4.0` | 0.5 – 12 |
| `slot_minutes` | `50` | 15 – 120 |
| `min_slot_minutes` | `30` | 15 – 120 |
| `revision_intervals_days` | `[1,3,7,14]` | each 1 – 60, ascending |
| `llm_base_url` | `""` (→ offline mode) | — |
| `llm_model` | `""` | — |
| `llm_api_key` | `""` | — |
| `default_effort_hours` | `1.0` | 0.1 – 20 |
| `ocr_provider` | `"off"` | `off` \| `cloudflare` |
| `cf_account_id` | `""` | — |
| `cf_api_token` | `""` | — |
| `ocr_model` | `"@cf/unum/uform-gen2-qwen-500m"` | — |
| `ocr_min_chars` | `40` | 1 – 10000 |

## ICS mapping (import — the only write path to external events)

| ICS | → | column |
|---|---|---|
| `UID` | → | `ics_uid` (re-import: update same event, skip if unchanged) |
| `SUMMARY` | → | `title` |
| `DTSTART` / `DTEND` | → | UTC via `zoneinfo` |
| `LOCATION` | → | `venue` |
| `DESCRIPTION` | → | `description` |
| `RRULE` | → | `ics_rrule`; M0 expands a 30-day horizon into concrete events |
| `CATEGORIES` contains `exam` (case-insens.) | → | surfaced as goal deadline candidate (auto-link if goal open) |

## Rules
- `kind='external'` ⇒ `locked=1`; engine code has no write path to locked rows (enforced in `db.py`).
- `kind IN ('study','revision')` ⇒ exactly one `jobs` row (`event_id` unique in `jobs`).
- A completed job keeps its event (history) — the calendar shows what actually happened.
- A `pinned` job is a user placement: `replan` never moves or deletes it. Only
  unpinned `pending`/`conflicted` jobs are re-derived.
- A `revision` job records `parent_job_id`; replan uses it to avoid duplicating a
  spaced-repetition round that is already done/skipped/pinned.
- Deletions are soft: materials → `superseded`; jobs → `skipped`; goals → `archived`.
