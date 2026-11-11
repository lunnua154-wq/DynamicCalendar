# AGENTS.md — rules for agents working in this repo

## Read order
1. `BLUEPRINT.md` → 2. `docs/ARCHITECTURE.md` → 3. `docs/DATA_MODEL.md` → 4. `docs/API.md` → 5. `docs/STYLE.md`

## Project in one line
The calendar (ICS) is the source of truth; **deterministic Python** schedules study jobs; the LLM enriches placement with validated semantic judgement (fatigue, subjects, natural-language constraints) — it proposes, the engine disposes.

## Hard rules (violating any = work rejected)
1. **The LLM never owns scheduling; it may only choose between engine-verified options.** `app/engine.py` contains no network and no LLM imports. Default path: candidates are hard-validated (overlap/capacity/deadline/done-pinned), scored, and placed at `argmin(penalty, start)` — with `smart_placement=off` this degenerates to the legacy earliest-free-slot behaviour. When `llm_placement_mode='rank'` and an endpoint is configured, the caller injects a `chooser(job_context, candidates)` (see `app/planner.py`) that picks from a shortlist of ≤4 hard-valid candidates (2 earliest + 2 lowest-penalty); out-of-range answers, invalid output and any exception fall back to the deterministic pick. LLM output can never produce an overlapping, over-capacity, past-deadline, or done/pinned-violating slot. `llm_placement_mode='rank'` is opt-in (default `off`); default installs make zero LLM calls inside `replan()`.
2. **External events are read-only.** Only re-import can change them. The engine may only *avoid* them, never move/delete them.
3. **Completed jobs are immutable.** A done job never moves and is never deleted.
4. **No new third-party dependency** without a new entry in `docs/DECISIONS.md` first.
5. **No frontend framework.** Vanilla JS + FullCalendar + Tailwind, built by Vite (the only build step in the repo). Three HTML pages, full stop (new views = tabs/panels inside an existing page, unless DECISIONS says otherwise).
6. **UTC everywhere in storage** (ISO-8601). Convert only at the API/render edge via `zoneinfo`.
7. **Offline mode is a feature.** With no LLM key configured, every LLM skill must fall back to a deterministic path (see skill table below). The app must be fully usable offline.
8. **A checkbox tick is one atomic server operation** — in a single SQLite transaction: mark done → schedule revision jobs → recompute remaining future work.
9. **No `datetime.now()` in engine code** — the clock is injected, so tests are deterministic.
10. **LLM output is always validated and stored as data** (see skill table). The engine reads data; it never calls the LLM (an injected `chooser` is a dependency, like the injected clock).
11. **Hard constraints relax, work is never dropped.** User/LLM constraints (`post_fatigue_buffer`, `subject_gap`, `blocked_day_part`) may be marked `hard`; if no candidate survives them, they degrade to soft penalties for that placement and `placement_note` records the relaxation. When *no* candidate exists at all (capacity/deadline exhausted — a real dead end), the engine reports `unplaced` on the replan receipt instead of silently vanishing the job.
12. **Placement receipts.** Every created job records `placement_source` (`engine`|`llm`) and `placement_note` (why this slot). Surfacing receipts is a feature — do not strip them for cosmetics.

## LLM skills (contracts — implemented in `app/llm.py`)

| Skill | Input → Output | Offline fallback |
|---|---|---|
| `tag` | material markdown → `{summary, tags[], effort_hours}` | tags = [filename stem], effort = default from settings |
| `cluster` | new material + existing topics → `{topic_id, relation: new\|duplicate\|version}` | de-dup by `content_hash` only; each file its own topic |
| `chat` | history + calendar context → `{reply, llm, actions?}` | calendar-grounded reply; bounded tool loop (reads + update_settings + set_material_effort + list/add/remove_constraint) when an LLM is configured; offline = deterministic fallback, no tools (constraint NL is still parsed deterministically) |
| `tune_settings` | context → `{daily_capacity_hours?, slot_minutes?}` | no-op (settings keep current values) |
| `annotate_events` | external events → `[{event_id, subject, fatigue 0–3}]` | duration/keyword heuristics (app/annotate.py) |
| `choose_placement` | job context + hard-valid candidate shortlist → `{choice, reason}` | `argmin(penalty, start)` deterministic pick |

Constraint kinds (stored in `constraints`, enforced by `app/constraints.py` + the engine):
- `post_fatigue_buffer` — no study within N min of a fatigue≥M external (`min_fatigue` 1–3, `buffer_minutes` 15–240, `hard`)
- `subject_gap` — no study of `subject` within N min after an external of the same subject (`gap_minutes` 15–240, `hard`)
- `interleave` — penalise more than `max_consecutive` (1–5) same-subject jobs in a row (soft only)
- `blocked_day_part` — no (or penalised) study between `start_hour` and `end_hour`, measured as interval overlap (a 50-minute slot starting 11:30 overlaps 12:00–13:00) (`hard`)

Bounds validation (enforced server-side, LLM or user):
- `daily_capacity_hours`: 0.5 – 12
- `slot_minutes`: 15 – 120
- `effort_hours`: 0.1 – 20 per material
- constraint params: per-kind schemas above (`app/constraints.validate_params`)
- annotation `fatigue`: integer 0 – 3

## Conventions
- Python: PEP 8, line length 100, type hints on public signatures, stdlib `sqlite3` (no ORM), parameterized queries only.
- JS: ES2022, camelCase, shared `web/common.js`, user data rendered via `textContent` only.
- CSS: Tailwind utility classes; project tokens (colors `external`/`study`/`revision`) defined in the Tailwind theme file — no component framework.
- Commit messages: `<area>: <imperative>` — e.g. `engine: cap revision pull-forward at 14d`.

## Run & test
- Backend dev: `python -m app.main` (port 8000)
- Frontend dev: `cd web && npm run dev` (Vite port 5173, proxies `/api` → 8000)
- Tests: `pytest -q`
- Docker: `docker compose up --build` (note: Docker Desktop daemon must be running)

## Definition of done
- Engine change → deterministic unit test added (injected clock)
- API change → `docs/API.md` updated in the same commit
- New dependency or new page or new LLM skill → `docs/DECISIONS.md` entry
