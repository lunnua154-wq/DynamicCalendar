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
upload (multipart)                          [fast: store + create a row]
  → save original + compute content_hash (sha256)
  → hash de-dup first (deterministic, always)
  → material row status = 'processing', response returns immediately
background task (same pipeline, own DB connection)
  → markitdown → data/materials/markdown/<id>.md
  → llm.tag()      (offline fallback: filename stem + default effort)
  → llm.cluster()  (offline fallback: new topic per file, hash de-dup only)
  → material status = ready | duplicate | superseded | error
  → engine.replan(goal)   # future slots only, never touches done work
→ the Inbox polls `GET /api/inbox/files` every 2 s while a row is processing
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
inputs:  db snapshot, settings, now (injected), utc_offset_minutes
output:  updated job slots

local(dt) = dt + utc_offset_minutes        # the "user clock" for day windows
free_slots(day)  = capacity_slots(day)
               − external events on that day
               − done jobs (they keep their slot as history)
               − pending jobs kept in place (rules 1 and 2)

capacity_slots(day) = settings.daily_capacity_hours (dynamic setting)
                      split into settings.slot_minutes chunks inside the
                      local 08:00–22:00 study window

1. KEEP: pending jobs dated today keep their slot (no churn)
2. KEEP-PINNED: jobs the user dragged (jobs.pinned = 1) keep their slot
3. PLACE-LEARN: remaining learn work fills free slots from today → deadline,
   material order, earliest slot first (this is also pull-forward: small
   effort finishes today, the next material starts immediately)
4. REVISIONS: after a learn job completes, rounds at +1d/+3d/+7d/+14d scan
   forward for a free slot; the horizon stops at deadline + max(interval).
   No slot → the round is stored as 'conflicted' and surfaced in the UI
5. STALE: re-derived pending jobs with no place are deleted; jobs whose
   recomputed slot is unchanged are reused (no ID churn)

Invariants:
  - never overlaps external events
  - done jobs never move; pinned jobs never move
  - idempotent: same (db, settings, now) in → same out, same job ids
  - pure: no I/O except the single transaction at commit
```

Learn work is placed **before** revisions so a day's study load keeps its order
(`docs/WALKTHROUGH.md` step 5 shows the resulting slots).

## Dynamic settings (the "fake time" defaults)

`daily_capacity_hours` and `slot_minutes` are **settings, not constants**.
Shipped placeholder values (no real usage data yet): **4 h/day, 50 min/job,
min slot 30 min, revision intervals [1,3,7,14] days, UTC offset = host offset**.

`utc_offset_minutes` is the user's clock offset: the study window is local
08:00–22:00, while storage and the API stay UTC. It is seeded from the host on
first run and is a normal dynamic setting afterwards (e.g. travel, DST).

Who may change them: the user (settings panel or `PUT /api/settings`) or the LLM
`tune_settings` skill — both bounds-validated (0.5–12 h, 15–120 min).
The engine reads the current value on every recompute; a settings change also
re-plans every open goal immediately.

## LLM integration rules
- All LLM I/O lives in `llm.py`; every skill has a deterministic fallback (offline mode).
- LLM output is validated (bounds above) **before** persisting; rejected output → fallback.
- The LLM endpoint is configured via `settings` (base_url + model + key), so switching
  providers (Cloudflare Workers AI / OpenAI / Ollama) is a config change, not code.
- No key configured → `llm.enabled == False` → every skill takes its fallback path.
