# LLM-Utilised Placement — Design & Roadmap

Branch: `better-llm-utilisation` · Author: plan for review · Status: **Phase 1+2 implemented on this branch; Phase 3+ roadmap**

## 1. What the user asked for

> Let the LLM handle more; let it **choose time slots**, not just tag materials and
> tune effort hours. Examples:
> - avoid allocating jobs **right after tiring 3-hour lectures**;
> - avoid allocating **math right after a math class**.

And: the current architecture is a huge constraint (`AGENTS.md` #1 — *the LLM never
schedules*), so this needs a deliberate migration path, not a patch.

## 2. The core insight

The interesting LLM capability here is **not** "compute the next free slot" — a
greedy loop already does that deterministically and perfectly. The interesting
capability is **judgement under soft constraints**:

- semantic matching ("this is a *math* class", "this lecture is *draining*"),
- natural-language preferences ("don't study math right after math"),
- trade-offs ("morning better for deep work, evening ok for revisions"),
- arbitration between competing soft goals.

Meanwhile everything that makes the app trustworthy — *hard* guarantees — is
deterministic and must stay deterministic: no overlapping externals, capacity
caps, deadlines, done/pinned immutability, no duplicate revision rounds.

So the architecture is **"LLM proposes, deterministic engine disposes"**:

```
                 ┌────────────────────────────────────────────┐
                 │  engine.replan (deterministic, pure)        │
                 │                                             │
 free slots ───► │  1. enumerate VALID candidates              │
                 │     (hard constraints: overlap, capacity,   │
                 │      deadline, done/pinned immutability)    │
                 │  2. score candidates (soft penalties from   │
                 │     built-in wisdom + stored constraints)   │
                 │  3. pick a slot:                            │
                 │     • chooser=None → argmin(penalty, start) │
                 │     • chooser set  → LLM pick from a small  │
                 │       shortlist, re-verified, else fallback │
                 └──────────────┬─────────────────────────────┘
                                │
                   every created job records WHY:
                   jobs.placement_source ('engine'|'llm')
                   jobs.placement_note   (human-readable reason)
```

The LLM is **never** in the hard-constraint path. A wrong LLM answer can pick the
*second-best* slot, never an invalid one: the candidate shortlist is already
hard-valid, membership is re-checked, and any exception falls back to the
deterministic pick.

### What this replaces vs. what it keeps

| Aspect | Before | After (this branch) |
|---|---|---|
| Slot choice | earliest free slot | **best-penalty slot** (smart placement, on by default); LLM may rank a shortlist when `llm_placement_mode='rank'` |
| External events | opaque time ranges | **annotated**: `subject` + `fatigue` (0–3), used by the scorer |
| Preferences | none | **constraints as data**, compiled from natural language, enforced deterministically |
| Placement trust | opaque | **receipts**: `placement_source` + `placement_note` on every job |
| LLM failure | n/a | **silent fallback** to deterministic; placement never blocks a plan |

## 3. The three pillars

### Pillar A — Semantic annotations (events are understood, not just avoided)

`event_annotations(event_id PK, subject, fatigue 0–3, source, note)`:

- `fatigue`: how draining an event is. Heuristic (offline): ≥3h → 3, ≥2h → 2,
  ≥45min → 1; `+1` for `lab` in the title (cap 3). LLM annotation overrides with
  real semantics (a 2h "Linear Algebra Problem Session" is fatigue 3; a 3h
  "Film Studies" marathon may be 1).
- `subject`: normalised lowercase topic (`math`, `physics`, `general`, …).
  Heuristic: keyword map over the title. LLM: batched chunks of **15 events**
  (explicit `POST /api/events/annotate`, ~45 s budget, per-chunk failure
  isolation); per-item validation. App startup and ICS import annotate
  **heuristically only** — deterministic, instant, and never able to stall a
  request.
- Materials contribute their **tags** as subject candidates (no schema change).
- Users can re-annotate any time: `POST /api/events/annotate`.

Fatigue levels feed built-in wisdom:

```
slot starts within 60 min after a fatigue≥2 external → +30 penalty (fatigue 3 → +60)
```

### Pillar B — Constraints as data (natural language → structured rules → deterministic enforcement)

New table `constraints`:

```
id · kind · params(JSON) · enabled · hard · source('user'|'llm') · label · created_at
```

v1 kinds (per-kind schemas, bounds-validated server-side, unknown → 4xx):

| Kind | Params (bounds) | Meaning |
|---|---|---|
| `post_fatigue_buffer` | `min_fatigue` 1–3, `buffer_minutes` 15–240, `hard` | No study within N min after an event with fatigue ≥ N |
| `subject_gap` | `subject` (≤40 ch, lowercase), `gap_minutes` 15–240, `hard` | No study of *subject* within N min after an external event of the same subject — **"no math right after math class"** |
| `interleave` | `max_consecutive` 1–5 | Penalise a job whose subject equals the previous N placed jobs' subjects |
| `blocked_day_part` | `start_hour` 0–23, `end_hour` 0–23, `hard` | Never (or penalise) study in a window (e.g. 12–13 lunch) |

- **soft** (default): added to the slot penalty — the planner still guarantees
  feasibility, it just prefers compliant slots.
- **hard**: candidate filtered out. **Relaxation rule:** if *no* candidate
  survives hard filtering, the engine relaxes hard penalties to soft and stamps
  the job's `placement_note` with `hard constraints relaxed to fit` — a plan can
  never deadlock or silently drop work.
- NL → args compilation happens in **two** paths:
  1. When an LLM is configured, chat tool `add_constraint` receives structured
     args ("avoid math right after math class" → `kind=subject_gap,
     params={subject:'math', gap_minutes:60}, label=…`). The LLM is the
     compiler; Python is the validator (`constraints.validate_params`).
  2. Offline first-class: `constraints.parse_nl(text)` recognises the common
     patterns with regexes and creates the constraint deterministically, so the
     feature works with **no LLM key at all** (AGENTS.md #7).

### Pillar C — LLM slot selection (`llm_placement_mode`)

Setting `llm_placement_mode`: `off` (default) | `rank`.

When `rank` and an LLM is configured, each placeable job is offered a
**shortlist of ≤4 candidates**:

- the 2 earliest valid slots,
- the 2 lowest-penalty slots,

…de-duplicated, each annotated with machine-readable features (fatigue of the
preceding event, subject match, day part, penalty breakdown). The model answers
with `{"choice": <index>, "reason": "…"}`. Validation: index in range, reason
clamped to 200 chars; anything else → deterministic pick. One LLM call per job,
capped at `MAX_LLM_PLACEMENT_CALLS` (16) and `MAX_LLM_PLACEMENT_SECONDS` (20) per
replan, shared between learn and revision placements. `temperature=0`.

Why a shortlist instead of letting the LLM emit arbitrary datetimes?
Verification is then **membership in a hard-valid set** — sound by construction,
trivial to test, impossible for the LLM to "cheat" into an invalid slot.

Creative extensions (Phase 3+): full-assignment `choose` mode with global
verifier; per-replan batched placement in a single prompt.

## 4. What gets implemented on this branch (Phase 1 + 2)

1. **Smart placement (deterministic, default ON)** — `smart_placement=on`.
   Candidates are scored with built-in penalties and the planner places at
   `argmin(penalty, slot_start)` instead of *earliest*. Note: the built-in
   back-to-back same-subject nudge (+10) applies even with **no annotations
   at all**, so default slots can differ from the pre-branch packer (e.g.
   two math materials land 08:00/09:40, not 08:50). `smart_placement=off`
   restores the exact pre-branch earliest-free-slot behaviour (all existing
   placement invariants remain green under either setting).
2. **Annotations** — heuristic at import + app startup; LLM (chunked,
   time-boxed) only on explicit `POST /api/events/annotate {"force": true}`.
   Table + `db` helpers.
3. **Constraints** — table, validators, scorer, REST CRUD
   (`GET/POST/DELETE /api/constraints[/{id}]`), applied by the engine;
   `hard` relaxation rule.
4. **Chat tools** — `list_constraints`, `add_constraint`, `remove_constraint`
   (narrow registry, receipts in `actions`); offline chat parses the NL
   constraint patterns directly.
5. **LLM ranking** — `llm_placement_mode=rank`; `LLMClient.choose_placement`,
   injection via `engine.replan(..., chooser=)`; `app/planner.py` builds the
   chooser (cap 16 calls/replan); all replan call sites switch to it.
   `engine.py` itself stays free of network I/O — the chooser is an
   **injected dependency**, exactly like the injected clock (AGENTS.md #9).
6. **Explainability** — `jobs.placement_source`, `jobs.placement_note`;
   surfaced in `GET /api/jobs` automatically (`select j.*`) and as a tooltip in
   the week view.
7. **Settings** — `smart_placement` (on/off), `llm_placement_mode` (off/rank),
   bounds-validated, replan-triggering over `PUT /api/settings`.
8. **Tests** — `test_placement.py` (scoring, buffer, subject gap, hard
   relaxation, chooser injection, fallback), `test_constraints_api.py` (CRUD +
   validation errors + NL parser), LLM tests for `choose_placement` /
   `annotate_events` (mocked).
9. **Docs** — this file; `AGENTS.md` #1 rewritten; new `DECISIONS.md` entries;
   `API.md`; `ARCHITECTURE.md` placement section.

## 5. What is deliberately NOT on this branch (roadmap)

| Phase | Item | Why later |
|---|---|---|
| 3 | `llm_placement_mode='choose'` — LLM assigns the *whole* pending plan; global verifier repairs per-job; one shot replan | needs Phase-2 telemetry to know per-job ranking quality |
| 3 | **Chronotype / energy curve**: infer from drag & tick telemetry (skipped morning jobs → shift load later) | needs event telemetry table + consent-worthy data ethics pass |
| 3 | UI: constraint manager panel (inbox sidebar or week-view "why here?" popover per job) | violates nothing-page rule if implemented as panel inside existing pages; needs UX_SPEC entry first; kept out to minimise risk this phase |
| 4 | Local ranking model distilled from LLM choices (`phi-2`/`llama-3.2-1b` via Ollama) | wait for real usage logs from Phase 2 |
| 4 | `explain_plan` tool — LLM summarises the week in NL with receipts | trivial once notes exist; scheduled after UI surfaces notes |
| 4 | A/B harness: `scripts/eval_placement.py` — constraint-satisfaction rate, churn vs baseline | needs a week of data |
| 5 | Spaced-repetition aware *difficulty*: effort weighting by recall curve (LLM-graded difficulty per material) feeding placement | prerequisite: material difficulty tagging skill |

## 6. Honest constraints & trade-offs

- **"Not safe as highest priority" (user's call) is honoured within hard
  invariants.** We deliberately let the LLM pick slots and act on NL preferences —
  that is the risky-looking part. We do **not** let it: overlap externals,
  move done/pinned jobs, exceed capacity, schedule past deadlines, or delete
  revision rounds. Those stay absolute.
- **Determinism is scoped, not abandoned.** `smart_placement=off` and
  `llm_placement_mode=off` together restore the exact pre-branch behaviour.
  `engine.replan(chooser=None)` is a pure function; the LLM chooser is injected.
- **Offline mode remains first-class.** Heuristic annotations, deterministic NL
  parser, penalty scorer, and relaxation rule all work with zero LLM. Ranking is
  the only LLM-only piece and defaults off.
- **Cost:** ranking adds ≤16 short completions per replan (and ≤20 s wall-clock);
  revisions share the budget. Annotation is free at import/startup (heuristic);
  LLM annotation is chunked (15 events/call) and time-boxed (~45 s) only on the
  explicit annotate endpoint.
- **Subject inference is heuristic** (tags/keywords) until the LLM normalises;
  misclassification degrades placement quality, never validity.
- **Scan cost**: candidate search is O(days × slots × (externals+jobs)) in the
  worst case, but the argmin over penalties ≥ 0 is found at the *first*
  penalty-0 valid slot, so typical replans touch a handful of candidates;
  full-horizon scans only happen when every candidate carries a positive
  penalty or when a chooser is injected (the shortlist needs the survey).
  Measured ~3 s for a 42-day horizon / 60-material plan with chooser off after
  the early exit; keep an eye on this as goals grow.

## 7. Migration & rollback

- Schema: two new tables + two `jobs` columns via light `ALTER TABLE` migrations
  (same pattern as `pinned`). No backfill required; `placement_note` NULL on
  legacy jobs.
- Rollback: `git revert` the branch; dropped tables/columns are inert.
- Existing databases keep working; first replan after upgrade re-derives future
  unpinned jobs as usual (against the new scorer).

## 8. Acceptance criteria
- [x] A 3h `fatigue=3` lecture ending 15:00 pushes a default learn job to ≥16:00
      (soft) and past the buffer entirely under `post_fatigue_buffer(hard=true)`.
- [x] "avoid math right after math class" via chat creates `subject_gap` and the
      replan visibly moves the math job.
- [x] Same outcome with **no LLM configured** (offline NL parser + scorer).
- [x] `llm_placement_mode='rank'` with a mocked LLM picks a non-earliest shortlisted
      slot; `choice` out of range → deterministic fallback; LLM exception →
      fallback; every created job carries `placement_source/placement_note`.
- [x] `pytest -q` green; engine invariant tests unchanged.
## 9. Addendum — review pass (2026-11-09, same branch)

An independent review of the first cut found the architecture sound but two
headline behaviours broken. Both are fixed; the honest status:

- **Adjacency is a touching chain.** The built-in "right after" rule and
  `interleave` now penalise only candidates directly touching a same-subject
  run (a free slot or a foreign-subject job in between costs nothing) —
  candidate-dependent, so argmin is genuinely steered. Learn placement is
  round-robin least-recently-used-subject, so cross-subject interleaving
  materialises in the plan rather than only in receipts.
- **Built-in 60-minute windows no longer stretch** when a longer user
  constraint window exists (`subject_gap 240` no longer smears "within 60 min"
  penalties 200 minutes past the event).
- **rank mode is bounded:** `choose_placement` uses `max_retries=0, timeout=15`,
  and `planner.replan` caps *both* 16 LLM calls **and** 20 s of wall-clock per
  replan; jobs past the budget get a "LLM budget reached" receipt.
- **Receipts never lie by omission:** relaxation is recorded on the LLM path
  too; the duplicate "relaxed" phrase is gone; pool reuse on re-derived jobs
  refreshes source/note; bool/fractional LLM choices are rejected both layers.
- **Data hygiene:** corrupted constraint rows are skipped instead of crashing
  every replan; ICS annotations are batched in 15-event chunks with per-chunk
  failure isolation; pre-upgrade databases get backfilled at app startup
  (`POST /api/events/annotate {"force": true}` re-annotates on demand); the
  offline NL parser strips leading verbs ("doing math" → "math"), only creates
  rules for subjects present in the user's data, and de-dupes identical rules.
- **Not a defect, by design:** same-day pending jobs are never re-derived
  (you keep your 08:00 slot), so their receipts can stay stale for the day;
  hard constraints don't apply to today's kept placements; `smart_placement=off`
  restores earliest-free-slot *scoring* but stored constraints still hard-filter
  candidates (documented in config comment + P2/P4).

### Addendum — second review pass (2026-11-09)

An independent correctness review of the first commit flagged four trust
defects (S1–S4) plus moderate items. Status:

- **S1 fixed**: `_as_int` no longer truncates JSON floats (`2.9` → error, not `2`).
- **S2 fixed**: startup and ICS import annotate **heuristically only**; LLM
  annotation is explicit (`POST /api/events/annotate`), chunked (15 events), and
  time-boxed (~45 s); it can never stall startup or an HTTP response. User
  annotations (`source='user'`) survive `force` re-annotation.
- **S3 fixed (docs)**: the "no data → identical to pre-branch" claim was false —
  the built-in back-to-back nudge fires without annotations. Docs now say so
  plainly; `smart_placement=off` remains the exact rollback (P2 pins it).
- **S4 fixed**: `interleave` is soft-only end-to-end; `hard:true` → 422 (REST) /
  error receipt (chat tool); the scorer never honours such a row.
- **M1**: `find_placeable` early-exits at the first penalty-0 candidate
  (penalties ≥ 0 → argmin), restoring sub-second replans; full scan remains
  only for chooser-attached replans and all-positive-penalty horizons.
- **M2**: absolute dead ends are reported on the replan receipt (`unplaced`);
  conflicted revisions get a `placement_note`. Docs/AGENTS rule #11 updated to
  promise exactly this.
- **M4**: `blocked_day_part` judges interval overlap (11:30 slot vs 12:00–13:00
  block is blocked), not start-hour alone.
- **M5**: dead code deleted; `_constraint_out` single-sourced; list ordering
  documented correctly.
- **M6**: offline NL parser accepts "never more than N in a row".
- **Engine test-control note**: `find_placeable` for revisions and learn jobs
  shares the chooser budget — revision placements consume the same 16-call/20 s
  budget as learn placements.
- **M3** (synchronous replan inside chat's constraint writes) remains by
  design, bounded by the per-replan budget; noted in DECISIONS, not queued.
