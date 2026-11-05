# Code style

## Python
- PEP 8; 4 spaces; line length 100
- Type hints on all public signatures (py3.12 builtin generics: `list[str]`, `dict[str, Any]`)
- **No ORM.** Stdlib `sqlite3` via `app/db.py`; parameterized queries only — never f-string SQL
- One responsibility per module (layout in `BLUEPRINT.md` §8); keep functions under ~40 lines
- Time: store UTC ISO-8601 strings; convert with `zoneinfo` only at the API edge
- Errors: raise domain exceptions from `app/errors.py`; FastAPI handlers map them to `{"error": ...}`
- **No network in `engine.py`.** All LLM I/O lives in `llm.py`. All file I/O lives in `ingest.py`.
- Tests: `pytest`; engine tests must be deterministic (injected `now`, temp sqlite file); LLM mocked
- No new dependency without a `docs/DECISIONS.md` entry (see `AGENTS.md` rule 4)

## Web (Vite + Tailwind + FullCalendar, no framework)
- ES2022; no framework; Vite is the **only** build step (`npm run dev` / `npm run build` → `web/dist/`)
- Shared: `web/common.js` (`api()` fetch wrapper + small render helpers); one script per page
- **User data rendered via `textContent` only** — never `innerHTML` with user strings
- Tailwind v4 for all styling; theme tokens in `web/tailwind.css` (colors: `external` / `study` / `revision` + status tints); light theme only (M0)
- FullCalendar v6.1 vanilla packages (MIT), mounted from `web/src/<page>.js`:
  - month = `dayGridMonth`, week = `timeGridWeek`
  - event cards: `eventContent` returns DOM built with `document.createElement` + Tailwind classes
    (checkbox cards for study/revision jobs); the FullCalendar skin lives in `web/tailwind.css`
  - drag-reschedule: `eventDrop` → `POST /api/jobs/{id}/move`; veto via `eventAllow` (never onto an
    external event); external events also carry `startEditable: false`
  - all event rendering through the event API — never hand-rolled table cells
- Naming: JS camelCase, HTML `data-*` for ids (`data-job-id`)
- Accessibility: real `<button>` for actions, labelled checkboxes, visible `:focus` styles
- Three HTML pages, full stop. New views = tabs/panels inside an existing page

## Commits
`<area>: <imperative>` — areas: `engine`, `ics`, `ingest`, `llm`, `api`, `web`, `docs`, `docker`
e.g. `engine: cap revision pull-forward at 14d`, `ics: handle VEVENTs without DTEND`
