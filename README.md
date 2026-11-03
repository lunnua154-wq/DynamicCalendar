# DynaCalendar

A **dynamic, calendar-orientated study manager** with a **lightweight LLM assistant**.

- The calendar (ICS / RFC 5545) is the single source of truth
- Study jobs reflow in real time as progress changes (checkbox ticks, file drops, re-imports)
- The LLM only tags / de-dups / chats — it **never** does scheduling math
- Import-only sync for now (export is a future feature)

## Status

**Docs complete, code not started.** Next milestone: **M0** (3 pages + ICS import + calendar rendering, no LLM needed).

## Document map

| File | Purpose |
|---|---|
| `BLUEPRINT.md` | Concept, stack, milestones, confirmed decisions |
| `AGENTS.md` | Rules for anyone (human or AI agent) coding in this repo — **read first** |
| `CONTRIBUTING.md` | Setup, tests, commit conventions, review checklist |
| `docs/ARCHITECTURE.md` | Modules, data flows, engine algorithm, LLM skills |
| `docs/DATA_MODEL.md` | SQLite schema + ICS mapping rules |
| `docs/API.md` | REST endpoints (M0–M3) |
| `docs/STYLE.md` | Python + web code style |
| `docs/DECISIONS.md` | Decision log (append-only) |
| `docs/GLOSSARY.md` | Single vocabulary for code, docs, and chat |
| `docs/TESTING.md` | Test strategy + engine invariants E1–E9 |
| `docs/UX_SPEC.md` | Per-page UI spec (the contract for UI work) |
| `docs/WALKTHROUGH.md` | One concrete end-to-end scenario using `samples/` |

## Running (once M0 exists)

- Backend: `pip install -r requirements.txt && python -m app.main` → http://127.0.0.1:8000
- Frontend dev: `cd web && npm install && npm run dev` → http://127.0.0.1:5173 (proxies `/api` → 8000)
- Production: `cd web && npm run build` → FastAPI serves `web/dist/`
- Docker: `docker compose up --build` (Docker Desktop daemon must be running)
