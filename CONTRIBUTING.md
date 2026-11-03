# Contributing

For humans of any experience level **and** for AI coding tools. The docs are the source of
truth — when in doubt, read more docs, don't guess.

## Read order
1. `BLUEPRINT.md` — what we're building and why
2. `AGENTS.md` — the hard rules (this is the contract)
3. `docs/WALKTHROUGH.md` — one concrete scenario with real numbers (builds intuition)
4. `docs/GLOSSARY.md` — vocabulary
5. The doc for the area you're touching (`docs/API.md`, `docs/UX_SPEC.md`, `docs/DATA_MODEL.md`, …)

## Prerequisites
- Python **3.12+** (3.14 works)
- Node **20+** (24 works) + npm
- Docker (optional — dev works without it)

## Setup (dev)
```bash
# backend
python -m venv .venv
.venv\Scripts\activate        # Windows   (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
python -m app.main            # → http://127.0.0.1:8000

# frontend (second terminal)
cd web
npm install
npm run dev                   # → http://127.0.0.1:5173 (proxies /api to :8000)
```

## Test
```bash
pytest -q
```
The full strategy + per-area test matrix: `docs/TESTING.md`. Green `pytest -q` is the merge gate.

## Try the sample data
`samples/` holds the shared fixtures (a realistic `.ics` + 4 study materials).
The demo seed script (added in M0) loads them into a fresh database:
```bash
python scripts/seed.py        # fresh data/ + samples loaded
```

## Commits
- `<area>: <imperative>` — areas: `engine`, `ics`, `ingest`, `llm`, `api`, `web`, `docs`, `docker`
- Small commits. One concern per commit.
- A PR touching an area updates that area's doc **in the same commit** (see AGENTS.md "Definition of done").

## Review — what reviewers check first
1. AGENTS.md hard rules (esp. #1: no LLM in the engine; #2: external events read-only)
2. Invariant tests still green (`docs/TESTING.md` E1–E9)
3. Docs updated with the change
4. No new dependency without a `docs/DECISIONS.md` entry

## For AI coding tools
- **Read `AGENTS.md` before writing any code.** It is written for you.
- The doc set (`docs/*`) is the specification. Implement against it; if you believe the doc
  is wrong, propose the doc change first — don't silently diverge.
- Never add a dependency, a page, or a LLM skill without a `docs/DECISIONS.md` entry.
- Run `pytest -q` before you consider your work done.

## Where to look (cheat sheet)
| I want to… | Go to |
|---|---|
| change a screen's behavior/layout | `docs/UX_SPEC.md` → `web/<page>.html` + `web/common.js` |
| add/change an API endpoint | `docs/API.md` → `app/main.py` → `tests/test_api.py` |
| change scheduling behavior | `docs/WALKTHROUGH.md` (invariants) → `app/engine.py` → `tests/test_engine.py` |
| change the database | `docs/DATA_MODEL.md` → `app/db.py` |
| change ICS handling | `docs/DATA_MODEL.md` §ICS → `app/ics_sync.py` → `tests/test_ics_sync.py` |
| change LLM behavior | `AGENTS.md` §LLM skills → `app/llm.py` → `tests/test_llm.py` |
| add a dependency | `docs/DECISIONS.md` **first**, then `requirements.txt` |
