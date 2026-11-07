# Decision log (append-only)

Format: `date | decision | rationale | status`

| Date | Decision | Rationale | Status |
|---|---|---|---|
| 2026-11-03 | Calendar (ICS) is the single source of truth; every study job is a calendar event | familiar mental model; standard format; easy sync by importing the formal calendar file | confirmed |
| 2026-11-03 | Exactly **three** pages: calendar (month) / week / inbox | "I don't want too much page as difficult to manage" | confirmed |
| 2026-11-03 | LLM never does scheduling; deterministic Python engine owns all allocation math | predictability, low cost, offline mode, testability | confirmed |
| 2026-11-03 | LLM client = `openai` SDK against any OpenAI-compatible endpoint; **no key yet** → offline mode (deterministic fallbacks) is a first-class path, not a stub | user has no key yet but wants external APIs kept open (Cloudflare / Ollama / OpenAI) | confirmed |
| 2026-11-03 | Exam dates: **both** auto (external events tagged `exam`) and manual goal entry | "it is a great idea for setting both" | confirmed |
| 2026-11-03 | Daily capacity & slot length are **dynamic settings**, not constants; ship placeholder defaults (4 h/day, 50 min/job) as "fake time" first; LLM `tune_settings` skill or user override, bounds-validated | "are dynamic… just set a fake time first… more are llm jobs with some skills" | confirmed |
| 2026-11-03 | ICS **import only** for now; export deferred to a future feature (post-M4) | "import only first. export would be very future function" | confirmed |
| 2026-11-03 | `markitdown` (+ `pypdf`, `mammoth`, `python-pptx`) for document → markdown | one library covers the "feed in without caring" requirement; minimal pinned extras | confirmed |
| 2026-11-03 | Stdlib `sqlite3`, no ORM | dependency discipline | confirmed |
| 2026-11-03 | Frontend: vanilla JS + locally vendored FullCalendar + `fetch`; **no build step** | three pages, easy to manage, no toolchain | confirmed |
| 2026-11-03 | Cloud OCR (e.g. Cloudflare Workers AI) deferred to M4 for scanned PDFs; no local OCR (tesseract) in base image | keep base image light; cloud API for conversions is acceptable to user | confirmed |
| 2026-11-03 | Docker (`python:3.12-slim`) **and** host-Python dev path both supported | Docker installed (daemon currently stopped); host Python 3.14 available | confirmed |
| 2026-11-03 | Docs-first: full document set (ARCHITECTURE / DATA_MODEL / API / STYLE / DECISIONS + AGENTS.md) completed before M0 code | "prepare all the document needed before starting" | confirmed |
| 2026-11-03 | Single-user local app by design; multi-user, sync servers, auth all out of scope | keep it lightweight | confirmed |
| 2026-11-03 | Frontend stack upgraded: **Vite + Tailwind CSS + FullCalendar v7** (vanilla JS, no framework) | research (DeepWiki + web, 2026-11-03): FullCalendar is the most-used calendar component on the web (~2.4M weekly npm downloads, 19k+ stars); checkboxes-in-event-cards, drag-reschedule with drop veto, and Tailwind theming are all in the **free MIT core**; Tailwind is the explicitly blessed styling path ("Your design system") | confirmed |
| 2026-11-03 | Vite is the only build step in the repo; built `web/dist/` served by FastAPI; dev = `npm run dev` (proxy `/api` → 8000) | user wanted a proper modern stack ("would it be too few? not even tailwind?") | confirmed |
| 2026-11-03 | Rejected: react-big-calendar, Mantine Schedule, shadcn Calendar | research: rbc is React-only, slow on fixes, needs manual restyling; Mantine Schedule requires Mantine; shadcn Calendar is a date picker, not an event calendar | confirmed |
| 2026-11-07 | **M5 = final delivery stage** (define explicitly; the milestone table originally stopped at M4) | the remaining work is integration/acceptance, not a new feature area; target is a working end-to-end demo + production build + docs close-out | confirmed |
| 2026-11-07 | JSON request bodies are the documented API contract; endpoints also accept form-encoded bodies for backward compatibility | `docs/API.md` already specified JSON, but the web client and older tests sent forms; accepting both removes the mismatch without breaking clients | confirmed |
| 2026-11-07 | Cloudflare Workers AI is the optional OCR provider (off by default), using `pypdf` embedded-page images; no new dependency | honours the existing M4 decision and keeps the base image light; scanned PDFs are best-effort and never block ingest | confirmed |
| 2026-11-07 | A manually dragged job is `pinned` and survives replan; only unpinned pending/conflicted jobs are re-derived | "drag a job = instant reschedule, remaining jobs reflow" must not be undone by the next recompute | confirmed |
