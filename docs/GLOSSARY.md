# Glossary

The single vocabulary for this project. Use these terms in code, docs, commits, and chat.

| Term | Meaning |
|---|---|
| **External event** | A calendar event imported from a `.ics` file (lecture, lab, exam, personal). Read-only; exact time + venue; the engine may only avoid it. |
| **Goal** | A study goal (e.g. "pass MATH2001") with a deadline. The unit the engine optimizes. |
| **Exam event** | An external event tagged `EXAM`; auto-surfaced as a goal-deadline candidate. |
| **Material** | One fed-in file, converted to markdown. Has tags, a summary, and an effort estimate (hours). |
| **Topic** | A cluster of related materials (e.g. "Limits & Continuity"). LLM-suggested, user-mergeable. |
| **Study job** | A unit of allocated work over one or more material parts. Rendered as a calendar event with a checkbox. |
| **Revision job** | A study job of kind `revision`, scheduled after a learn job completes, at the spaced intervals (default +1d/+3d/+7d/+14d). |
| **Revision round** | The nth revision of a completed job (R1, R2, R3, R4 by default). |
| **Slot** | A schedulable chunk of time (`slot_minutes`, default 50 min) inside a day's capacity that is not occupied by an external event. |
| **Capacity** | `daily_capacity_hours` — how much study time the engine may use per day. A dynamic setting (placeholder 4 h). |
| **Recompute / Re-plan** | The engine re-deriving all *pending future* job slots from current state. Deterministic, idempotent. |
| **Pull-forward** | When remaining work finishes ahead of the deadline, the next material is scheduled immediately so the plan stays current. |
| **Re-import** | Importing a `.ics` again; updates external events by `UID` (the only way external events change). |
| **Offline mode** | Operation with no LLM endpoint configured; every LLM skill takes its deterministic fallback. |
| **LLM skill** | One named LLM capability: `tag`, `cluster`, `chat`, `tune_settings` (contracts in AGENTS.md). |
| **Superseded** | A material status: an older version replaced by a newer one (v1 when v2 arrives). |
| **Pinned job** | A pending job the user dragged by hand (`jobs.pinned = 1`); replans keep its slot instead of re-deriving it. |
| **Processing** | The transient material status while markitdown converts and the tagger/clusterer runs; the Inbox polls until it settles. |
| **Conflicted** | A revision round with no free slot before the horizon; surfaced in the UI rather than silently dropped. |
| **Unattached material** | A material not linked to any goal/topic yet; still visible in the inbox. |
| **Atomic tick** | The single transaction that marks a job done, schedules its revisions, and recomputes remaining work. |
