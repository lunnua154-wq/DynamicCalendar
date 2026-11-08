# API (M0–M5)

Base: `http://127.0.0.1:8000` — JSON in/out except upload endpoints (multipart).
All dates in queries: `YYYY-MM-DD` (local day, server converts with user zone).
All timestamps in responses: UTC ISO-8601 `YYYY-MM-DDTHH:MM:SSZ`.

**Body convention (M5):** endpoints documented with a JSON body accept
`application/json`. They also accept `application/x-www-form-urlencoded` for
backward compatibility (the web client uses JSON). Upload endpoints are always
multipart.

## Pages
| Route | Serves |
|---|---|
| `GET /` | `web/calendar.html` (P1) |
| `GET /week` | `web/week.html` (P2) |
| `GET /inbox` | `web/inbox.html` (P3) |

## Calendar
| Method & path | Body | Returns |
|---|---|---|
| `GET /api/events?from=&to=` | — | `[{id, kind, title, dtstart, dtend, venue, all_day, job?}]` — `job: {id, status, kind, material_id, revision_round, duration_min, pinned}` attached for study/revision kinds |
| `POST /api/ics/import` | multipart `file` (.ics) | `{imported, skipped, exams_found: [titles], exams: [{event_id, title, dtstart, dtend, venue}], replanned_goals: [ids]}` |
| `GET /api/stats?goal_id=` | — | `{totals: {planned_min, done_min, progress, materials, revisions_due}, goals: [...], by_day: [{date, planned_min, done_min}]}` — `by_day` covers the trailing 13 days and the next 14; unknown `goal_id` → **404** |

`exams` is additive (kept alongside `exams_found`) so the Calendar exam banner can
create a goal linked to the real exam event and its deadline. `replanned_goals` is
also additive: the import replans **every open goal** after the events are written,
so new/changed external events never leave jobs silently overlapping them (pinned
jobs that an import clobbers are deleted and re-placed unpinned).

`from` / `to` are **local calendar dates** (`YYYY-MM-DD`) and are inclusive: `from`
starts at 00:00 and `to` covers the whole day. Same parameters on `GET /api/jobs`.
ISO-8601 timestamps are also accepted (then they are exact, `to` exclusive).

## Goals
| Method & path | Body | Returns |
|---|---|---|
| `GET /api/goals` | — | `[{id, title, deadline, exam_event_id, status, progress, total_effort_hours, done_effort_hours, planned_jobs, done_jobs}]` |
| `GET /api/goals/{id}` | — | same row + progress fields |
| `POST /api/goals` | `{title, deadline?, exam_event_id?}` | goal row + progress; triggers `replan` if a deadline is known |
| `POST /api/goals/{id}/replan` | — | `{moved_jobs: [ids], revisions_added: [ids], moved_count}` |

`deadline` may be omitted when `exam_event_id` is supplied: the server derives it
from that external event. If neither is present → **422**.

## Jobs
| Method & path | Body | Returns |
|---|---|---|
| `GET /api/jobs?from=&to=` | — | `[{id, kind, title, material_id, material_title, slot_start, slot_end, duration_min, status, revision_round, pinned}]` — `title` = the file name |
| `POST /api/jobs/{id}/complete` | — | `{replanned: [ids], revisions_added: [ids]}` — **one atomic transaction**; idempotent for an already-done job |
| `POST /api/jobs/{id}/move` | `{slot_start, slot_end}` | `{job, moved_jobs}` — sets `pinned=1`, reflows the rest; **409** if overlapping an external event, another job, after the goal deadline, or before now |
| `POST /api/jobs/{id}/skip` | — | job row (`status='skipped'`); replans the goal |

Move accepts ISO-8601 with or without milliseconds/`Z`. `slot_end` must be after
`slot_start` (**422**). A moved job is **pinned**: later replans keep its slot — and a
**conflicted** job moved explicitly by the user becomes `pending` (pinned). `skip` does
not pin.

## Inbox
| Method & path | Body | Returns |
|---|---|---|
| `POST /api/inbox/upload` | multipart `files` (+ optional `goal_id`) | `[{filename, status: 'ready'\|'duplicate'\|'error', material_id, topic_id, topic_name, summary, tags, effort_hours, duplicate_of?, status_reason?, cluster_relation, cluster_reason?}]` — uploads with `goal_id` take one atomic replan after ingest; a failing file never fails the batch — it is returned as an `error` row |
| `GET /api/inbox/files` | — | `[{id, filename, summary, tags, effort_hours, status, status_reason, duplicate_of, topic_id, topic_name, goal_id}]` |
| `POST /api/inbox/files/{id}/dismiss` | `{action: 'duplicate'\|'superseded'\|'new'}` | material row — user override; replans the goal |
| `POST /api/inbox/files/{id}/effort` | `{hours}` | material row — user override (bounds-validated); replans the goal |
| `POST /api/inbox/files/{id}/attach` | `{goal_id}` | material row — attach to a goal and replan; **404** if the goal is missing |
| `POST /api/inbox/attach_ready` | `{"goal_id"}` | `{attached, material_ids, goal_id}` — attaches every `ready` material with no goal and replans **once**; **404** if the goal is missing |

A hash-duplicate creates a distinct `duplicate` material row linked via
`duplicate_of` (the original keeps its files). Unsupported formats produce a row
with `status='error'` and `status_reason='unsupported format (.xyz)'`; the original
file is kept.

## Topics
| Method & path | Body | Returns |
|---|---|---|
| `GET /api/topics` | — | `[{id, name, goal_id, created_at, file_count, members: [{id, filename, status, effort_hours}]}]` |
| `POST /api/topics/{id}/rename` | `{name}` | topic row |
| `POST /api/topics/merge` | `{source_id, target_id}` | target topic row; members re-attach and affected goals replan |

## Chat
| Method & path | Body | Returns |
|---|---|---|
| `GET /api/chat/messages` | — | `[{id, role, content, created_at}]` (oldest first) |
| `POST /api/chat` | `{message}` | `{reply, llm: 'on'\|'off', actions?: [{tool, args, result}], llm_error?: {skill, message}}` — `'off'` = fallback path was used; `llm_error` is present only when an endpoint **is** configured but the call failed (the reply is then the offline answer) |

Chat receives calendar context (open goals, next 14 days of jobs/external events,
recent materials) so it can answer “what’s due this week?” even offline. The LLM
may invoke narrow tools: read-only (list_goals, list_jobs, list_events,
list_materials, get_stats) and two bounded writes (update_settings,
set_material_effort). Every tool result is returned to the client in the additive
`actions` array as `{tool, args, result}` receipts. Settings changes made by chat
are bounds-validated and stored with `updated_by='llm'` and visible in the
receipt; the engine still owns placement and re-plan is explicit (user presses
Re-plan). The LLM never moves jobs or touches external events.

## Settings
| Method & path | Body | Returns |
|---|---|---|
| `GET /api/settings` | — | all keys with decoded values + `updated_by`; credential keys (`llm_api_key`, `cf_api_token`) are **write-only**: value echoes `""` with `configured: true` |
| `PUT /api/settings` | `{"daily_capacity_hours": 5, ...}` | updated rows (credentials masked the same way) — validated as a whole before any write (422 on out-of-bounds or a malformed `llm_base_url`); open goals are re-planned after a change |

Keys: `daily_capacity_hours` (0.5–12), `slot_minutes` (15–120),
`min_slot_minutes` (15–120), `revision_intervals_days` (list of 1–6 ints, 1–60, normalised to ascending order),
`default_effort_hours` (0.1–20), `utc_offset_minutes` (−840…840, the user's local clock offset),
`llm_base_url` (empty → offline, else an `http(s)://` URL), `llm_model`, `llm_api_key` (write-only),
`ocr_provider` (`off|cloudflare`), `cf_account_id`, `cf_api_token`, `ocr_model`,
`ocr_min_chars`. Locally-hosted endpoints need no key (a client-side placeholder is
used); hosted endpoints authenticate with `llm_api_key`.

## LLM diagnostics
| Method & path | Body | Returns |
|---|---|---|
| `GET /api/llm/status` | — | `{configured, model, base_url, api_key_set, last_error: {skill, message, at} \| null}` — no secrets |
| `POST /api/llm/test` | — | `{ok: true, model, reply, latency_ms}` or `{ok: false, kind, skill, message, latency_ms}`; **always 200** (a failure is a diagnostic answer). `kind`: `not_configured \| auth \| not_found \| connection \| timeout \| http_error \| bad_request \| rate_limit \| bad_config \| unknown` |

`/api/llm/test` sends one tiny completion with `max_retries=0`, so a dead endpoint
answers in well under a second. The Settings panel uses both endpoints for its
"Test connection" button and its status line.

## Health
| Route | Serves |
|---|---|
| `GET /api/health` | `{"status": "ok"}` — used by the Docker healthcheck and `scripts/smoke_e2e.py` |

## Errors
`{"error": "<message>"}` with proper status codes: 404 unknown route / not found,
405 method not allowed, 409 conflict (e.g. job move overlaps external event),
422 validation, 500 unexpected.
