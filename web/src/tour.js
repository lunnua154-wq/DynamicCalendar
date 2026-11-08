/** First-run guided tour: five steps spread over the three pages.
 *
 *  Design notes (why it looks like this):
 *  - No fourth page: AGENTS.md rule 5 keeps the app at three HTML pages, so the
 *    tour is an overlay that spotlights the real controls (docs/DECISIONS.md).
 *  - The state lives in localStorage, so a step survives the page navigation a
 *    step needs (calendar -> inbox -> week). No backend involvement.
 *  - Every step explains *why* the app behaves that way, not only where to
 *    click: the calendar is the source of truth, the planner is deterministic,
 *    the LLM only labels.
 *  - The spotlight degrades gracefully: when the target is missing (for example
 *    an .ics with no exam event, so no exam banner) the step still renders as a
 *    plain card instead of pointing at nothing.
 */

import { toast } from "./common.js";

const STEPS = [
  {
    page: "calendar",
    target: "#btn-import",
    title: "Your calendar is the source of truth",
    body:
      "Import the .ics once. Classes, labs and exams land on the grid, and " +
      "nothing in this app is allowed to move or delete them. Every study " +
      "block is planned around what is already there.",
    hint: "Try: Import .ics",
  },
  {
    page: "calendar",
    target: "#exam-banner",
    title: "A deadline becomes a goal",
    body:
      "An exam found in the calendar shows up here. One click opens a goal " +
      "whose deadline is the exam itself, not today — and that date is what " +
      "the scheduler works backwards from.",
    hint: "No exam in your .ics? Skip this one; the rest still applies.",
  },
  {
    page: "inbox",
    target: "#drop-zone",
    title: "Dump your materials in",
    body:
      "Drop PDFs, slides, problem sets — anything. Each file is converted to " +
      "text, tagged and de-duplicated, then turned into study hours. The " +
      "assistant only labels it; it never decides when you sit down.",
    hint: "Works with no API key: offline, the filename becomes the topic.",
  },
  {
    page: "week",
    target: "#week-calendar",
    title: "The plan fills the gaps by itself",
    body:
      "Deterministic Python packs study blocks into the free time between " +
      "your real events, never on top of one. Same input, same plan — no " +
      "model, no randomness.",
    hint: "The week page is the plan; the calendar page is the truth it obeys.",
  },
  {
    page: "week",
    target: "#week-calendar",
    title: "Tick it, and the plan reflows",
    body:
      "Ticking a block is one atomic transaction: it fades to done, four " +
      "revisions appear (+1/+3/+7/+14 days), and the rest reflows around " +
      "them. Finished work never moves again — drag any block to pin it.",
    hint: "Dropping a block onto a real event is refused, not silently accepted.",
  },
];

const PAGE_URL = { calendar: "/", week: "/week", inbox: "/inbox" };
const KEY_DONE = "dynacalendar.tour.done";
const KEY_STEP = "dynacalendar.tour.step";

let ui = null; // { blocker, hole, card } while a step is on screen
let onResize = null;

/* --------------------------------------------------------------- storage */

function safeGet(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key, value) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* private mode: the tour still works for this page view */
  }
}

function isDone() {
  return safeGet(KEY_DONE) === "1";
}

function readStep() {
  const raw = safeGet(KEY_STEP);
  if (raw === null) return null;
  const n = Number(raw);
  return Number.isInteger(n) && n >= 0 && n < STEPS.length ? n : null;
}

/* ------------------------------------------------------------------- dom */

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function teardown() {
  if (onResize) {
    window.removeEventListener("resize", onResize);
    window.removeEventListener("scroll", onResize, true);
    onResize = null;
  }
  if (ui) {
    ui.blocker.remove();
    ui.hole.remove();
    ui.card.remove();
    ui = null;
  }
  document.removeEventListener("keydown", onKey);
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function placeCard(card, rect) {
  const pad = 16;
  const gap = 14;
  const width = card.offsetWidth;
  const height = card.offsetHeight;
  let top;
  let left;

  if (rect) {
    const below = rect.bottom + gap;
    const above = rect.top - gap - height;
    top = below + height + pad <= window.innerHeight ? below : Math.max(pad, above);
    left = rect.left + rect.width / 2 - width / 2;
  } else {
    top = (window.innerHeight - height) / 2;
    left = (window.innerWidth - width) / 2;
  }

  card.style.top = Math.round(clamp(top, pad, window.innerHeight - height - pad)) + "px";
  card.style.left = Math.round(clamp(left, pad, window.innerWidth - width - pad)) + "px";
}

/** Rect of the spotlight target, or null when it is missing / not rendered. */
function targetRect(selector) {
  if (!selector) return null;
  const node = document.querySelector(selector);
  if (!node || !node.getClientRects().length) return null;
  const rect = node.getBoundingClientRect();
  if (rect.width < 2 || rect.height < 2) return null;
  return rect;
}

/* ---------------------------------------------------------------- render */

function render(index) {
  teardown();
  const step = STEPS[index];
  const total = STEPS.length;

  const target = document.querySelector(step.target);
  if (target && target.scrollIntoView) target.scrollIntoView({ block: "center" });

  requestAnimationFrame(() => {
    const rect = targetRect(step.target);

    // Click-catcher: blocks the page underneath so the card owns the focus.
    const blocker = el("div", "tour-blocker");
    if (!rect) blocker.classList.add("tour-blocker--solid");

    // Spotlight: a transparent box around the target, with an enormous shadow
    // that dims everything else — the standard "cut a hole" trick.
    const hole = el("div", "tour-hole");
    hole.hidden = !rect;

    const card = el("div", "tour-card");
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-label", step.title);

    const meta = el("div", "tour-meta", `Step ${index + 1} of ${total}`);
    const title = el("h3", "tour-title", step.title);
    const body = el("p", "tour-body", step.body);
    const hint = el(
      "p",
      "tour-hint",
      step.hint || `Why: ${step.title.toLowerCase()}.`
    );
    card.append(meta, title, body, hint);

    const row = el("div", "tour-actions");
    const skip = el("button", "tour-btn tour-btn--ghost", "Skip tour");
    const back = el("button", "tour-btn tour-btn--ghost", "Back");
    back.disabled = index === 0;
    const next = el(
      "button",
      "tour-btn tour-btn--primary",
      index === total - 1 ? "Finish" : "Next"
    );
    row.append(skip, back, next);
    card.append(row);

    skip.onclick = () => finish("Tour skipped — press ? to replay it.");
    back.onclick = () => {
      if (index === 0) return;
      const previous = index - 1;
      safeSet(KEY_STEP, String(previous));
      if (STEPS[previous].page !== step.page) {
        window.location.href = PAGE_URL[STEPS[previous].page] + "?tour=1";
      } else {
        render(previous);
      }
    };
    next.onclick = () => {
      if (index === total - 1) {
        finish("Tour finished — press ? any time to replay it.");
        return;
      }
      const following = index + 1;
      safeSet(KEY_STEP, String(following));
      if (STEPS[following].page !== step.page) {
        window.location.href = PAGE_URL[STEPS[following].page] + "?tour=1";
      } else {
        render(following);
      }
    };

    document.body.append(blocker, hole, card);
    ui = { blocker, hole, card };

    const place = () => {
      const current = targetRect(step.target);
      if (!current) {
        hole.hidden = true;
        placeCard(card, null);
        return;
      }
      hole.hidden = false;
      const pad = 6;
      hole.style.top = Math.round(current.top - pad) + "px";
      hole.style.left = Math.round(current.left - pad) + "px";
      hole.style.width = Math.round(current.width + pad * 2) + "px";
      hole.style.height = Math.round(current.height + pad * 2) + "px";
      placeCard(card, current);
    };
    place();

    onResize = () => place();
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onResize, true);
    document.addEventListener("keydown", onKey);
  });
}

function onKey(event) {
  if (event.key === "Escape") {
    event.preventDefault();
    finish("Tour skipped — press ? to replay it.");
  }
}

function finish(message) {
  safeSet(KEY_DONE, "1");
  safeSet(KEY_STEP, null);
  teardown();
  if (message) toast(message, "info");
}

/* ----------------------------------------------------------------- entry */

/** Start the tour from the beginning (the "?" button). */
export function startTour() {
  safeSet(KEY_DONE, null);
  safeSet(KEY_STEP, "0");
  if (window.location.pathname !== "/") {
    window.location.href = "/?tour=1";
    return;
  }
  render(0);
}

/**
 * Mount the tour for one page.
 * `page` is "calendar" | "week" | "inbox".
 */
export function initTour(page) {
  const asked = new URLSearchParams(window.location.search).get("tour") === "1";
  const button = document.getElementById("btn-tour");
  if (button) button.onclick = () => startTour();

  if (isDone() && !asked) return;

  let index = readStep();
  if (index === null) {
    // A first visit only auto-starts on the calendar page: landing deep on
    // /week or /inbox should not hijack the screen.
    if (isDone() || page !== "calendar") return;
    index = 0;
    safeSet(KEY_STEP, "0");
  }
  // Steps that live on another page wait for that page (the Next button
  // navigates and the stored index picks the tour back up there).
  if (STEPS[index].page !== page) return;
  render(index);
}

/** Exported for tests / console: forget that the tour ever ran. */
export function resetTour() {
  safeSet(KEY_DONE, null);
  safeSet(KEY_STEP, null);
}
