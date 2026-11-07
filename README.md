# DynaCalendar

A **dynamic, calendar-orientated study manager** with a **lightweight LLM assistant**.

- The calendar (ICS / RFC 5545) is the single source of truth
- Study jobs reflow in real time as progress changes (checkbox ticks, file drops, re-imports)
- The LLM only tags / de-dups / chats — it **never** does scheduling math
- Import-only sync for now (export is a future feature)

## Status

**M0 (calendar skeleton + ICS import) and M1 (inbox upload → markdown → tagging) are done.**  
Current focus: **M2 — LLM assistant** (chat + auto-tag / de-dup / cluster of dropped files + LLM endpoint config), per `BLUEPRINT.md §9`. The LLM never schedules; deterministic Python does.

M2 in place: settings panel to configure the LLM endpoint (`llm_base_url` / `llm_model` / `llm_api_key`), and file upload now clusters drops into existing topics (`new`/`duplicate`/`version`) instead of one topic per file. With no endpoint configured, chat and tagging fall back to deterministic offline behavior.

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

## Running

- Backend: `pip install -r requirements.txt && python -m app.main` → http://127.0.0.1:8000
- Frontend dev: `cd web && npm install && npm run dev` → http://127.0.0.1:5173 (proxies `/api` → 8000)
- Production: `cd web && npm run build` → FastAPI serves `web/dist/`
- Docker: `docker compose up --build` (Docker Desktop daemon must be running)
- Tests: `pytest -q`

## Offline-first LLM

With no LLM endpoint configured, every LLM skill (tag, cluster, chat, tune_settings) falls back to a deterministic path, so chat and file tagging work without any API key. Configure `llm_base_url` / `llm_model` (and optional `llm_api_key`) in Settings to point at any OpenAI-compatible endpoint (OpenAI, Cloudflare Workers AI, Ollama…).
