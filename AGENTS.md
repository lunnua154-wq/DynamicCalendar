# AGENTS.md — rules for agents working in this repo

## Read order
1. `BLUEPRINT.md` → 2. `docs/ARCHITECTURE.md` → 3. `docs/DATA_MODEL.md` → 4. `docs/API.md` → 5. `docs/STYLE.md`

## Project in one line
The calendar (ICS) is the source of truth; **deterministic Python** schedules study jobs; the LLM only tags / de-dups / chats.

## Hard rules (violating any = work rejected)
1. **The LLM never schedules.** `app/engine.py` is pure and deterministic, unit-testable with no network. No LLM calls inside the engine.
2. **External events are read-only.** Only re-import can change them. The engine may only *avoid* them, never move/delete them.
3. **Completed jobs are immutable.** A done job never moves and is never deleted.
4. **No new third-party dependency** without a new entry in `docs/DECISIONS.md` first.
5. **No frontend framework.** Vanilla JS + FullCalendar + Tailwind, built by Vite (the only build step in the repo). Three HTML pages, full stop (new views = tabs/panels inside an existing page, unless DECISIONS says otherwise).
6. **UTC everywhere in storage** (ISO-8601). Convert only at the API/render edge via `zoneinfo`.
7. **Offline mode is a feature.** With no LLM key configured, every LLM skill must fall back to a deterministic path (see skill table below). The app must be fully usable offline.
8. **A checkbox tick is one atomic server operation** — in a single SQLite transaction: mark done → schedule revision jobs → recompute remaining future work.
9. **No `datetime.now()` in engine code** — the clock is injected, so tests are deterministic.
10. **LLM output is always validated and stored as data** (see skill table). The engine reads data; it never calls the LLM.

## LLM skills (contracts — implemented in `app/llm.py`)

| Skill | Input → Output | Offline fallback |
|---|---|---|
| `tag` | material markdown → `{summary, tags[], effort_hours}` | tags = [filename stem], effort = default from settings |
| `cluster` | new material + existing topics → `{topic_id, relation: new\|duplicate\|version}` | de-dup by `content_hash` only; each file its own topic |
| `chat` | history + calendar context → reply | calendar-grounded reply (open goals, next 14 days of jobs/events, material summaries) |
| `tune_settings` | context → `{daily_capacity_hours?, slot_minutes?}` | no-op (settings keep current values) |

Bounds validation (enforced server-side, LLM or user):
- `daily_capacity_hours`: 0.5 – 12
- `slot_minutes`: 15 – 120
- `effort_hours`: 0.1 – 20 per material

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
