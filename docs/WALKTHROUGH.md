# Walkthrough — one concrete end-to-end scenario

This document uses the **real fixtures in `samples/`** with **real numbers**, produced
by the M1 implementation. If the code behaves differently than this doc, the code is
wrong (or this doc is stale — fix both, doc first). Vocabulary: `docs/GLOSSARY.md`.

## Setup
- Today: **Tue 3 Nov 2026, 12:00** local, zone UTC+8 (`utc_offset_minutes = 480`).
- Settings: `daily_capacity_hours = 4`, `slot_minutes = 60` (an example value — it is a
  dynamic setting; the shipped default is 50), `revision_intervals_days = [1,3,7,14]`.
- The study window is 08:00–22:00 **local**, so slots are 08:00, 09:00, … 21:00.

## Step 1 — Import
Import `samples/sample_calendar.ics`:
- **10 events in, 0 skipped** (9 concrete + the weekly RRULE event imported at its DTSTART)
- Exactly one `EXAM`-tagged event: **MATH2001 Final Exam, Fri 18 Dec 09:30–12:00 @ LT204**
- P1 shows the amber exam banner → user clicks **Create goal**

## Step 2 — Goal
Goal `MATH2001` created with the exam event's `dtstart` as deadline (**18 Dec 09:30**,
UTC `2026-12-18T01:30:00Z`) and `exam_event_id` linked.

## Step 3 — Drop materials (offline mode — no LLM key)
Drop all 4 files from `samples/materials/` at once. Every file is `ready` with the
deterministic fallback: tag = filename stem, effort = `default_effort_hours` (1.0 h).

| File | Result | Attached to |
|---|---|---|
| `math_chapter3_limits.md` | new topic `math_chapter3_limits` | MATH2001 |
| `math_chapter3_limits_v2.md` | new topic `math_chapter3_limits_v2` (offline: hashes differ, no version match) | MATH2001 |
| `problem_set_3.md` | new topic `problem_set_3` | MATH2001 |
| `cs_lecture08_graphs.md` | new topic `cs_lecture08_graphs` | **unattached** (no CS goal exists) |

Total effort on MATH2001 = **3.0 h** = 3 slots of 60 min. The Graphs file is not
scheduled — there is nothing to optimize toward.

## Step 4 — Allocation (engine, deterministic)
Free slots, capacity 4 h/day, minus external events:

| Day | External | Placed |
|---|---|---|
| Tue 3 Nov | lecture 09:00–10:30 (slots 09:00 and 10:00 collide and are skipped; morning slots before 12:00 are past) | **12:00 Limits**, **13:00 Limits v2**, **14:00 PS3** |
| Wed 4 Nov | CS lecture 10:00–12:00, gym 18:00 | (slack) |

All 3 h of MATH2001 work lands on Tue 3 Nov — **weeks before the exam** → the plan is
"complete, ahead of schedule". Pull-forward has nothing left to schedule because the
only unattached material belongs to no goal.

## Step 5 — Atomic tick (the dynamic moment)
User ticks **Limits (12:00–13:00)** at **15:05**:

Single transaction:
1. Job → `done` (`done_at 15:05`). The calendar keeps it (faded, struck through).
2. Four revision jobs are inserted, each landing in the first free slot on/after its target:
   - **R1 (+1d) → Wed 4 Nov 08:00–09:00**
   - **R2 (+3d) → Fri 6 Nov 08:00–09:00**
   - **R3 (+7d) → Tue 10 Nov 08:00–09:00**
   - **R4 (+14d) → Tue 17 Nov 08:00–09:00**
3. Recompute: nothing else moves (`moved_jobs` is empty), and Tue's pending jobs keep
   their slots.
4. Week progress bar becomes `1h / 5h` (three learn jobs + R1 + R2 fall in this week).

UI: the ticked card fades to `done` with a ✓, the four revision cards appear with a
violet dashed border.

## Step 6 — Rerun is a no-op
Pressing **Re-plan** twice afterwards reports `moved_jobs = []` and
`revisions_added = []`, and every job keeps its id — the recompute is idempotent and
does not churn rows.

## Step 7 — External change (re-import)
The course moves the Wed lecture to 13:00–15:00. Re-import the updated `.ics`:
- The event is updated in place (same `UID`); `imported = 1`, `skipped = 9`.
- Re-import re-plans every open goal, so Wed's free slots shift around the new lecture.
- Done jobs never move; external events are never touched by the engine.

## Step 8 — Dragging (P2)
Drag a pending job to another slot → `POST /api/jobs/{id}/move` sets `pinned = 1`.
Later replans keep that slot instead of re-deriving it away. Dropping onto an external
event is vetoed by `eventAllow` and rejected by the server with **409**.

## Invariants to check after every step
- no job overlaps an external event (E1)
- done jobs never moved (E2)
- external events unchanged by the engine
- recompute is idempotent and does not churn job ids (E3)
- revisions are never silently dropped — unplaceable rounds become `conflicted` (E7)
