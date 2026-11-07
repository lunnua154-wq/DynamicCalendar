# Testing

Strategy: the engine is the heart, so it gets the deepest tests. Everything else is
thick enough to catch regressions. Fixtures: `samples/` (see its README — UIDs are stable).

## Environment
- `pytest`; no external services. SQLite in a temp file per test.
- Engine tests **always** inject `now` and `settings` — never read the clock or real settings.
- LLM is always mocked (`unittest.mock`); a second set of tests runs it **unconfigured**
  to prove offline-mode fallbacks.

## Engine (deepest coverage) — `tests/test_engine.py`
| # | Invariant | Fixture setup |
|---|---|---|
| E1 | no job ever overlaps an external event | sample_calendar.ics + goal + 8 h effort (forces slot hunting) |
| E2 | done jobs never move | complete one job, re-plan 5 times, assert slot unchanged |
| E3 | idempotent | replan twice from same state → identical job slots |
| E4 | today's pending jobs keep their slots (no churn) | place jobs, complete an unrelated one, assert today untouched |
| E5 | capacity cap respected | 20 h effort, 4 h/day capacity → ≤ 4 slots/day |
| E6 | revision placement around external events | complete a job the day before the lab (fixture) → R lands after the lab |
| E7 | revisions never silently dropped | block all candidate slots → job marked `conflicted`, surfaced (not vanished) |
| E8 | deadline respected | effort > free time until deadline → remaining jobs land ON deadline day, none after |
| E9 | pull-forward | small effort → next material scheduled immediately, not parked at deadline |
| E10 | revisions are not duplicated | complete a learn job, replan 5× → exactly one job per round |
| E11 | a done revision is never respawned | complete a revision, replan → no new job for that round |
| E12 | pinned jobs survive replan | move a future job, replan → exact same slot |
| E13 | revisions obey capacity/deadline | many learn completions on 1 h/day; no day over capacity, no revision after the deadline |

## ICS — `tests/test_ics_sync.py`
- import `samples/sample_calendar.ics` → 10 rows, correct UTC conversion (UTC+8 fixture)
- `UID` dedup: re-import same file → 0 new, updated-in-place on changed rows
- `EXAM` category (any case) → exam candidate surfaced
- all-day event (`books-due`) → `all_day=1`
- invalid file (garbage text) → clean error, no partial import (transaction rollback)

## Ingest — `tests/test_ingest.py`
- all 4 sample markdown files convert and hash (hashes stable across runs)
- hash de-dup: drop the same file twice → second is `duplicate`, no new topic
- unsupported format → row `error` with reason, original file kept on disk

## API — `tests/test_api.py` (FastAPI TestClient)
- every endpoint in `docs/API.md`: happy path + documented error codes
- JSON body **and** form-encoded body accepted for goals/move/dismiss/effort
- `POST /api/jobs/{id}/move` onto an external slot or another job → **409**; moving
  also pins the job (replan keeps the slot); milliseconds in ISO timestamps are accepted
- `POST /api/jobs/{id}/complete` → single transaction observable: done + revisions +
  recompute in one response; ticking an already-done job is a no-op
- `POST /api/goals` with only `exam_event_id` derives the deadline from the exam event
- upload with `goal_id` schedules jobs immediately (no manual replan)
- duplicate upload creates a linked `duplicate` row, no new topic
- topics: list with members, rename, merge (members move, no orphan topic)
- `PUT /api/settings` out of bounds → **422**, value unchanged
- upload without `files` part → **422**
- chat returns calendar-grounded offline text and persists user/assistant messages

## OCR — `tests/test_ocr.py`
- disabled provider → `None` (no network)
- enabled + mocked Workers AI response → text; error/timeout → `None`
- PDF image extraction is best-effort and never raises

## Delivery — `scripts/smoke_e2e.py`
- one runnable acceptance script: fresh DB → import ICS → goal from exam → upload
  samples → jobs appear → tick → revisions → move/pin → replan → chat → stats.
  Exit code 0 = M5 offline demo passes.

## LLM — `tests/test_llm.py`
- each skill: mocked-LLM happy path; mocked-LLM garbage output → bounds validation rejects → fallback used
- unconfigured client → every skill returns fallback shape (no exceptions, no network)

## Web (manual, M0–M3)
Browser checklist (documented here, executed per milestone):
- [ ] P1: import sample .ics → chips render; day panel shows venue; exam banner appears
- [ ] P1: tick a job in day panel → card fades, revisions appear on P2
- [ ] P2: drag job onto external event → snaps back + toast; drag to free slot → reflow animates
- [ ] P2: failed tick (server 500 simulated) → checkbox reverts
- [ ] P3: drop all 4 samples at once → statuses settle, result lines correct (LLM + offline)
- [ ] P3: dismiss a "new topic" row → it becomes `duplicate`, re-plan triggered
- [ ] nav: any optimistic failure reverts (no half-state on any page)

## Automation of web tests
Playwright is **not** in the dependency list yet. If/when adopted → DECISIONS.md entry first.

## Gate
`pytest -q` green is the merge gate. A PR that breaks an invariant test is rejected,
regardless of how good the feature is.
