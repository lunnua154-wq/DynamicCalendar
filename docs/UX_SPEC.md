# UX Spec (M0–M5)

Detailed per-page specification. This is the contract for UI work — if the implementation
and this doc disagree, the doc wins (update the doc first, then the code).
Vocabulary: see `docs/GLOSSARY.md`. Styling: Tailwind, tokens defined in `web/tailwind.css`.

## Global

- **Top navigation** (all pages): app name "DynaCalendar" left; tabs `Calendar` `Week` `Inbox`
  (active tab underlined); right side: settings gear (opens settings panel).
- **Toasts**: top-right, stack, auto-dismiss 4 s. Kinds: `info` / `success` / `error`.
- **Loading**: skeleton rows/bars; never a blank page.
- **Error handling**: every failed API call → error toast with the server's message;
  UI state is never left half-updated (revert optimistic changes).
- **Colors** (theme tokens, Material 3 light scheme): `external` = sky (solid), `study` =
  amber (left-accent card), `revision` = violet (dashed border), `done` = faded + strikethrough;
  everything else uses the M3 role tokens (`primary`, `surface-container*`, `outline*`).
- **Desktop-first** (M0). Below 768 px: week grid collapses to a vertical list per day
  (M4/M5 polish) and must remain usable: no horizontal scroll, checkboxes reachable.
- **Settings panel** (M2/M3/M4): LLM endpoint (`base_url`, `model`, `api_key`) **and**
  dynamic settings (`daily_capacity_hours`, `slot_minutes`, `default_effort_hours`),
  plus optional OCR (`ocr_provider`, `cf_account_id`, `cf_api_token`, `ocr_model`).
  Values load from `GET /api/settings`; out-of-bounds values show the server error and
  do not half-save. The LLM badge reads `LLM: on/off` from base URL + model.

---

## P1 — Calendar (month) · route `/`

```
┌────────────────────────────────────────────────────────────────────┐
│ DynaCalendar   Nov 2026   ◀ Today ▶     [Import .ics] [Re-plan]  │
├────────────────────────────────────────────────────────────────────┤
│ Mo Tu We Th Fr Sa Su                                             │
│ ┌──────┬──────┬──────┬──────┬──────┬──────┬──────┐               │
│ │  1   │  2   │  3   │  4   │  5   │  6   │  7   │  (day cells) │
│ │      │gym   │MATH L│CS L  │MATH L│MATH L│      │               │
│ │      │      │+2    │      │      │      │      │               │
│ └──────┴──────┴──────┴──────┴──────┴──────┴──────┘               │
├────────────────────────────────────────────────────────────────────┤
│ (right panel — appears when a day is clicked)                     │
│  Tue 3 Nov                                                        │
│  ● 09:00–10:30  MATH2001 Lecture — Week 4   LT204  [external]    │
│  ☐ 14:00–15:00  Limits & Continuity (part 1) [job, 50m]          │
│  ☐ 15:00–16:00  Revision R2 — Limits        [revision, 50m]      │
└────────────────────────────────────────────────────────────────────┘
```

- Month grid: `dayGridMonth`. Day number top-left; today = accent ring.
- Event chips in a cell: max 3 visible, then `+n more` (clicking the cell opens the day panel).
- **External chips**: solid `external` color, no checkbox.
- **Job chips**: `study` color; **revision chips**: `revision` color; done = faded + strike.
- Click a day → **day panel** (right side on desktop, bottom sheet on narrow):
  - external entries: time range + **venue** + title — read-only (no controls)
  - job entries: **checkbox** + title + duration + kind badge
  - ticking a job here behaves exactly like P2 (one atomic API call, optimistic update)
- **Import .ics**: file picker → spinner → success toast `Imported N events (M skipped)`
  + calendar refresh. Invalid file → error toast with reason (e.g. "not an ICS file").
- **Exam banner** (after an import that contains `EXAM`-tagged events and no goal is
  linked yet): amber banner under the header:
  `Exam detected: MATH2001 Final Exam — Fri 18 Dec 09:30 @ LT204   [Create goal] [Dismiss]`
  "Create goal" pre-fills title/deadline/venue from the event and sends the real
  `exam_event_id` to the API.
- **Goal progress / stats panel** (M4/M5): a compact panel under the header or in the
  right rail showing, per open goal, `done h / total h` with a bar, plus
  `revisions due` for the next 7 days. Data comes from `GET /api/stats`.
  Empty state: hidden until at least one goal exists.
- **Re-plan**: confirm dialog ("Recompute all pending future work?") → spinner →
  toast with count of moved jobs.
- **Empty state** (no events at all): centered illustration-less message:
  `No events yet. Import your calendar (.ics) to start — study jobs will appear around it.`

## P2 — Week · route `/week`

```
┌────────────────────────────────────────────────────────────────────┐
│ DynaCalendar   2 – 8 Nov 2026  ◀ Today ▶    [week progress bar]  │
├────────┬───────────────────────────────────────────────────────────┤
│ time   │ Mo 2    Tu 3    We 4    Th 5    Fr 6    Sa 7    Su 8    │
│ 08:00  │         │         │ CS L    │         │         │         │
│ 09:00  │         │ MATH L  │         │ MATH L  │         │         │
│ 10:30  │         │         │         │         │         │         │
│ ...    │         │         │         │         │         │         │
│ 14:00  │ gym     │ [☐ Lim…│ gym     │         │ [☐ R2 —│         │
│ 15:00  │         │ [☐ Lim…│         │         │ MATH L  │         │
│ 16:00  │         │ [☐ PS3 │         │         │ [☐ R2 …│         │
└────────┴───────────────────────────────────────────────────────────┘
```

- Time grid: `timeGridWeek`, 7 days, scroll container ~520 px, hour lines light.
- **Today column**: accent background tint; "today" pill in the header cell.
- **External events**: solid `external` cards, showing `HH:MM–HH:MM` + **venue** on one line
  (venue in muted style). **Not draggable** (cursor: not-allowed + tooltip "external — fixed").
- **Job cards**: white card, `study` left-border; content:
  `☐` checkbox · title (material name) · duration badge · topic in muted small text.
  Revision cards: `revision` left-border, title prefixed `R2 · <material>`.
- **Checkbox**: the star of this page. Ticking:
  1. optimistic check (immediate, 100 ms)
  2. `POST /api/jobs/{id}/complete`
  3. on success: card fades to `done`; other job cards **animate to new slots** (transition 200 ms);
     new revision cards appear with a one-time highlight pulse
  4. on failure: revert the check, error toast
- **Drag to reschedule** (jobs only):
  - drag ghost shows target slot time
  - drop on a free slot → optimistic move → `POST /api/jobs/{id}/move` → others reflow (animated)
  - drop onto/over an external event → **veto** (`eventAllow`): snap back + toast
    `Can't schedule over: MATH2001 Lab (Fri 14:00)`
  - drop out of range (beyond deadline / before today) → snap back + explanatory toast
- **Click a job card** (not the checkbox) → popover: material title, topic, effort,
  status, created; actions: `[Open material]` `[Mark skipped]` `[Re-plan goal]`.
- **Week progress bar** (header): `done effort / planned effort this week` as a thin bar +
  label (e.g. `2.5h / 4h`). Zero planned → bar hidden.
- **Empty state**: `Nothing planned for this week. Create a goal and drop materials in the Inbox.`

## P3 — Inbox · route `/inbox`

```
┌────────────────────────────────────────────────────────────────────┐
│ DynaCalendar          [goal selector: MATH2001 ▾]                 │
├──────────────────────────┬─────────────────────────────────────────┤
│ CHAT   (LLM off)        │  INBOX                                  │
│                          │  ┌───────────────────────────────────┐ │
│ ▸ What's due this week?  │  │  Drop files here — just dump     │ │
│ ◂ LLM is not configured…│  │  everything in: pdf, docx, pptx, │ │
│                          │  │  images, html…                   │ │
│ [message____________] [→]│  └───────────────────────────────────┘ │
│                          │  math_chapter3_limits_v2.md           │
│                          │  ● ready   version update of limits v1│
│                          │    [Limits & Continuity] 3.0h [edit]  │
│                          │  cs_lecture08_graphs.md               │
│                          │  ● ready   new topic "Graphs" (unatt.)│
│                          │  ─ processing…  (spinner row)         │
├──────────────────────────┴─────────────────────────────────────────┤
│ TOPICS:  Limits & Continuity (2 files)  Problem Set 3 (1)        │
│            [merge ▾] [rename]                                     │
└────────────────────────────────────────────────────────────────────┘
```

- **Left — chat**: message list (user right, assistant left), input + send.
  - Badge `(LLM off)` next to the title when no endpoint is configured; assistant replies
    then are the offline fallbacks (still useful: reads the calendar, reports schedules).
  - Chat answers about the calendar ("what's due this week?") are rendered with the
    same event chips as the day panel.
  - Assistant replies may be followed by a compact action receipt row (⚙ lines, error
    lines in rose) listing each tool the chat ran (e.g. `⚙ read goals`,
    `settings → daily_capacity_hours 6, slot_minutes 50`, `⚙ effort → limits.pdf → 5h`).
    Receipts appear for live replies only — they are not persisted and do not show
    when history is reloaded.
- **Right — inbox**:
  - Drop zone: drag & drop or click-to-browse, multiple files at once.
  - Each file row: name, **status chip** (`processing` spinner / `ready` / `duplicate` /
    `superseded` / `error` + reason), result line in human text
    (`new topic "X"` · `duplicate of Y` · `version update of Y`), tags as small chips,
    effort with inline edit (number, hours), row actions: `[Dismiss]` (mark duplicate/
    superseded — user override) `[Open]`.
  - A `ready` row with no goal shows an amber `unattached` chip — without one it is
    invisible on the calendar (jobs are only scheduled for goal-attached materials).
  - An amber banner above the file list counts ready+unattached files and offers
    one-click **Attach all to "<selected goal>"** (`POST /api/inbox/attach_ready`,
    single replan).
  - Status updates: poll `GET /api/inbox/files` every 2 s while any row is `processing`
    (stop when all settle).
- **Goal selector** (top-right): the dropdown pre-selects the first open goal (drops
  land attached by default); `Unattached` is a valid state — pick it explicitly if you
  want to stash materials without scheduling; shown as a chip on the row.
  - Beside it, a title + deadline input and `New goal` button create a goal via
    `POST /api/goals` (deadline required) and select it in the selector.
- **Topics strip** (bottom): each topic = name + file count + actions `[merge ▾]` `[rename]`;
  merging = pick target topic, members re-attach, re-plan triggered.
- **Errors**: unsupported file type → row `error` + `unsupported format (xxx)` — the row
  stays, nothing is lost; user can retry with a converted file.
- **Empty state** (no files): the drop zone is the star; no fake data.

## First-run tour (all pages)

Onboarding is an overlay, never a route: the app stays at three pages
(`docs/DECISIONS.md`, 2026-11-08). Everything below lives in `web/src/tour.js`.

- **Trigger**: a first visit auto-starts the tour **only on `/`**. Landing deep on
  `/week` or `/inbox` never hijacks the screen; the `?` button starts it anywhere.
- **Entry point**: `?` (`#btn-tour`, left of ⚙) in every header replays from step 1.
  On `/week` or `/inbox` it navigates to `/?tour=1` first.
- **Five steps**, each explaining *why*, not only where to click:
  1. `/` → `#btn-import` — the imported calendar is the source of truth.
  2. `/` → `#exam-banner` — a deadline becomes a goal (deadline = exam date).
  3. `/inbox` → `#drop-zone` — materials are converted, tagged and de-duplicated.
  4. `/week` → `#week-calendar` — the deterministic planner fills the free gaps.
  5. `/week` → `#week-calendar` — a tick is atomic (done + 4 revisions + reflow);
     dragging pins a block.
- **Crossing pages**: `Next`/`Back` store the step index and load
  `<page>?tour=1`; the destination page picks the step up on load. No API call.
- **Spotlight**: the target is scrolled into view and ringed, the rest of the page
  is dimmed by the ring's `box-shadow` cut-out. When the target is missing or has
  no layout (an `.ics` with no exam event → no banner) the step renders as a
  centred card and its copy says so — the tour never points at nothing.
- **Exit**: `Skip tour`, `Esc` or `Finish` set `dynacalendar.tour.done=1` and drop
  the stored step, so it never re-opens by itself; `?` clears the flag and replays.
- **Storage**: `dynacalendar.tour.done` / `dynacalendar.tour.step`. A failing
  `localStorage` (private mode) degrades to "the tour works for this page view".
- **Scope**: presentation only. The tour never calls the API and never mutates
  calendar data — the demo can run it on seeded data without side effects.

## Interaction invariants (all pages)
1. Any optimistic UI change that fails a server call **reverts** (no half-states).
2. A checkbox tick is always the single `POST /api/jobs/{id}/complete` call — never batched.
3. External events render identically on P1 and P2 (same data, same styling).
4. Every state change is reflected in the nav (e.g. a completed job counts toward progress).
5. No alert()/confirm() — use the dialog/toast components.
