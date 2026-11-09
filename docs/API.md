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
| `GET /api/events?from=&to=` | — | `[{id, kind, title, dtstart, dtend, venue, all_day, job?}]` — `job: {id, status, kind, material_id, revision_round, duration_min, pinned, placement_source, placement_note}` attached for study/revision kinds |
| `POST /api/ics/import` | multipart `file` (.ics) | `{imported, skipped, exams_found: [titles], exams: [{event_id, title, dtstart, dtend, venue}], annotated: n, replanned_goals: [ids]}` — `annotated` counts external events given a heuristic annotation (keyword/duration) in this request; app startup backfills pre-upgrade databases the same way; LLM annotation is on-demand only via `POST /api/events/annotate` |
| `POST /api/events/annotate` | `{force?: bool}` | `{annotated: n}` — annotate external events that lack one (or re-annotate all when `force: true`). LLM (chunked, ~45 s budget, heuristic for what it cannot cover) when an endpoint is configured, heuristic otherwise. Annotations sourced `user` are **never** overwritten by `force`; LLM/time failures degrade per event to the heuristic |
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
| `POST /api/goals/{id}/replan` | — | `{moved_jobs: [ids], revisions_added: [ids], moved_count, unplaced}` — `unplaced` counts jobs that could not fit anywhere before the deadline (capacity exhausted, not a preference failure) and would otherwise have vanished silently |

`deadline` may be omitted when `exam_event_id` is supplied: the server derives it
from that external event. If neither is present → **422**.

## Jobs
| Method & path | Body | Returns |
|---|---|---|
| `GET /api/jobs?from=&to=` | — | `[{id, kind, title, material_id, material_title, slot_start, slot_end, duration_min, status, revision_round, pinned, placement_source, placement_note}]` — `title` = the file name; `placement_source`/`placement_note` explain why this slot was chosen (`engine` = deterministic argmin-penalty pick, `llm` = LLM shortlist pick, `note` carries the reason) |
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
list_materials, get_stats, list_constraints) and bounded writes (update_settings,
set_material_effort, add_constraint, remove_constraint). Every tool result is
returned to the client in the additive `actions` array as `{tool, args, result}`
receipts. Settings changes made by chat are bounds-validated and stored with
`updated_by='llm'`; constraint writes take effect through an immediate replan of
open goals. The LLM never moves jobs directly or touches external events.

**Constraints from chat.** “Avoid math right after math class” is compiled to a
`subject_gap` constraint — with an LLM configured, via the `add_constraint` tool
(LLM emits structured, validated args); offline, via a deterministic NL parser in
the chat fallback (`app/constraints.parse_nl`). Both paths validate against
per-kind schemas and persist the rule with `source='llm'` / `source='user'`.
`POST /api/chat` in that case replies “Got it — saved the rule …” and the plan is
re-flowed immediately.

## Constraints
User/LLM-authored scheduling preference rules, stored as data and enforced deterministically by the planner (soft = penalty, hard = exclude candidate; hard relaxes to soft when no candidate survives — the job is never dropped).

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/constraints` | — | `[{id, kind, params, hard, enabled, source: 'user'\|'llm', label, created_at}]` |
| `POST /api/constraints` | `{kind, params, hard?, label?}` | `{constraint: {...}, replanned_goals: n}` — **201**; open goals are re-planned immediately |
| `DELETE /api/constraints/{id}` | — | `{deleted: id, replanned_goals: n}` — **404** if unknown |

Kinds and params (bounds-validated, **422** otherwise):
`post_fatigue_buffer` `{min_fatigue: 1–3, buffer_minutes: 15–240}` — no study within N min of a fatigue≥M event;
`subject_gap` `{subject: string ≤40 chars, gap_minutes: 15–240}` — no study of *subject* within N min after a same-subject external (“no math right after math class”);
`interleave` `{max_consecutive: 1–5}` — penalise more than N same-subject jobs in a row (soft-only);
`blocked_day_part` `{start_hour: 0–23, end_hour: 0–23}` — no (or penalised) study between the hours.
Add `hard: true` to any kind to exclude matching slots outright — except
`interleave`, which is soft-only (`hard: true` → **422**). `label` is optional
(auto-generated from kind+params). `blocked_day_part` blocks on interval
overlap (a 50-minute slot starting 11:30 overlaps a 12:00–13:00 block), not
just the slot's start hour.

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
`ocr_min_chars`, `smart_placement` (`on|off` — deterministic penalty scoring in the
planner; default `on`, `off` restores earliest-free-slot), `llm_placement_mode`
(`off|rank` — `rank` lets the configured LLM pick from a ≤4 shortlist of
hard-valid slots per job, deterministic fallback on any failure; default `off`).
Locally-hosted endpoints need no key (a client-side placeholder is used);
hosted endpoints authenticate with `llm_api_key`.

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
