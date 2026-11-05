# Testing

Strategy: the engine is the heart, so it gets the deepest tests. Everything else is
thick enough to catch regressions. Fixtures: `samples/` (see its README — UIDs are stable).

## Environment
- `pytest` in the repo virtualenv; no external services. SQLite in a temp file per test
  (`tests/conftest.py` keeps them under `data/_test/`, which is gitignored).
- Engine tests **always** inject `now` and pin `utc_offset_minutes = 0` — never read the
  clock or the host timezone.
- LLM is always mocked or unconfigured; a set of tests runs unconfigured to prove the
  offline fallbacks. `tests/test_ingest.py` uses a deterministic `StubClient`.

## Engine — `tests/test_engine.py` (deepest coverage)
| # | Invariant |
|---|---|
| E1 | no job ever overlaps an external event (8 h effort forces slot hunting) |
| E2 | done jobs never move (complete one, re-plan 5×) |
| E3 | idempotent: same state → identical slots |
| E4 | today's pending jobs keep their slots (no churn) |
| E5 | capacity cap respected (20 h effort, 4 h/day → ≤ 4 slots/day) |
| E6 | revision placement around external events (R1 lands after the lab) |
| E7 | revisions never silently dropped → `conflicted` |
| E8 | deadline respected — nothing lands after it |
| E9 | pull-forward: small effort → the next material is scheduled immediately |
| — | future work survives a replan (regression for the delete-without-recreate bug) |
| — | a replan does not churn job ids, and repeated replans keep revision jobs |
| — | a pinned (dragged) job keeps its slot across replans |
| — | a +8 h offset moves the slot window to the local 08:00 boundary |

## ICS — `tests/test_ics_sync.py`
- `samples/sample_calendar.ics` → 10 rows, correct UTC conversion (UTC+8 fixture)
- `UID` dedup: re-import same file → 0 new, update-in-place on changed rows
- `EXAM` category → `exams_found` + `exam_events` details (venue, id)
- `CATEGORIES` and `RRULE` are stored
- all-day event (`books-due`) → `all_day=1`; missing `DTEND` → 60 min default
- garbage input → `IcsImportError`, no partial import

## Ingest — `tests/test_ingest.py` (M1)
- sample markdown files convert, hash, get tagged and clustered
- hash de-dup: same bytes twice → `duplicate`, no second topic
- unsupported format → `error` with `unsupported format (.xyz)`, original file kept
- version relation → previous material becomes `superseded` with `superseded_by`
- offline clustering → one topic per file
- processing twice is a no-op; attaching to a goal schedules learn jobs
- topic merge re-attaches members

## API — `tests/test_api.py` (FastAPI TestClient)
- every endpoint in `docs/API.md`: happy path + documented error codes
- `POST /api/jobs/{id}/move` onto an external slot → **409**; a valid move sticks across a replan
- `POST /api/jobs/{id}/complete` → one transaction (done + revisions + recompute); second tick → **409**
- `PUT /api/settings` out of bounds → **422** (and unknown keys → 422)
- upload without `files[]` → **422**
- full M1 flow: upload → `processing` → poll → `ready` → topic → attach → jobs
- pages are served (`/`, `/week`, `/inbox`)

## Web (manual, M0–M3)
Browser checklist (documented here, executed per milestone). M0/M1 were verified in headless
Edge driven over the DevTools protocol (scripts kept outside the repo — Playwright is still
not a dependency), against the seeded app on `http://127.0.0.1:8000`:

- [x] P1: import sample .ics → chips render; day panel opens and lists the day with venues
- [x] P1: tick a job in the day panel → the card fades, the server records `done`, 4 revision jobs appear
- [x] P2: external events are not draggable; a move onto one is rejected with 409 (pytest) and vetoed in the UI
- [ ] P2: full pointer drag of a job card to a free slot (needs a real drag; not yet scripted)
- [ ] P2: failed tick (server 500 simulated) → checkbox reverts
- [x] P3: drop all 4 samples at once → statuses settle to `ready`, result lines correct (offline)
- [x] P3: duplicate re-upload → `duplicate` row, no new topic
- [x] P3: Open shows the converted markdown; chat answers offline
- [x] Settings: panel opens with current values, saving persists and recomputes the plan
- [x] nav: the three tabs are present and the active one is marked

## Automation of web tests
Playwright is **not** in the dependency list yet. If/when adopted → DECISIONS.md entry first.

## Gate
`pytest -q` green is the merge gate. A PR that breaks an invariant test is rejected,
regardless of how good the feature is.
