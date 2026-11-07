# M5 — Final delivery & acceptance

M5 is the **final delivery stage** (defined 2026-11-07) and is not a new feature area.
It is the gate that proves M0–M4 work together: the dynamic loop is closed end-to-end,
the frontend builds for production, the Docker path works, and the docs describe what
actually ships.

## Definition of done

A delivery is accepted only when **all** of these are true:

1. **Tests** — `pytest -q` is green, including the engine invariants E1–E9 and the
   M3/M4 regression tests (move/pin, revision de-duplication, upload → replan, JSON bodies).
2. **Production build** — `cd web && npm ci && npm run build` succeeds and
   `python -m app.main` serves the built pages from `web/dist/` at `/`, `/week`, `/inbox`.
3. **Offline demo** — the full walkthrough below works with **no LLM endpoint configured**
   (deterministic fallbacks, `(LLM off)` badge).
4. **LLM smoke test** — with any OpenAI-compatible endpoint configured (e.g. a local
   llama.cpp/Ollama server), `tag`, `cluster` and `chat` answer without errors; a broken
   endpoint still falls back to offline behaviour (never a 500).
5. **Docker** — `docker compose up --build` serves the same app on `http://localhost:8000`,
   with the database persisted in the `./data` volume.
6. **Docs** — `docs/API.md`, `docs/ARCHITECTURE.md`, `docs/DATA_MODEL.md`,
   `docs/UX_SPEC.md`, `docs/TESTING.md` and `README.md` match the shipped behaviour.
7. **No secrets** — no API key, token or local endpoint is committed; runtime data stays
   in `data/` (gitignored).

## Acceptance demo (offline, ~3 minutes)

Prerequisites: `pip install -r requirements.txt`, a fresh database
(`rm -rf data/` or `DYNACALENDAR_DB=/tmp/demo.db`), server on `http://127.0.0.1:8000`.

1. **Import** — P1 → *Import .ics* → `samples/sample_calendar.ics`.
   Expect `Imported 10 events` and the amber **Exam detected: MATH2001 Final Exam** banner.
2. **Goal** — click *Create goal* on the banner.
   Expect a goal whose deadline is the exam date (18 Dec), not today.
3. **Drop files** — P3 → select the goal → drop all four `samples/materials/*.md`.
   Expect four `ready` rows. Offline: four separate topics, 1.0 h each.
   With an LLM: the `_v2` file is a **version update of** the first, the others are new topics.
4. **Allocation** — P2 → the goal's four hours are placed in free slots around the
   imported classes; no card overlaps an external event.
5. **Atomic tick** — tick the first job on P2.
   Expect: the card fades to *done*, four revision cards appear (+1/+3/+7/+14 d), and the
   week progress bar increases. The done job never moves on later replans.
6. **Drag** — drag a future job to another free slot.
   Expect: it stays there after clicking *Re-plan* (pinned), and the remaining jobs reflow.
   Dropping onto an external event snaps back with a toast (server returns 409).
7. **Upload while planning** — drop another material.
   Expect: it appears in the inbox **and** the goal is replanned immediately (no manual
   *Re-plan* needed).
8. **Chat** — P3 → ask *“what's due this week?”*.
   Expect a concrete answer from the calendar data even with `(LLM off)`.
9. **Stats** — the progress/stats surface reports done vs planned hours and goal progress.
10. **Re-import** — re-import the `.ics`; expect `0 imported, 10 skipped` (idempotent).

## LLM smoke test

Configure *Settings* with any OpenAI-compatible endpoint, for example a local
llama.cpp server:

```
base URL: http://127.0.0.1:8080/v1
model:    gemma-3-1b-it-Q4_K_M.gguf   (or the model id reported by /v1/models)
API key:  (empty for local)
```

The badge switches to `(LLM on)`. Verify tag/cluster on a drop and one chat reply.
Then point the base URL at a dead port and verify the app still works offline
(fallback, no 500).

> The local endpoint is a **test fixture**, not a committed default. Settings live in
> SQLite; do not commit `data/` or `.env`.

## Docker

```bash
docker compose up --build          # app on :8000, data in ./data
docker compose --profile llm up    # optional local ollama service on :11434
```

The app image uses a Node build stage for `web/dist` and a `python:3.12-slim` runtime;
it runs as a non-root user and stores SQLite under `/data`.

## Evidence to capture

- `pytest -q` output (count + green).
- `npm run build` output (dist files).
- Offline demo screenshots/notes for steps 1–10.
- One LLM-on chat reply and one fallback reply.
- `docker compose up` log line showing the healthcheck passing.

## Known limitations (explicitly out of scope for M5)

- ICS **export** remains a future feature (import-only, per DECISIONS).
- Scanned-PDF OCR is best-effort/optional (Cloudflare Workers AI, off by default);
  text-based PDFs and office documents are the supported path.
- Single-user local app: no auth, multi-user sync, or hosted deployment.
