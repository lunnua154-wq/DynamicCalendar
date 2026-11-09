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
  Heuristic: keyword map over the title. LLM: one **batched** call for all
  un-annotated imported externals (ieft imported by `.ics`), per-item validation.
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
capped at `MAX_LLM_PLACEMENT_CALLS` (16) per replan; revisions and overflow use
the deterministic scorer. `temperature=0`.

Why a shortlist instead of letting the LLM emit arbitrary datetimes?
Verification is then **membership in a hard-valid set** — sound by construction,
trivial to test, impossible for the LLM to "cheat" into an invalid slot.

Creative extensions (Phase 3+): full-assignment `choose` mode with global
verifier; per-replan batched placement in a single prompt.

## 4. What gets implemented on this branch (Phase 1 + 2)

1. **Smart placement (deterministic, default ON)** — `smart_placement=on`.
   Candidates are scored with built-in penalties; the planner places at
   `argmin(penalty, slot_start)` instead of *earliest*. With no annotations and
   no constraints, all penalties are 0 and behaviour is **identical** to before
   (all existing tests must stay green).
2. **Annotations** — heuristic on import + `POST /api/events/annotate` (LLM batch
   when configured, heuristic otherwise). Table + `db` helpers.
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
- **Cost:** ranking adds ≤16 short completions per replan; batched annotation
  adds 1 call per import. Bounded by design.
- **Subject inference is heuristic** (tags/keywords) until the LLM normalises;
  misclassification degrades placement quality, never validity.
- **Scan cost**: candidate search is O(days × slots × (externals+jobs)) over the
  deadline horizon — trivial for a single-user local app (≤ ~4k slots).

## 7. Migration & rollback

- Schema: two new tables + two `jobs` columns via light `ALTER TABLE` migrations
  (same pattern as `pinned`). No backfill required; `placement_note` NULL on
  legacy jobs.
- Rollback: `git revert` the branch; dropped tables/columns are inert.
- Existing databases keep working; first replan after upgrade re-derives future
  unpinned jobs as usual (against the new scorer).

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

## 8. Acceptance criteria
- [ ] A 3h `fatigue=3` lecture ending 15:00 pushes a default learn job to ≥16:00
      (soft) and past the buffer entirely under `post_fatigue_buffer(hard=true)`.
- [ ] "avoid math right after math class" via chat creates `subject_gap` and the
      replan visibly moves the math job.
- [ ] Same outcome with **no LLM configured** (offline NL parser + scorer).
- [ ] `llm_placement_mode='rank'` with a mocked LLM picks a non-earliest shortlisted
      slot; `choice` out of range → deterministic fallback; LLM exception →
      fallback; every created job carries `placement_source/placement_note`.
- [ ] `pytest -q` green; engine invariant tests unchanged.
