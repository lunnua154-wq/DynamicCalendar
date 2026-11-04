/** P2 — Week (time grid) with drag-reschedule, checkboxes, progress bar. */

import { Calendar } from "@fullcalendar/core";
import timeGridPlugin from "@fullcalendar/timegrid";
import interactionPlugin from "@fullcalendar/interaction";
import "@fullcalendar/timegrid";
import "../tailwind.css";
import { api, toast, dialog, utcTime, utcDay, setText } from "./common.js";

const calendarEl = document.getElementById("week-calendar");
const rangeEl = document.getElementById("week-range");
const progressWrap = document.getElementById("progress-wrap");
const progressBar = document.getElementById("progress-bar");
const progressText = document.getElementById("progress-text");
const btnToday = document.getElementById("btn-today");
const popover = document.getElementById("job-popover");
const weekEmpty = document.getElementById("week-empty");

let calendar;
let currentJobId = null;

function isoDate(d) {
  return d.toISOString().slice(0, 10);
}

function renderEvent(arg) {
  const e = arg.event;
  const job = e.extendedProps.job;
  const isExternal = e.extendedProps.kind === "external";
  const card = document.createElement("div");
  card.className = `p-2 text-[11px] leading-tight rounded border ${
    isExternal
      ? "border-external bg-external/90 text-slate-900 font-medium cursor-not-allowed"
      : job && job.status === "done"
      ? "border-done bg-slate-100 text-slate-500 line-through font-medium opacity-70 cursor-default"
      : "border-study bg-white text-slate-900 font-medium cursor-move shadow-sm"
  }`;

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
    ? `· ${job.duration_min}m ${job.kind === "revision" ? "R" + job.revision_round : ""}`
    : "";
  card.appendChild(meta);

  if (job && job.status !== "done") {
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "mt-1 shrink-0";
    cb.title = "Mark done";
    cb.onclick = async (ev) => {
      ev.stopPropagation();
      await tickJob(job.id);
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
    calendar.removeAllEvents();
    events.forEach((e) => {
      calendar.addEvent({
        id: e.id,
        title: e.title,
        start: e.dtstart,
        end: e.dtend,
        allDay: e.all_day === 1,
        extendedProps: {
          kind: e.kind,
          venue: e.venue || "",
          job: e.job || null,
        },
      });
    });
    setText(rangeEl, `${utcDay(view.currentStart)} – ${utcDay(view.currentEnd)}`);
    updateProgress(events);
    weekEmpty.hidden = events.length > 0;
  } catch (err) {
    toast("Failed to load week: " + err.message, "error");
  }
}

function updateProgress(events) {
  let plannedMin = 0;
  let doneMin = 0;
  events.forEach((e) => {
    const job = e.extendedProps.job;
    if (!job) return;
    const mins = job.duration_min || 0;
    plannedMin += mins;
    if (job.status === "done") doneMin += mins;
  });
  if (plannedMin === 0) {
    progressWrap.hidden = true;
    return;
  }
  progressWrap.hidden = false;
  const pct = Math.round((doneMin / plannedMin) * 100);
  progressBar.style.width = `${pct}%`;
  setText(progressText, `${(doneMin / 60).toFixed(1)}h / ${(plannedMin / 60).toFixed(1)}h`);
}

async function tickJob(jobId) {
  try {
    await api(`jobs/${jobId}/complete`, { method: "POST" });
    closePopover();
    loadEvents();
    toast("Job marked done", "success");
  } catch (err) {
    toast("Failed: " + err.message, "error");
  }
}

async function moveJob(jobId, startIso, endIso) {
  try {
    await api(`jobs/${jobId}/move`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: `slot_start=${encodeURIComponent(startIso)}&slot_end=${encodeURIComponent(endIso)}`,
    });
    toast("Job rescheduled", "success");
    loadEvents();
  } catch (err) {
    toast("Can't move there: " + err.message, "error");
  }
}

function openPopover(job, anchorRect) {
  closePopover();
  currentJobId = job.id;
  popover.hidden = false;
  popover.innerHTML = "";
  const close = document.createElement("button");
  close.className = "absolute right-2 top-2 text-slate-400 hover:text-slate-700 text-xs";
  close.textContent = "✕";
  close.onclick = closePopover;
  popover.appendChild(close);

  const title = document.createElement("div");
  title.className = "font-semibold mb-1";
  title.textContent = job.material_title || "Job";
  popover.appendChild(title);

  const line = document.createElement("div");
  line.className = "text-xs text-slate-500 mb-3";
  line.textContent = `${utcDay(job.slot_start)} ${utcTime(job.slot_start)}–${utcTime(job.slot_end)} · ${job.duration_min}m`;
  popover.appendChild(line);

  const kind = document.createElement("div");
  kind.className = "text-xs text-slate-500 mb-3";
  kind.textContent = job.kind === "revision" ? `Revision R${job.revision_round}` : "Learn job";
  popover.appendChild(kind);

  const actions = document.createElement("div");
  actions.className = "flex gap-2 pt-3 border-t border-slate-100";
  const skip = document.createElement("button");
  skip.className = "flex-1 py-1.5 rounded text-sm text-slate-600 border border-slate-200 hover:bg-slate-50";
  skip.textContent = "Mark skipped";
  skip.onclick = async () => {
    try {
      await api(`jobs/${jobId}/skip`, { method: "POST" });
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
      await api(`goals/${job.goal_id}/replan`, { method: "POST" });
      closePopover();
      loadEvents();
      toast("Goal re-planned", "success");
    } catch (err) {
      toast("Re-plan failed: " + err.message, "error");
    }
  };
  actions.appendChild(skip);
  actions.appendChild(replan);
  popover.appendChild(actions);

  // position below anchor
  popover.style.left = Math.max(8, anchorRect.left) + "px";
  popover.style.top = Math.min(anchorRect.bottom + 6, window.innerHeight - 260) + "px";
}

function closePopover() {
  popover.hidden = true;
  currentJobId = null;
}

calendar = new Calendar(calendarEl, {
  plugins: [timeGridPlugin, interactionPlugin],
  initialView: "timeGridWeek",
  slotDuration: "00:30:00",
  slotMinTime: "08:00:00",
  slotMaxTime: "22:00:00",
  height: "auto",
  allDaySlot: false,
  headerToolbar: false,
  editable: true,
  eventEditable: (info) => info.event.extendedProps.kind !== "external",
  eventDragStart: (info) => {
    if (info.event.extendedProps.kind === "external") return false;
  },
  eventAllow: (dropInfo) => {
    // veto dragging a job onto an external event
    if (draggedJobId) {
      const overlapping = calendar.getEvents().some((e) => {
        if (e.extendedProps.kind !== "external") return false;
        const es = new Date(e.start),
          ee = new Date(e.end);
        const ds = new Date(dropInfo.date),
          de = new Date(dropInfo.date);
        de.setMinutes(de.getMinutes() + (draggedDuration || 50));
        return ds < ee && es < de;
      });
      return !overlapping;
    }
    return true;
  },
  events: [],
  eventContent: renderEvent,
  eventClick: (info) => {
    const job = info.event.extendedProps.job;
    if (!job) return;
    if (job.status === "done") {
      closePopover();
      return;
    }
    const rect = info.jsEvent
      ? { left: info.jsEvent.pageX, top: info.jsEvent.pageY, bottom: info.jsEvent.pageY, width: 0 }
      : { left: 100, top: 100, bottom: 120, width: 0 };
    openPopover(job, rect);
  },
  eventDrop: async (info) => {
    const job = info.event.extendedProps.job;
    if (!job || job.status === "done") return;
    const startIso = info.event.start.toISOString();
    const endIso = info.event.end ? info.event.end.toISOString() : null;
    try {
      await api(`jobs/${job.id}/move`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: `slot_start=${encodeURIComponent(startIso)}&slot_end=${encodeURIComponent(endIso || startIso)}`,
      });
      toast("Job rescheduled", "success");
      loadEvents();
    } catch (err) {
      info.revert();
      toast("Can't move there: " + err.message, "error");
    }
  },
  datesSet: loadEvents,
});
calendar.render();

let draggedJobId = null;
let draggedDuration = 50;
calendar.on("eventDragStart", (info) => {
  const job = info.event.extendedProps.job;
  draggedJobId = job ? job.id : null;
  draggedDuration = job ? job.duration_min : 50;
});
calendar.on("eventDragStop", () => {
  draggedJobId = null;
  draggedDuration = 50;
});

btnToday.onclick = () => calendar.today();
document.addEventListener("click", (e) => {
  if (!popover.hidden && !popover.contains(e.target)) closePopover();
});

loadEvents();
