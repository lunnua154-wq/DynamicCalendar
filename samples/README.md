# Samples — shared fixtures

These files are the **concrete fake objects** the project is built and tested against:
unit tests, the demo seed, and the walkthrough in `docs/WALKTHROUGH.md` all use them.

| File | Feeds | Expected outcome |
|---|---|---|
| `sample_calendar.ics` | ICS import tests, exam detection | 10 events imported (the weekly RRULE event is imported at its DTSTART instance — expansion is post-M4); exactly 1 event tagged `EXAM` (MATH2001 Final, 18 Dec); 1 all-day event (books due) |
| `materials/math_chapter3_limits.md` | ingest + clustering | topic "Limits & Continuity" (LLM mode) / own topic (offline) |
| `materials/math_chapter3_limits_v2.md` | **version detection** | LLM mode: "version update of" the v1 file → v1 marked `superseded`. Offline mode: kept as a separate topic (hashes differ) |
| `materials/problem_set_3.md` | ingest + effort estimate | topic "Problem Set 3"; effort estimate > notes (it's exercises) |
| `materials/cs_lecture08_graphs.md` | cross-topic clustering | new topic "Graphs"; **unattached** to any goal (no CS goal exists) — exercises the "unattached material" state |

## Rules
- UIDs in the `.ics` are **stable** — tests reference them. Never renumber.
- Event times are `Asia/Singapore` (UTC+8); the UTC-conversion tests depend on it.
- The two `v1`/`v2` files are intentionally similar-but-different (hashes must differ).
- Changing a fixture is an **intentional act**: update this table in the same commit.
