# DynaCalendar — Project Blueprint

A **dynamic, calendar-orientated study manager** with a **lightweight LLM assistant**.

---

## 1. Core concept & principles

**Calendar-first.** The calendar is a mental model everyone already has (decades of use), it gives a big-picture overview, and it is a *standard format* (ICS / RFC 5545) — so anything from Google/Outlook/etc. can be imported by just dropping the `.ics` file in, and the whole system can be exported back out. The calendar is the **single source of truth**: every study job is a calendar event; every event renders with time, venue, and status.

**Dynamic.** The calendar is not a static plan. Whenever real progress changes (checkbox ticked, new file fed in, external event changes), the scheduler **recomputes future work in real time**. Completed work is immutable; the future reflows around reality.

**LLM is an assistant, not the brain.** The LLM never does scheduling math. Deterministic Python handles all allocation. The LLM does exactly three things:

1. **Tag** learning material (topic, summary, effort estimate)
2. **Classify / de-duplicate / cluster** fed-in files (versions, groups, duplicates)
3. **Chat** — answer questions ("what's due this week?", "summarize chapter 3")

This keeps LLM cost low, makes behavior predictable, and means the app works with any OpenAI-compatible endpoint (or none at all for M0).

---

## 2. Event model — two kinds of events

| Kind | Source | Time | Venue | Movable? | Checkbox? |
|---|---|---|---|---|---|
| **External** | imported `.ics` (classes, labs, exams, personal) | fixed, exact | **shown** | no (read-only; updated by re-import) | no |
| **Study job** | allocated by the engine | engine picks a free slot | optional note | yes (drag; engine may move it) | **yes** — the core progress signal |
| **Revision job** (sub-kind) | engine schedules after a learn-job is done | spaced intervals | — | yes | **yes** |

Key rule: **study jobs never overlap external events.** External events are hard constraints; study jobs flow around them.

---

## 3. Pages — exactly three

### P1 — Calendar (Home) · month view
- FullCalendar month grid
- External events: solid color, exact time + venue on click/hover
- Study/revision jobs: different color, show material title
- Click a day → day side-panel with the checkbox list
- Top bar: **Import .ics** · **Re-plan** (force recompute) *(export is a future feature — see DECISIONS)*

### P2 — Week · the working view
- FullCalendar time-grid (week) view
- **AI-allocated revision jobs: cards with checkboxes** (primary content of this page)
- **External events: exact time + venue** (pinned, not draggable)
- Week progress bar: completed effort vs. planned effort
- **Drag a job to another slot = instant reschedule**, remaining jobs reflow
- Today column highlighted

### P3 — Inbox · chat + file feed-in
- Left: chat with the assistant
- Right: drop zone — **"just drop everything in"**
- Per-file pipeline status: `received → converting (→ markdown) → tagging → result`
- Result is one of: *new topic* / *duplicate of X* / *version update of Y* / *added to group Z*
- **The user never cares about filenames, versions, groups, or duplication** — the system decides and the user can override via chat or a button
- Bottom: topic (group) list with member files; merge/split via chat or UI

---

## 4. Data model

```
Goal                          e.g. "pass exam X" (the unit the engine optimizes)
 ├─ deadline                  from an external "exam" event, or set manually
 ├─ Topics (clusters)         LLM-suggested groups of materials
 │   └─ Material              file → markdown + tags + effort_estimate (hours)
 └─ Jobs                      allocated work units (kind: learn | revision)
      ├─ slot                 date + start time + duration
      ├─ status               pending | done | skipped
      └─ material_ref

progress(goal)  = done_effort / total_effort
remaining(goal) = total_effort − done_effort
```

- **Spacing rule** (deterministic, no LLM): after a *learn* job is done, schedule *revision* jobs at **+1d, +3d, +7d, +14d** (configurable).
- **Allocation rule**: spread `remaining` across **free slots** (slots not occupied by external events) from today → deadline, earlier slots filled first; if the user is ahead, pull the next material forward.
- **Capacity & slot length are dynamic settings** (not constants): placeholder defaults `4 h/day`, `50 min/job` ("fake time" for now); user or the LLM `tune_settings` skill (bounds-validated) can change them; the engine always reads the current setting.
- LLM effort estimates are **stored data** (user-overridable). The engine reads them; it never asks the LLM to compute.

---

## 5. Dynamic behavior (deterministic rules)

| Trigger | Effect |
|---|---|
| Tick a job checkbox | mark done → update progress → schedule revisions → recompute all *future* jobs |
| Drop new file(s) in inbox | ingest → markdown → tag → effort added → future slots recomputed only |
| Re-import `.ics` (external change) | recompute around the new external layout |
| Drag a job (week view) | its slot moves, the rest reflows around it |
| "Re-plan" button | full recompute of all pending future work |

Invariants: **completed jobs are never touched. External events are never touched by the engine.**

---

## 6. Tech stack (dependency-conscious)

### Server — Python 3.12 (pinned in Docker; host has 3.14, fine for dev)
| Dependency | Purpose | Why it's worth it |
|---|---|---|
| `fastapi` + `uvicorn[standard]` | API + static page hosting | tiny, standard |
| `sqlite3` (stdlib) | single-file DB | **no ORM** — keeps the list short |
| `icalendar` | ICS parse & emit | the formal calendar format, both directions |
| `markitdown` + extras `pypdf`, `mammoth`, `python-pptx` | **any document → markdown, one library** | exactly the "feed in without caring" requirement; pin only these extras |
| `openai` (SDK) | LLM client | works with **any** OpenAI-compatible endpoint: Cloudflare Workers AI, OpenAI, local Ollama — **no key yet: offline mode is first-class** (deterministic fallbacks, see docs/ARCHITECTURE.md) |
| `python-multipart` | file uploads | required by FastAPI uploads |
| *(optional, later)* Cloudflare OCR (Workers AI) | scanned PDFs | no heavy local OCR (no tesseract) in the base image |

### Frontend — Vite + Tailwind + FullCalendar (vanilla JS, **no framework**)
- 3 HTML pages + shared vanilla JS (easy to manage, matches "I don't want too many pages")
- `FullCalendar` v6.1 (MIT, the last release line that ships vanilla-JS packages) for month + week views — checkbox cards via `eventContent`, drag-reschedule via `eventDrop`, drop veto via `eventAllow` (never onto external events)
- `Tailwind CSS` for all styling (the officially blessed theming path — "styled entirely by your own CSS or Tailwind")
- `Vite` for dev + build (built `web/dist/` served by FastAPI) — the only build step in the repo
- `fetch` for API calls (no htmx needed)

### Storage
```
data/
  dynacalendar.db        # SQLite — events, goals, materials, jobs, chat
  materials/<id>.md      # converted markdown (+ original file kept)
  calendar.ics           # canonical export, always in sync with DB
```

### Docker
- `Dockerfile` on `python:3.12-slim`
- `compose.yaml`: app service + optional `ollama` profile for local LLM
- ⚠️ Your Docker daemon is not currently running (Docker Desktop needs starting) — or just run the app directly with host Python; both paths supported

---

## 7. ICS sync (the "works with sync easily" part)

- **Import (now)**: upload `.ics` (or point at a folder) → parse → external events (read-only, exact time + venue preserved); events tagged `exam` become goal deadline candidates
- **Export (future, post-M4)**: one-click combined `.ics` (tagged via `CATEGORIES`, `SUMMARY: [REVISION] …`) — deferred per decision log
- Timezones: stored in UTC, rendered in the user's zone (`zoneinfo`)

---

## 8. Project layout

```
dynacalendar/
  BLUEPRINT.md
  README.md
  AGENTS.md
  docs/
    ARCHITECTURE.md
    DATA_MODEL.md
    API.md
    STYLE.md
    DECISIONS.md
  compose.yaml
  Dockerfile
  app/
    main.py          # FastAPI app + routes (serves the 3 pages + API)
    config.py        # settings (LLM endpoint, spacing, capacity…)
    db.py            # sqlite3 data layer (no ORM)
    ics_sync.py      # import / export
    ingest.py        # file → markdown pipeline, de-dup, tagging glue
    engine.py        # deterministic scheduler (allocation, revisions, recompute)
    llm.py           # pluggable LLM client (OpenAI-compatible)
  web/
    calendar.html    # P1
    week.html        # P2
    inbox.html       # P3
    common.js        # shared API client + rendering
    style.css
  data/              # runtime (gitignored)
  tests/
```

---

## 9. Milestones

| # | Deliverable | LLM? |
|---|---|---|
| **M0** | Skeleton: 3 pages, ICS import, month + week rendering, event store, checkbox state — proves the core UX | no |
| **M1** | Inbox pipeline: upload → markdown (markitdown), material store, file status list | no |
| **M2** | LLM assistant: chat + auto-tag / de-dup / cluster of dropped files; endpoint config | yes |
| **M3** | Dynamic engine: goals, allocation, spaced revisions, checkbox → live recompute, week-view checkboxes + drag-reschedule | no (estimates from M2) |
| **M4** | Progress stats, Cloudflare OCR for scanned PDFs, Docker hardening, tests *(ICS export = future feature)* | optional |

**Shipped:** M0 and M1 (2026-11-05). The M3 engine pieces (goals, allocation, spaced
revisions, checkbox → live recompute, week-view checkboxes + drag-reschedule) shipped
alongside them; `README.md` carries the current status and `docs/TESTING.md` the green
test matrix.

---

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Scanned PDFs have no text layer | Cloudflare OCR (Workers AI) added at M4; M1 assumes text-based PDFs |
| LLM effort estimates can be wrong | stored as data, user-overridable; LLM never does scheduling math |
| ICS timezone drift | UTC storage + `zoneinfo` |
| `markitdown` extras bloat the image | pin only `pypdf`, `mammoth`, `python-pptx` |
| Scope creep (multi-user, sync servers, etc.) | **single-user local app by design**; everything else out of scope |

---

## 11. Decisions (confirmed 2026-11-03 — full log in docs/DECISIONS.md)

1. **LLM**: keep the OpenAI-compatible client (pluggable: Cloudflare / Ollama / OpenAI). **No key yet** → offline mode with deterministic fallbacks is a first-class path, not an afterthought.
2. **Exam dates**: both — auto from external events tagged `exam` **and** manual goal entry.
3. **Capacity & slot length**: dynamic settings with placeholder defaults (4 h/day, 50 min/job); LLM `tune_settings` skill or user override (bounds-validated).
4. **Sync**: **import only** for now; export is a future feature.
5. **UI language**: English (default kept).
6. **Docs-first**: full document set (ARCHITECTURE / DATA_MODEL / API / STYLE / DECISIONS + AGENTS.md) completed **before** M0 code.

## 12. Document set

`BLUEPRINT.md` · `AGENTS.md` · `CONTRIBUTING.md` · `docs/ARCHITECTURE.md` · `docs/DATA_MODEL.md` · `docs/API.md` · `docs/STYLE.md` · `docs/DECISIONS.md` · `docs/GLOSSARY.md` · `docs/TESTING.md` · `docs/UX_SPEC.md` · `docs/WALKTHROUGH.md` · `README.md`
