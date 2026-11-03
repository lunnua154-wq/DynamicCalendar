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
  → llm.tag()      (offline fallback: filename stem + default effort)
  → llm.cluster()  (offline fallback: new topic per file)
  → engine.replan(goal)   # future slots only, never touches done work
  → inbox status line updates: received → converting → tagged → done
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
    3. engine.replan(goal)  # redistribute remaining future work
  → response: {replanned: [...], revisions_added: [...]}
```

## Engine algorithm (`engine.replan`)

```
inputs:  db snapshot, settings, now (injected)
output:  updated job slots

free_slots(day)  = capacity_slots(day)
               − external events on that day
               − done jobs (they keep their slot as history)
               − pending jobs kept in place (see rule 1)

capacity_slots(day) = settings.daily_capacity_hours (dynamic setting)
                      split into settings.slot_minutes chunks,
                      only where no external event runs

remaining = goal.effort_total − done_effort(goal)

1. KEEP: pending jobs already placed on today's date stay put (no churn)
2. PLACE: next unallocated work (topic order → material order) fills
   free slots from today → goal.deadline, one slot at a time
3. PULL-FORWARD: if remaining hits 0 before the deadline, the next
   material is scheduled immediately (the plan stays "full and current")
4. REVISIONS: after a learn job completes, rounds at +1d/+3d/+7d/+14d;
   if a round slot is occupied by an external event, push to the next
   free slot (never silently drop — mark 'conflicted' and surface in UI)

Invariants:
  - never overlaps external events
  - done jobs never move
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
- The LLM endpoint is configured via `settings` (base_url + model + key), so switching
  providers (Cloudflare Workers AI / OpenAI / Ollama) is a config change, not code.
- No key configured → `llm.enabled == False` → every skill takes its fallback path.
