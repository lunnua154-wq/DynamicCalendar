# API (M0–M3)

Base: `http://127.0.0.1:8000` — JSON in/out except upload endpoints (multipart).
All dates in queries: `YYYY-MM-DD` (local day, server converts with user zone).

## Pages
| Route | Serves |
|---|---|
| `GET /` | `web/calendar.html` (P1) |
| `GET /week` | `web/week.html` (P2) |
| `GET /inbox` | `web/inbox.html` (P3) |

## Calendar
| Method & path | Body | Returns |
|---|---|---|
| `GET /api/events?from=&to=` | — | `[{id, kind, title, dtstart, dtend, venue, all_day, job?}]` — `job: {id, status}` attached for study/revision kinds |
| `POST /api/ics/import` | multipart `file` (.ics) | `{imported, skipped, exams_found: [titles]}` |

## Goals
| Method & path | Body | Returns |
|---|---|---|
| `GET /api/goals` | — | `[{id, title, deadline, exam_event_id, progress: 0–1}]` |
| `POST /api/goals` | `{title, deadline?, exam_event_id?}` | goal row; triggers `replan` if deadline set |
| `POST /api/goals/{id}/replan` | — | `{moved_jobs: [ids]}` |

## Jobs
| Method & path | Body | Returns |
|---|---|---|
| `GET /api/jobs?from=&to=` | — | `[{id, kind, title, material_id, slot_start, slot_end, duration_min, status, revision_round}]` |
| `POST /api/jobs/{id}/complete` | — | `{replanned: [ids], revisions_added: [ids]}` — **one atomic transaction** (see ARCHITECTURE) |
| `POST /api/jobs/{id}/move` | `{slot_start, slot_end}` | job row — reflows the rest; 409 if overlapping an external event |
| `POST /api/jobs/{id}/skip` | — | job row (`status='skipped'`) |

## Inbox
| Method & path | Body | Returns |
|---|---|---|
| `POST /api/inbox/upload` | multipart `files[]` | `[{filename, status: 'processing'\|'ready'\|'error', material_id?}]` (processing completes async; clients poll next) |
| `GET /api/inbox/files` | — | `[{id, filename, summary, tags, effort_hours, status, duplicate_of?}]` |
| `POST /api/inbox/files/{id}/dismiss` | `{action: 'duplicate'\|'superseded'}` | material row — user override of the LLM's call |
| `POST /api/inbox/files/{id}/effort` | `{hours}` | material row — user override of estimate |

## Chat
| Method & path | Body | Returns |
|---|---|---|
| `POST /api/chat` | `{message}` | `{reply, llm: 'on'\|'off'}` — `'off'` = fallback path was used |

## Settings
| Method & path | Body | Returns |
|---|---|---|
| `GET /api/settings` | — | all keys with values + `updated_by` |
| `PUT /api/settings` | `{"daily_capacity_hours": 5, ...}` | updated rows — bounds-validated (422 on out-of-bounds); LLM-suggested values arrive via the chat path, flagged `updated_by='llm'` |

## Errors
`{"error": "<message>"}` with proper status codes: 404 not found, 409 conflict
(e.g. job move overlaps external event), 422 validation, 500 unexpected.
