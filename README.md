# DynaCalendar

A **dynamic, calendar-orientated study manager** with a **lightweight LLM assistant**.

- The calendar (ICS / RFC 5545) is the single source of truth
- Study jobs reflow in real time as progress changes (checkbox ticks, file drops, re-imports)
- The LLM only tags / de-dups / chats — it **never** does scheduling math
- Import-only sync for now (export is a future feature)

## Status

**M0–M4 are implemented; M5 (final delivery) is complete.** The full loop works
end-to-end: import an `.ics`, create a goal from an exam, drop materials (tagged
and clustered, optionally via an LLM), and jobs are scheduled immediately.
Ticking a job adds spaced revisions and reflows the plan atomically. Dragging a
job pins it, and external calendar events override conflicting pinned slots.
Chat is grounded in the calendar and falls back to useful offline answers. Stats
and optional Cloudflare OCR are available, and the app ships as a multi-stage
Docker image.

- **M2** — settings panel for any OpenAI-compatible endpoint; auto-tag, de-dup,
  and topic clustering (`new`/`duplicate`/`version`); grounded chat.
- **M3** — deterministic planner, exam-derived goals, upload → ingest → replan,
  move/pin with conflict vetoes, revision de-duplication.
- **M4** — stats panel, optional Cloudflare OCR for scanned PDFs, Docker compose,
  responsive week view.
- **M5** — API contract (`docs/API.md`), delivery checklist/demo
  (`docs/DELIVERY.md`), and one-command acceptance smoke test
  (`python scripts/smoke_e2e.py`).

The LLM never schedules; deterministic Python does. With no endpoint configured,
every LLM skill falls back to a deterministic path.

## Document map

| File | Purpose |
|---|---|
| `BLUEPRINT.md` | Concept, stack, milestones, confirmed decisions |
| `AGENTS.md` | Rules for anyone (human or AI agent) coding in this repo — **read first** |
| `CONTRIBUTING.md` | Setup, tests, commit conventions, review checklist |
| `docs/ARCHITECTURE.md` | Modules, data flows, engine algorithm, LLM skills |
| `docs/DATA_MODEL.md` | SQLite schema + ICS mapping rules |
| `docs/API.md` | REST endpoints (M0–M5) |
| `docs/STYLE.md` | Python + web code style |
| `docs/DECISIONS.md` | Decision log (append-only) |
| `docs/GLOSSARY.md` | Single vocabulary for code, docs, and chat |
| `docs/TESTING.md` | Test strategy + engine invariants E1–E13 |
| `docs/UX_SPEC.md` | Per-page UI spec (the contract for UI work) |
| `docs/WALKTHROUGH.md` | One concrete end-to-end scenario using `samples/` |
| `docs/DELIVERY.md` | M5 final-delivery acceptance checklist + demo script |

## Running

- Backend: `pip install -r requirements.txt && python -m app.main` → http://127.0.0.1:8000
- Frontend dev: `cd web && npm install && npm run dev` → http://127.0.0.1:5173 (proxies `/api` → 8000)
- Production: `cd web && npm run build` → FastAPI serves `web/dist/`
- Docker: `docker compose up --build` (Docker Desktop daemon must be running)
- Tests: `pytest -q`

## Offline-first LLM

With no LLM endpoint configured, every LLM skill (tag, cluster, chat, tune_settings) falls back to a deterministic path, so chat and file tagging work without any API key. Configure `llm_base_url` / `llm_model` (and optional `llm_api_key`) in Settings to point at any OpenAI-compatible endpoint (OpenAI, Cloudflare Workers AI, Ollama…).
