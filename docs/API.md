# API (M0–M3)

Base: `http://127.0.0.1:8000` — JSON in/out except the two multipart endpoints
(`POST /api/ics/import`, `POST /api/inbox/upload`).

Conventions:
- Timestamps are **UTC ISO-8601** strings (`2026-11-03T01:00:00Z`).
- `from` / `to` query parameters are **local calendar dates** (`YYYY-MM-DD`); the
  server converts them with the `utc_offset_minutes` setting. `to` is inclusive.
- Errors are `{"error": "<message>"}` with status 404, 409, 422 or 500.

## Pages
| Route | Serves |
|---|---|
| `GET /` | `web/calendar.html` (P1) |
| `GET /week` | `web/week.html` (P2) |
| `GET /inbox` | `web/inbox.html` (P3) |

## Calendar
| Method & path | Body | Returns |
|---|---|---|
| `GET /api/events?from=&to=` | — | `[{id, kind, title, dtstart, dtend, all_day, venue, description, categories[], locked, job?}]` — `job` is attached for `study`/`revision` events |
| `POST /api/ics/import` | multipart `file` (.ics) | `{imported, skipped, exams_found: [titles], exam_events: [{id, title, dtstart, dtend, venue}]}` |

`job` shape: `{id, event_id, goal_id, kind, status, material_id, material_title, topic_name, duration_min, revision_round, slot_start, slot_end}`.
An import also re-plans every open goal, because the external layout changed.

## Goals
| Method & path | Body | Returns |
|---|---|---|
| `GET /api/goals` | — | `[{id, title, deadline, exam_event_id, status, progress, done_hours, total_hours}]` |
| `POST /api/goals` | `{title, deadline?, exam_event_id?}` | goal row; `deadline` may be `YYYY-MM-DD` (→ UTC midnight) or an ISO timestamp; omitted when linked to an exam event. Triggers a replan |
| `POST /api/goals/{id}/replan` | — | `{moved_jobs: [ids], revisions_added: [ids], conflicted: []}` |

## Jobs
| Method & path | Body | Returns |
|---|---|---|
| `GET /api/jobs?from=&to=` | — | `[{id, goal_id, kind, status, material_id, material_title, topic_name, duration_min, revision_round, slot_start, slot_end}]` |
| `POST /api/jobs/{id}/complete` | — | `{job, replanned: [ids], revisions_added: [ids]}` — **one atomic transaction** (see ARCHITECTURE). 409 if the job is not pending |
| `POST /api/jobs/{id}/move` | `{slot_start, slot_end?}` | `{job, replanned: [ids]}` — 409 if it would overlap an external event; the job is pinned so later replans keep it |
| `POST /api/jobs/{id}/skip` | — | job row (`status='skipped'`); 409 if the job is not pending |

## Inbox
| Method & path | Body | Returns |
|---|---|---|
| `POST /api/inbox/upload` | multipart `files[]`, optional `goal_id` | `[{filename, status: 'processing'\|'duplicate', material_id, duplicate_of}]` — `processing` rows settle asynchronously; poll the list |
| `GET /api/inbox/files` | — | `[{id, filename, summary, tags[], effort_hours, status, status_reason, result, duplicate_of, superseded_by, topic_id, topic_name, goal_id, created_at}]` |
| `GET /api/materials/{id}` | — | material row + `markdown` (the converted text) |
| `POST /api/inbox/files/{id}/dismiss` | `{action: 'duplicate'\|'superseded'\|'ready'}` | material row — user override of the automatic call |
| `POST /api/inbox/files/{id}/effort` | `{hours}` | material row — bounds-validated (0.1–20) |
| `POST /api/inbox/files/{id}/attach` | `{goal_id}` | material row — attaches/detaches and replans both goals |

`status` is one of `processing`, `ready`, `duplicate`, `superseded`, `error`
(`status_reason` explains `error`). `result` is the human sentence shown in P3
(`new topic "X"`, `duplicate of Y`, `version update of Y`, `unsupported format (.xyz)`).

## Topics
| Method & path | Body | Returns |
|---|---|---|
| `GET /api/topics` | — | `[{id, name, goal_id, file_count, effort_hours}]` |
| `POST /api/topics/{id}/rename` | `{name}` | topic row |
| `POST /api/topics/{id}/merge` | `{target_id}` | target topic row — members re-attach and affected goals replan |

## Chat
| Method & path | Body | Returns |
|---|---|---|
| `POST /api/chat` | `{message}` | `{reply, llm: 'on'\|'off'}` — `'off'` = deterministic fallback (no LLM configured) |
| `GET /api/chat` | — | `[{id, role, content, created_at}]` |

## Settings
| Method & path | Body | Returns |
|---|---|---|
| `GET /api/settings` | — | `[{key, value, updated_by, updated_at}]` |
| `PUT /api/settings` | `{"daily_capacity_hours": 5, ...}` | `{settings: [rows], engine: {...}}` — 422 on unknown keys or out-of-bounds values; updates re-plan every open goal |

Writable keys and bounds: `daily_capacity_hours` (0.5–12), `slot_minutes` (15–120),
`min_slot_minutes` (15–120), `default_effort_hours` (0.1–20),
`revision_intervals_days` (list, each 1–60, ascending), `utc_offset_minutes` (−840–840),
`llm_base_url`, `llm_model`, `llm_api_key`. LLM-suggested values arrive through the
chat path flagged `updated_by='llm'`.
