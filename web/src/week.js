/** P2 — Week (time grid) with drag-reschedule, checkboxes, progress bar.
 *  Below 768 px the grid is replaced by a vertical day-list fallback. */

import { Calendar } from "@fullcalendar/core";
import timeGridPlugin from "@fullcalendar/timegrid";
import interactionPlugin from "@fullcalendar/interaction";
import "@fullcalendar/timegrid";
import "../tailwind.css";
import { api, toast, dialog, utcTime, utcDay, setText, formatHours, initSettings, localDateString } from "./common.js";
import { initTour } from "./tour.js";

const calendarEl = document.getElementById("week-calendar");
const rangeEl = document.getElementById("week-range");
const progressWrap = document.getElementById("progress-wrap");
const progressBar = document.getElementById("progress-bar");
const progressText = document.getElementById("progress-text");
const btnToday = document.getElementById("btn-today");
const btnSettings = document.getElementById("btn-settings");
const popover = document.getElementById("job-popover");
const weekEmpty = document.getElementById("week-empty");

const listEl = document.createElement("div");
listEl.id = "week-list";
listEl.className = "hidden h-full overflow-y-auto p-4";
calendarEl.after(listEl);

let calendar;
let currentJobId = null;
// Declared here, before eventAllow / eventDrop reference them.
let draggedJobId = null;
let draggedDuration = 50;
let pinnedJobs = new Set(); // jobs moved by the user this session (pin indicator)
let isMobile = false;
let eventsCache = [];
// job id -> checkbox element, for optimistic ticks across grid + mobile list
const jobCbById = new Map();

const today = new Date();
today.setHours(0, 0, 0, 0);

// Local calendar day (never toISOString: it shifts by the UTC offset and drops
// the first/last day of the visible week).
function isoDate(d) {
  return localDateString(d);
}

// ISO seconds, no milliseconds: YYYY-MM-DDTHH:MM:SSZ
function isoSeconds(d) {
  return d.toISOString().slice(0, 19) + "Z";
}

function addMinutes(d, mins) {
  return new Date(d.getTime() + mins * 60000);
}

function isSameDay(a, b) {
  return a.toDateString() === b.toDateString();
}

function eventKindClass(kind) {
  if (kind === "external") return "border-external bg-external/90 text-slate-900 font-medium cursor-not-allowed";
  return "border-study bg-white text-slate-900 font-medium cursor-move shadow-sm";
}

function renderEvent(arg) {
  const e = arg.event;
  const job = e.extendedProps.job;
  const isExternal = e.extendedProps.kind === "external";
  const done = job && job.status === "done";
  const card = document.createElement("div");
  card.className = `p-2 text-[11px] leading-tight rounded border ${done
    ? "border-done bg-slate-100 text-slate-500 line-through font-medium opacity-70 cursor-default"
    : eventKindClass(e.extendedProps.kind)}`;

  const time = document.createElement("div");
  time.className = "text-[10px] " + (isExternal ? "text-slate-700" : "text-slate-500");
  time.textContent = e.allDay ? "All day" : `${utcTime(e.start)}–${utcTime(e.end)}`;
  card.appendChild(time);

  const title = document.createElement("div");
  title.className = "truncate font-medium mt-0.5";
  title.textContent = e.title;
  card.appendChild(title);

  const meta = document.createElement("div");
  meta.className = "text-[9px] text-slate-400 mt-0.5 truncate";
  meta.textContent = isExternal
    ? `· ${e.extendedProps.venue || ""}`
    : job
    ? `· ${job.duration_min != null ? job.duration_min + "m" : ""} ${job.kind === "revision" ? "R" + job.revision_round : "learn"}`
    : "";
  card.appendChild(meta);

  if (job && job.placement_note) {
    const why = document.createElement("div");
    why.className = "text-[9px] text-indigo-400 mt-0.5 truncate";
    why.textContent = `${job.placement_source === "llm" ? "✨ " : ""}why: ${job.placement_note}`;
    why.title = job.placement_note;
    card.appendChild(why);
  }

  const pinned = pinnedJobs.has(e.id) || (job && job.pinned === 1);
  if (pinned) {
    const pin = document.createElement("span");
    pin.className = "text-[10px] text-yellow-500 ml-1";
    pin.textContent = "📌";
    pin.title = "Pinned — moved manually, kept on re-plan";
    meta.appendChild(pin);
  }

  if (job && !done) {
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "mt-1 shrink-0";
    cb.title = "Mark done";
    jobCbById.set(job.id, cb);
    cb.onclick = (ev) => {
      ev.stopPropagation();
      tickJob(job.id, card);
    };
    card.appendChild(cb);
  }
  return { domNodes: [card] };
}

async function loadEvents() {
  if (!calendar) return;
  const view = calendar.view;
  const from = isoDate(view.currentStart);
  const to = isoDate(view.currentEnd);
  try {
    const events = await api(`events?from=${from}&to=${to}`);
    eventsCache = events;
    calendar.removeAllEvents();
    jobCbById.clear();
    events.forEach((e) => {
      const draggable = e.kind !== "external";
      calendar.addEvent({
        id: e.id,
        title: e.title,
        start: e.dtstart,
        end: e.dtend,
        allDay: e.all_day === 1,
        // External events are fixed: no drag, no resize (AGENTS.md #2). The
        // per-event flags are what FullCalendar honours — a global
        // `eventEditable` option does not exist (it logged a warning).
        startEditable: draggable,
        durationEditable: draggable,
        extendedProps: {
          kind: e.kind,
          venue: e.venue || "",
          job: e.job || null,
        },
      });
      const j = e.job || null;
      if (j && j.pinned === 1) pinnedJobs.add(e.id);
    });
    setText(rangeEl, `${utcDay(view.currentStart)} – ${utcDay(view.currentEnd)}`);
    await updateProgress(events);
    const empty = events.length === 0;
    // The empty state is page-level, not mobile-only: on desktop an empty week
    // used to show a blank grid with no explanation.
    weekEmpty.classList.toggle("hidden", !empty);
    if (isMobile) renderMobileList();
  } catch (err) {
    toast("Failed to load week: " + err.message, "error");
  }
}

/** Week progress bar. Computes planned/done for the visible week from the
 *  loaded events. Optionally augments with GET /api/stats `by_day` entries
 *  whose date falls inside the visible range. Never shows all-future totals
 *  as "this week". */
async function updateProgress(events) {
  const view = calendar.view;
  let plannedMin = 0;
  let doneMin = 0;
  let usedStats = false;
  try {
    const stats = await api("stats");
    const byDay = stats && stats.by_day && Array.isArray(stats.by_day) ? stats.by_day : [];
    if (byDay.length && view) {
      const f = isoDate(view.currentStart);
      const t = isoDate(view.currentEnd);
      byDay.forEach((d) => {
        if (!d || d.date < f || d.date >= t) return;
        plannedMin += Number(d.planned_min || 0);
        doneMin += Number(d.done_min || 0);
      });
      usedStats = plannedMin > 0 || doneMin > 0;
    }
  } catch {
    // stats endpoint unavailable -> fall through to events below
  }
  if (!usedStats) {
    plannedMin = 0;
    doneMin = 0;
    events.forEach((e) => {
      const job = e.job;
      if (!job) return;
      const mins = job.duration_min || 0;
      plannedMin += mins;
      if (job.status === "done") doneMin += mins;
    });
  }
  if (plannedMin === 0) {
    progressWrap.classList.toggle("hidden", true);
    return;
  }
  progressWrap.classList.toggle("hidden", false);
  const pct = Math.round((doneMin / plannedMin) * 100);
  progressBar.style.width = `${pct}%`;
  setText(progressText, `${formatHours(doneMin / 60)} / ${formatHours(plannedMin / 60)}`);
}

async function tickJob(jobId, el) {
  const cb = jobCbById.get(jobId);
  if (cb) {
    cb.disabled = true;
    cb.checked = true;
  }
  if (el) el.classList.add("line-through", "text-slate-400", "opacity-60");
  try {
    await api(`jobs/${jobId}/complete`, { method: "POST" });
    toast("Job marked done", "success");
    loadEvents(); // refresh so new revision jobs appear
  } catch (err) {
    if (cb) {
      cb.checked = false;
      cb.disabled = false;
    }
    if (el) el.classList.remove("line-through", "text-slate-400", "opacity-60");
    toast("Failed: " + err.message, "error");
  }
}

async function moveJob(jobId, startIso, endIso) {
  await api(`jobs/${jobId}/move`, {
    method: "POST",
    body: { slot_start: startIso, slot_end: endIso },
  });
  toast("Job rescheduled", "success");
  loadEvents();
}

async function replanOpenGoals() {
  const goals = await api("goals");
  let moved = 0;
  for (const g of goals) {
    if (g.status === "closed" || g.status === "done" || g.status === "completed") continue;
    const res = await api(`goals/${g.id}/replan`, { method: "POST" });
    moved += res.moved_count ?? res.moved_jobs?.length ?? 0;
  }
  toast(`Re-planned ${moved} jobs`, "success");
  loadEvents();
}

function openPopover(job, anchorRect, fcEvent) {
  closePopover();
  currentJobId = job.id;
  popover.classList.toggle("hidden", false);
  popover.innerHTML = "";

  const close = document.createElement("button");
  close.className = "absolute right-2 top-2 text-slate-400 hover:text-slate-700 text-xs";
  close.textContent = "✕";
  close.onclick = closePopover;
  popover.appendChild(close);

  const title = document.createElement("div");
  title.className = "font-semibold mb-1";
  title.textContent = fcEvent.title || "Job";
  popover.appendChild(title);

  const line = document.createElement("div");
  line.className = "text-xs text-slate-500 mb-2";
  line.textContent = `${utcDay(fcEvent.start)} ${utcTime(fcEvent.start)}–${utcTime(fcEvent.end)} · ${job.duration_min != null ? job.duration_min + "m" : ""}`;
  popover.appendChild(line);

  const kind = document.createElement("div");
  kind.className = "text-xs text-slate-500 mb-3";
  kind.textContent = job.kind === "revision" ? `Revision R${job.revision_round ?? ""}` : "Learn job";
  const pinned = job.pinned === 1;
  if (pinned) {
    kind.textContent += "  📌 pinned";
  }
  popover.appendChild(kind);

  const actions = document.createElement("div");
  actions.className = "flex gap-2 pt-3 border-t border-slate-100";
  const skip = document.createElement("button");
  skip.className = "flex-1 py-1.5 rounded text-sm text-slate-600 border border-slate-200 hover:bg-slate-50";
  skip.textContent = "Mark skipped";
  skip.onclick = async () => {
    try {
      await api(`jobs/${job.id}/skip`, { method: "POST" });
      closePopover();
      loadEvents();
      toast("Job skipped", "success");
    } catch (err) {
      toast("Failed: " + err.message, "error");
    }
  };
  const replan = document.createElement("button");
  replan.className = "flex-1 py-1.5 rounded text-sm text-blue-600 hover:bg-blue-50";
  replan.textContent = "Re-plan goal";
  replan.onclick = async () => {
    try {
      closePopover();
      if (job && job.goal_id) {
        await api(`goals/${job.goal_id}/replan`, { method: "POST" });
        toast("Goal re-planned", "success");
      } else {
        await replanOpenGoals();
      }
      loadEvents();
    } catch (err) {
      toast("Re-plan failed: " + err.message, "error");
    }
  };
  actions.appendChild(skip);
  actions.appendChild(replan);
  popover.appendChild(actions);

  popover.style.left = Math.max(8, anchorRect.left) + "px";
  popover.style.top = Math.min(anchorRect.bottom + 6, window.innerHeight - 260) + "px";}

function closePopover() {
  popover.classList.toggle("hidden", true);
  currentJobId = null;
}

function renderMobileList() {
  listEl.innerHTML = "";
  if (!eventsCache.length) {
    listEl.textContent = "Nothing planned for this week. Create a goal and drop materials in the Inbox.";
    return;
  }
  const byDay = new Map();
  eventsCache.forEach((e) => {
    const key = isoDate(new Date(e.dtstart));
    if (!byDay.has(key)) byDay.set(key, []);
    byDay.get(key).push(e);
  });
  [...byDay.keys()].sort().forEach((dayKey) => {
    const dayEvents = byDay.get(dayKey).sort((a, b) => new Date(a.dtstart) - new Date(b.dtstart));

    const header = document.createElement("div");
    header.className = "mb-2 mt-4 text-xs font-semibold text-slate-500 uppercase tracking-wider";
    header.textContent = utcDay(dayKey);
    listEl.appendChild(header);

    dayEvents.forEach((e) => {
      const job = e.job;
      const isExternal = e.kind === "external";
      const done = job && job.status === "done";

      const row = document.createElement("div");
      row.className = `flex items-start gap-2 p-2 rounded border ${done
        ? "border-done bg-slate-100 text-slate-500 line-through opacity-60"
        : isExternal
        ? "border-external bg-external/10 text-slate-900"
        : "border-study bg-white text-slate-900"}`;

      const time = document.createElement("div");
      time.className = "text-[10px] text-slate-500 shrink-0";
      time.textContent = e.all_day === 1 ? "All day" : `${utcTime(e.dtstart)}–${utcTime(e.dtend)}`;
      row.appendChild(time);

      const info = document.createElement("div");
      info.className = "flex-1 min-w-0";
      const title = document.createElement("div");
      title.className = "text-sm font-medium truncate";
      title.textContent = e.title;
      info.appendChild(title);
      const meta = document.createElement("div");
      meta.className = "text-[9px] text-slate-400 truncate";
      meta.textContent = isExternal
        ? `· ${e.venue || ""}`
        : job
        ? `· ${job.duration_min != null ? job.duration_min + "m" : ""} ${job.kind === "revision" ? "R" + job.revision_round : "learn"}`
        : "";
      info.appendChild(meta);
      if (job && job.placement_note) {
        const why = document.createElement("div");
        why.className = "text-[9px] text-indigo-400 truncate";
        why.textContent = `${job.placement_source === "llm" ? "✨ " : ""}why: ${job.placement_note}`;
        why.title = job.placement_note;
        info.appendChild(why);
      }
      row.appendChild(info);

      if (job && !done) {
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.className = "mt-0.5 shrink-0";
        cb.title = "Mark done";
        jobCbById.set(job.id, cb);
        cb.onclick = (ev) => {
          ev.stopPropagation();
          tickJob(job.id, row);
        };
        row.appendChild(cb);
      }
      listEl.appendChild(row);
    });
  });
}

/** Desktop: time grid. Mobile (< 768 px): vertical day-list, no horizontal scroll. */
function toggleMobileView() {
  const mobile = window.innerWidth < 768;
  if (mobile === isMobile) return;
  isMobile = mobile;
  calendarEl.classList.toggle("hidden", mobile);
  listEl.classList.toggle("hidden", !mobile);
  if (mobile) {
    renderMobileList();
  } else if (calendar) {
    calendar.updateSize();
  }
}

calendar = new Calendar(calendarEl, {
  plugins: [timeGridPlugin, interactionPlugin],
  initialView: "timeGridWeek",
  slotDuration: "00:30:00",
  slotLabelInterval: "01:00",
  slotLabelFormat: { hour: "2-digit", minute: "2-digit", hour12: false },
  slotMinTime: "08:00:00",
  slotMaxTime: "22:00:00",
  height: "auto",
  // Genuine all-day events (VALUE=DATE in the .ics) render in a compact
  // all-day row; engine jobs are never all-day.
  allDaySlot: true,
  headerToolbar: false,
  editable: true,
  // Today column tint (dayHeaderClassNames / dayCellClassNames).
  dayHeaderClassNames: (arg) => (isSameDay(arg.date, today) ? ["bg-blue-50 font-bold text-blue-700"] : []),
  dayCellClassNames: (arg) => (isSameDay(arg.date, today) ? ["bg-blue-50/50"] : []),
  eventDragStart: (info) => {
    if (info.event.extendedProps.kind === "external") return;
    const job = info.event.extendedProps.job;
    draggedJobId = job ? job.id : null;
    draggedDuration = job && job.duration_min ? job.duration_min : 50;
  },
  eventAllow: (dropInfo) => {
    if (!draggedJobId) return true;
    // Never drop a job onto or over an external event.
    const dur = draggedDuration || 50;
    const ds = dropInfo.date;
    const de = new Date(ds.getTime() + dur * 60000);
    const overExternal = calendar.getEvents().some((e) => {
      if (e.extendedProps.kind !== "external") return false;
      const es = new Date(e.start);
      const ee = new Date(e.end);
      return es < de && ds < ee;
    });
    if (overExternal) return false;
    // Reject past dates.
    const startOfToday = new Date();
    startOfToday.setHours(0, 0, 0, 0);
    return ds >= startOfToday;
  },
  events: [],
  eventContent: renderEvent,
  eventClick: (info) => {
    const job = info.event.extendedProps.job;
    if (!job || job.status === "done") {
      closePopover();
      return;
    }
    const rect = info.jsEvent
      ? // The popover is position:fixed, so it needs viewport coordinates
        // (clientX/clientY); pageX/pageY drift once the page is scrolled.
        { left: info.jsEvent.clientX, top: info.jsEvent.clientY, bottom: info.jsEvent.clientY, width: 0 }
      : { left: 100, top: 100, bottom: 120, width: 0 };
    openPopover(job, rect, info.event);
  },
  eventDrop: async (info) => {
    const job = info.event.extendedProps.job;
    if (!job || job.status === "done") return;
    const startIso = isoSeconds(info.event.start);
    const endIso = info.event.end ? isoSeconds(info.event.end) : isoSeconds(addMinutes(info.event.start, draggedDuration || 50));
    try {
      await moveJob(job.id, startIso, endIso);
      pinnedJobs.add(info.event.id);
    } catch (err) {
      info.revert();
      toast("Can't move there: " + err.message, "error");
    }
  },
  datesSet: loadEvents,
});
calendar.render();

window.addEventListener("resize", toggleMobileView);
toggleMobileView();

btnToday.onclick = () => calendar.today();
document.addEventListener("click", (e) => {
  if (!popover.classList.contains("hidden") && !popover.contains(e.target)) closePopover();
});

loadEvents();
initSettings(btnSettings);
initTour("week");
