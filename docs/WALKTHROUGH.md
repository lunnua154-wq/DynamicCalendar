# Walkthrough — one concrete end-to-end scenario

This document uses the **real fixtures in `samples/`** with **real numbers**. If the code
behaves differently than this doc, the code is wrong (or this doc is stale — fix both, doc first).
Vocabulary: `docs/GLOSSARY.md`.

## Setup
- Today: **Tue 3 Nov 2026**, local zone UTC+8, all times local.
- Settings: `daily_capacity_hours = 4` (placeholder), `slot_minutes = 60` (example value —
  it's a dynamic setting), `revision_intervals_days = [1,3,7,14]`.
- The 4 h capacity lands in 14:00–18:00 on sample days (afternoons free in the fixture).

## Step 1 — Import
Import `samples/sample_calendar.ics`:
- 10 events in (9 concrete + 1 RRULE weekly expanded within the 30-day horizon)
- Exactly one `EXAM`-tagged event: **MATH2001 Final Exam, Fri 18 Dec 09:30–12:00 @ LT204**
- P1 shows the amber exam banner → user clicks **Create goal**

## Step 2 — Goal
Goal `G1 "MATH2001"` created: deadline **18 Dec**, linked to the exam event.

## Step 3 — Drop materials (LLM mode)
Drop all 4 files from `samples/materials/` at once:

| File | LLM `tag` | LLM `cluster` | Result |
|---|---|---|---|
| `math_chapter3_limits.md` | topic "Limits & Continuity", 3.0 h | new topic | scheduled |
| `math_chapter3_limits_v2.md` | same topic, 3.0 h (v2 content) | **version update of** v1 | v1 → `superseded`, v2 scheduled |
| `problem_set_3.md` | topic "Problem Set 3", 3.0 h | new topic | scheduled |
| `cs_lecture08_graphs.md` | topic "Graphs", 2.0 h | new topic, **no matching goal** | **unattached** (chip on row) |

Total effort on G1 = **6.0 h** (6 slots of 60 min). The Graphs file is unattached —
it is NOT scheduled (nothing to optimize toward); user later creates goal G2 "CS1010"
to attach it.

## Step 4 — Allocation (engine, deterministic)
Free slots (capacity 14:00–18:00, minus external events):

| Day | External | Free slots | Jobs placed |
|---|---|---|---|
| Tue 3 Nov | lecture 09:00–10:30 | 14:00, 15:00, 16:00, 17:00 | J1 Limits p1 (14:00) · J2 Limits p2 (15:00) · J3 Limits p3 (16:00) · J4 PS3 p1 (17:00) |
| Wed 4 Nov | CS lecture 10:00–12:00, gym 18:00 | 14:00, 15:00, 16:00, 17:00 | J5 PS3 p2 (14:00) · J6 PS3 p3 (15:00) |
| Thu 5 Nov | lecture 09:00–10:30 | 14:00–17:00 | (free — slack) |
| Fri 6 Nov | **lab 14:00–16:00** | 16:00, 17:00 | (free — slack) |

All 6 slots of G1 are done by 4 Nov — **weeks before the exam** → plan is "complete,
ahead of schedule" (pull-forward has nothing left; G2's unattached material stays put).

## Step 5 — Atomic tick (the dynamic moment)
User ticks **J1 (Limits p1)** on Tue 3 Nov at 15:05:

Single transaction:
1. J1 → `done` (done_at 15:05). Calendar keeps it (faded, struck).
2. Revisions inserted:
   - **R1 (+1d → Wed 4 Nov)**: slots 14:00/15:00 taken by J5/J6 → placed **16:00–17:00**
   - **R2 (+3d → Fri 6 Nov)**: lab occupies 14:00–16:00 → placed **16:00–17:00** (after lab)
   - **R3 (+7d → Tue 10 Nov)**: **14:00–15:00**
   - **R4 (+14d → Tue 17 Nov)**: **14:00–15:00**
3. Recompute: remaining G1 work = J2–J6 (5 slots). Today's pending jobs (J2–J4) keep their
   slots; J5/J6 (future) re-derive to the same slots → **zero moves** (stability/idempotency).
4. Week progress bar updates: `1h / 4h` for the week.

UI: J1 fades; R1–R4 appear with highlight pulse; nothing else moves.

## Step 6 — External change (re-import)
The course moves the Wed lecture to 13:00–15:00. User re-imports the updated `.ics`:
- The event updates in place (same UID), locked rows only change via this path.
- Recompute: Wed's free slots shift (13:00–15:00 now occupied) → J5/J6 + R1 reflow
  to the earliest free slots; **done J1 never touches**; external events never move.

## Step 7 — Offline-mode variant (no LLM key)
Same drops, no endpoint configured:
- `tag` fallback: tags = [filename stem], effort = `default_effort_hours` (1.0 h each)
- `cluster` fallback: hash de-dup only (all 4 hashes differ) → **4 topics, all attached**
- G1 total = 4.0 h → all 4 slots placed Tue 3 Nov 14:00–18:00
- P3 chat shows `(LLM off)`; the assistant still answers schedule questions (reads the calendar)
- Inbox rows show `ready` with the fallback summary (filename stem)

## Invariants to check after every step
- no job overlaps an external event
- done jobs never moved
- external events unchanged by the engine
- recompute is idempotent (run twice → same result)
