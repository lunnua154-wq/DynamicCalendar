# DynaCalendar

A **dynamic, calendar-orientated study manager** with a **lightweight LLM assistant**.

- The calendar (ICS / RFC 5545) is the single source of truth
- Study jobs reflow in real time as progress changes (checkbox ticks, file drops, re-imports)
- The LLM only tags / de-dups / chats — it **never** does scheduling math
- Import-only sync for now (export is a future feature)

## Status

| Milestone | Deliverable | State |
|---|---|---|
| **M0** | 3 pages, ICS import, month + week rendering, event store, checkbox state | **done** |
| **M1** | Inbox pipeline: upload → markitdown → material store → status list → topics | **done** |
| M2 | LLM assistant: chat + auto-tag / de-dup / cluster, endpoint config | client + skill contracts are wired; offline fallbacks are the default until a key is configured |
| M3 | Dynamic engine: goals, allocation, spaced revisions, drag-reschedule | engine, revisions and drag-reschedule shipped alongside M0/M1 |
| M4 | Progress stats, Cloudflare OCR, Docker hardening, more tests | next |

What works end to end today:

- Import an `.ics` → external events stored read-only; an `EXAM`-tagged event offers a goal
- **P1 Calendar**: month grid, day panel with checkboxes, exam banner, re-plan
- **P2 Week**: time grid, per-job checkboxes, drag-reschedule with external-event veto,
  week progress bar, job popover (open material / skip / re-plan goal)
- **P3 Inbox**: drop files → `processing → ready | duplicate | superseded | error`,
  topics strip with rename/merge, per-file effort overrides and goal attachment,
  offline chat that reads the calendar
- Deterministic engine: capacity/slot settings, spaced revisions (+1/3/7/14 d),
  deadline-aware placement, idempotent recompute, pinned manual moves,
  `conflicted` rounds surfaced instead of dropped
- Settings panel: capacity, slot length, revision intervals, UTC offset, LLM endpoint

## Quickstart (host Python)

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows   (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt

python scripts/seed.py            # optional: samples/ calendar + materials into data/
python -m app.main                # → http://127.0.0.1:8000
```

`web/dist/` is a build artifact (gitignored). Build it once before opening the UI;
until then `http://127.0.0.1:8000` serves a page with these instructions.

```bash
cd web
npm install
npm run build                     # → web/dist/, served by FastAPI
npm run dev                       # or: dev server on :5173, proxies /api → :8000
```

## Docker

```bash
docker compose up --build            # → http://127.0.0.1:8000
docker compose --profile llm up -d   # optional local Ollama on :11434
```

## Tests

```bash
pytest -q                         # 63 tests, no network, no external services
```

The engine invariants (E1–E9) and the M1 ingest pipeline are the deepest suites —
see `docs/TESTING.md`.

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

## Known limitations (M0/M1)

- `RRULE` recurrence is imported at its `DTSTART` instance only; expansion is post-M4.
- Export to `.ics` is a future feature (import only, per `docs/DECISIONS.md`).
- Scanned PDFs have no text layer (Cloudflare OCR is M4); text-based PDFs convert fine.
- The UI is English and desktop-first; below 768 px the layout stacks, but the week grid
  still scrolls horizontally (polish is M4).
- One local user, no auth, no sync server — by design.
