# Architecture

## Layers

```
web/  (calendar.html · week.html · inbox.html — Vite + Tailwind + FullCalendar)
   ↓  HTTP / JSON
app/main.py        FastAPI routes — thin, validation only
   ├─ ics_sync.py   import .ics → external events (read-only)
   ├─ ingest.py     file → markdown (markitdown) → tag/cluster glue
   ├─ engine.py     DETERMINISTIC scheduler — the heart (no network, no LLM)
   ├─ llm.py        pluggable OpenAI-compatible client + offline fallbacks
   └─ db.py         sqlite3 data layer (stdlib, no ORM)
```

## Data flow — file drop (P3 inbox)

```
upload (multipart)
  → save original + compute content_hash (sha256)
  → hash de-dup first (deterministic, always)
  → markitdown → data/materials/<id>.md
  → optional OCR (Cloudflare, off by default) if the text layer is empty
  → llm.tag()      (offline fallback: filename stem + default effort)
  → llm.cluster()  (offline fallback: hash de-dup only; new topic per file)
  → attach to the selected goal (if any)
  → engine.replan(goal)   # future slots only, never touches done work or pinned jobs
  → inbox status line updates: received → converting → tagged → done
```

## Data flow — ICS re-import (P1)

```
POST /api/ics/import (multipart file)
  → merge events by ICS UID (same transaction, all-or-nothing)
  → engine.replan() for every open goal
    (pinned jobs that now overlap an imported external are deleted
     and re-placed unpinned; pinned jobs that don't overlap stay put)
  → exams detected are reported with their event ids
```

The user never deals with versions/groups/duplication — the result of `cluster`
is presented as human text ("duplicate of X", "version update of Y", "new topic").

## Data flow — checkbox tick (P2 week)

```
POST /api/jobs/{id}/complete
  single SQLite transaction:
    1. job.status = 'done', done_at = now
    2. if kind == 'learn': insert revision jobs at intervals
       settings.revision_intervals_days (default [1,3,7,14]) into free slots
       (skip a round that already exists for this learn job)
    3. engine.replan(goal)  # redistribute remaining future work
  → response: {replanned: [...], revisions_added: [...]}
```

## Data flow — drag/move (P2 week)

```
POST /api/jobs/{id}/move {slot_start, slot_end}
  → reject 409 if it overlaps an external event or another job, is before now,
    or lands after the goal deadline
  → write the event slot, set jobs.pinned = 1
  → engine.replan(goal): pinned job stays, the rest reflows around it
```

## Engine algorithm (`engine.replan`)

```
inputs:  db snapshot, settings, now (injected)
output:  updated job slots

free_slots(day)  = capacity_slots(day)
               − external events on that day
               − done jobs (they keep their slot as history)
               − pinned jobs (user placements)
               − pending jobs kept in place (today's date)

capacity_slots(day) = settings.daily_capacity_hours (dynamic setting)
                      split into settings.slot_minutes chunks,
                      only where no external event runs

remaining = goal.effort_total − done_effort(goal)

1. KEEP: pending jobs on today's date and pinned jobs stay put (no churn)
2. PLACE: next unallocated work (topic order → material order) fills
   free slots from today → goal.deadline, one slot at a time
3. PULL-FORWARD: if remaining hits 0 before the deadline, the next
   material is scheduled immediately (the plan stays "full and current")
4. REVISIONS: after a learn job completes, rounds at +1d/+3d/+7d/+14d;
   existing done/skipped/pinned rounds are never duplicated; if a round slot
   is occupied, push to the next free slot; never silently drop — mark
   'conflicted' and surface in UI. Revisions respect the daily capacity and
   the goal deadline.

Invariants:
  - never overlaps external events
  - done jobs and pinned jobs never move
  - external events never change
  - no duplicate revision round for the same learn job
  - idempotent: same (db, settings, now) in → same out
  - pure: no I/O except the single transaction at commit
```

## Dynamic settings (the "fake time" defaults)

`daily_capacity_hours` and `slot_minutes` are **settings, not constants**.
Shipped placeholder values (no real usage data yet): **4 h/day, 50 min/job,
min slot 30 min, revision intervals [1,3,7,14] days**.

Who may change them: the user (P3 or settings endpoint) or the LLM
`tune_settings` skill — both bounds-validated (0.5–12 h, 15–120 min).
The engine reads the current value on every recompute; no restart, no
re-derivation.

## LLM integration rules
- All LLM I/O lives in `llm.py`; every skill has a deterministic fallback (offline mode).
- LLM output is validated (bounds above) **before** persisting; rejected output → fallback.
- LLM JSON may arrive wrapped in markdown fences — parsing strips fences before validation.
- The LLM endpoint is configured via `settings` (base_url + model + key), so switching
  providers (Cloudflare Workers AI / OpenAI / Ollama / llama.cpp) is a config change, not code.
- No key configured → `llm.enabled == False` → every skill takes its fallback path.
- `chat` is a bounded tool-calling loop (max 5 rounds) over a narrow registry: read-only
  tools (list_goals, list_jobs, list_events, list_materials, get_stats) and two bounded
  writes (update_settings, set_material_effort). Tool results are returned to the client
  as `actions` receipts; settings changes are bounds-validated and stored with
  `updated_by='llm'`. The engine remains the only scheduler — no tool moves jobs or
  touches external events (rule #2). Offline (no LLM configured): deterministic
  calendar-grounded fallback, no tools offered.
- `tune_settings` keyword path retired: settings changes now go through the explicit
  `update_settings` tool invoked by the model.

## Progress stats

`GET /api/stats` derives numbers from the job/material tables (no cache):
`planned_min` (pending+done jobs in range), `done_min` (done), `progress`,
`revisions_due`, per-day totals, and per-goal `total_effort_hours` vs
`done_effort_hours`. `by_day` covers the trailing 13 days and the next 14; jobs
outside the window are ignored. The Week progress bar and the Calendar stats panel
both read it.

## OCR (optional, M4)

`app/ocr.py` is a best-effort hook for scanned PDFs:
- off by default (`ocr_provider = "off"`);
- when `cloudflare` is configured, `pypdf` extracts embedded page images and each image
  is sent to Cloudflare Workers AI (stdlib `urllib`, no new dependency);
- any failure returns `None`, so ingest keeps the normal markitdown output and never fails.

