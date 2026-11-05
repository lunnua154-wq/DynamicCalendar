/** P2 — Week (time grid): checkboxes, drag-reschedule, progress bar. */

import { Calendar } from "@fullcalendar/core";
import timeGridPlugin from "@fullcalendar/timegrid";
import interactionPlugin from "@fullcalendar/interaction";
import "../tailwind.css";
import {
  api,
  el,
  fmtDay,
  fmtTime,
  fmtHours,
  hide,
  initNav,
  localDayString,
  show,
  toast,
  toggle,
} from "./common.js";

const rangeEl = document.getElementById("week-range");
const progressWrap = document.getElementById("progress-wrap");
const progressBar = document.getElementById("progress-bar");
const progressText = document.getElementById("progress-text");
const popover = document.getElementById("job-popover");
const weekEmpty = document.getElementById("week-empty");

let calendar;
let events = [];
let draggedJob = null;

function cardFor(event) {
  const job = event.extendedProps.job;
  const isExternal = event.extendedProps.kind === "external";
  const isDone = job && job.status === "done";
  const isRevision = event.extendedProps.kind === "revision";
  const conflicted = job && job.status === "conflicted";

  const card = el(
    "div",
    `h-full w-full px-1.5 py-1 rounded-md border text-[10px] leading-tight shadow-sm overflow-hidden flex items-start gap-1.5 ${
      isExternal
        ? "bg-sky-100 border-sky-400 text-sky-900 cursor-not-allowed"
        : isDone
        ? "bg-slate-100 border-slate-300 text-slate-500 opacity-70"
        : conflicted
        ? "bg-rose-50 border-rose-400 text-rose-900 cursor-grab"
        : isRevision
        ? "bg-violet-50 border-violet-400 border-dashed text-violet-900 cursor-grab"
        : "bg-white border-amber-400 text-amber-900 cursor-grab"
    }`
  );

  // The checkbox is the core progress signal of this page (UX_SPEC P2).
  if (job && !isDone && !conflicted) {
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "mt-0.5 h-3.5 w-3.5 shrink-0 cursor-pointer accent-amber-600";
    checkbox.setAttribute("aria-label", `Mark ${event.title} done`);
    checkbox.onclick = (clickEvent) => clickEvent.stopPropagation();
    checkbox.onchange = async () => {
      checkbox.disabled = true;
      try {
        await api(`jobs/${job.id}/complete`, { method: "POST" });
        toast("Job done — revisions scheduled", "success");
        await loadEvents();
      } catch (error) {
        checkbox.checked = false;
        checkbox.disabled = false;
        toast(error.message, "error");
      }
    };
    card.appendChild(checkbox);
  } else if (isDone) {
    card.appendChild(el("span", "mt-0.5 shrink-0 font-bold text-emerald-600", "✓"));
  }

  const body = el("div", "min-w-0 flex-1");
  body.appendChild(
    el("div", "font-medium opacity-90", event.allDay ? "All day" : `${fmtTime(event.start)}–${fmtTime(event.end)}`)
  );
  const title = el("div", "font-semibold truncate", event.title);
  if (isDone) title.classList.add("line-through");
  body.appendChild(title);

  const meta = [];
  if (isExternal) {
    if (event.extendedProps.venue) meta.push(event.extendedProps.venue);
    meta.push("fixed");
  } else if (job) {
    if (job.topic_name) meta.push(job.topic_name);
    meta.push(`${job.duration_min} min`);
    if (conflicted) meta.push("no free slot");
  }
  if (meta.length) body.appendChild(el("div", "truncate opacity-80", meta.join(" · ")));
  card.appendChild(body);
  return card;
}

async function loadEvents() {
  if (!calendar) return;
  const view = calendar.view;
  const from = localDayString(view.currentStart);
  const to = localDayString(new Date(view.currentEnd.getTime() - 86400000));
  try {
    events = await api(`events?from=${from}&to=${to}`);
    calendar.removeAllEvents();
    events.forEach((event) => {
      const draggable = event.kind !== "external";
      calendar.addEvent({
        id: event.id,
        title: event.title,
        start: event.dtstart,
        end: event.dtend,
        allDay: event.all_day === 1,
        // External events are fixed: no drag, no resize (AGENTS.md #2).
        startEditable: draggable,
        durationEditable: draggable,
        extendedProps: {
          kind: event.kind,
          venue: event.venue || "",
          job: event.job || null,
        },
      });
    });
    rangeEl.textContent = `${fmtDay(view.currentStart)} – ${fmtDay(
      new Date(view.currentEnd.getTime() - 86400000)
    )}`;
    updateProgress();
    toggle(weekEmpty, events.length === 0);
    weekEmpty.classList.toggle("flex", events.length === 0);
  } catch (error) {
    toast(`Failed to load week: ${error.message}`, "error");
  }
}

function updateProgress() {
  let planned = 0;
  let done = 0;
  events.forEach((event) => {
    const job = event.job;
    if (!job) return;
    const minutes = job.duration_min || 0;
    planned += minutes;
    if (job.status === "done") done += minutes;
  });
  if (planned === 0) {
    hide(progressWrap);
    progressWrap.classList.remove("flex");
    return;
  }
  show(progressWrap);
  progressWrap.classList.add("flex");
  progressBar.style.width = `${Math.round((done / planned) * 100)}%`;
  progressText.textContent = `${fmtHours(done / 60)} / ${fmtHours(planned / 60)}`;
}

function closePopover() {
  hide(popover);
}

function openPopover(event, anchor) {
  const job = event.extendedProps.job;
  if (!job) return;
  popover.innerHTML = "";
  popover.appendChild(
    el("div", "font-semibold mb-1 pr-6 truncate", event.title || "Study job")
  );
  popover.appendChild(
    el(
      "div",
      "text-xs text-slate-500",
      `${fmtDay(event.start)} ${fmtTime(event.start)}–${fmtTime(event.end)} · ${job.duration_min} min`
    )
  );
  popover.appendChild(
    el(
      "div",
      "text-xs text-slate-500 mb-2",
      job.kind === "revision" ? `Revision R${job.revision_round}` : "Learn job"
    )
  );
  if (job.topic_name) popover.appendChild(el("div", "text-xs text-slate-500", job.topic_name));
  if (job.status === "conflicted") {
    popover.appendChild(
      el("div", "text-xs text-rose-600 font-medium mt-1", "No free slot — re-plan or reschedule")
    );
  }

  const actions = el("div", "flex flex-col gap-1 mt-3 pt-3 border-t border-slate-100");
  const openMaterial = el(
    "button",
    "text-left text-xs px-2 py-1.5 rounded hover:bg-slate-50 text-slate-700",
    "Open material"
  );
  openMaterial.onclick = () => {
    window.location.href = `/inbox?material=${encodeURIComponent(job.material_id)}`;
  };
  const skip = el(
    "button",
    "text-left text-xs px-2 py-1.5 rounded hover:bg-slate-50 text-slate-700",
    "Mark skipped"
  );
  skip.onclick = async () => {
    try {
      await api(`jobs/${job.id}/skip`, { method: "POST" });
      closePopover();
      toast("Job skipped", "success");
      await loadEvents();
    } catch (error) {
      toast(error.message, "error");
    }
  };
  const replan = el(
    "button",
    "text-left text-xs px-2 py-1.5 rounded hover:bg-slate-50 text-blue-700",
    "Re-plan goal"
  );
  replan.onclick = async () => {
    try {
      await api(`goals/${job.goal_id}/replan`, { method: "POST" });
      closePopover();
      toast("Goal re-planned", "success");
      await loadEvents();
    } catch (error) {
      toast(error.message, "error");
    }
  };
  actions.append(openMaterial, skip, replan);
  popover.appendChild(actions);

  const rect = anchor.getBoundingClientRect();
  show(popover);
  const width = 288;
  popover.style.left = `${Math.min(Math.max(8, rect.left), window.innerWidth - width - 12)}px`;
  popover.style.top = `${Math.min(rect.bottom + 6, window.innerHeight - 240)}px`;
}

function overlapsExternal(start, end, ignoreEventId) {
  return calendar.getEvents().some((event) => {
    if (event.extendedProps.kind !== "external") return false;
    if (event.id === ignoreEventId) return false;
    const eventStart = event.start ? event.start.getTime() : 0;
    const eventEnd = event.end ? event.end.getTime() : eventStart;
    return start < eventEnd && eventStart < end;
  });
}

calendar = new Calendar(document.getElementById("week-calendar"), {
  plugins: [timeGridPlugin, interactionPlugin],
  initialView: "timeGridWeek",
  headerToolbar: false,
  height: "100%",
  allDaySlot: false,
  slotDuration: "00:30:00",
  slotLabelInterval: "01:00",
  slotLabelFormat: { hour: "2-digit", minute: "2-digit", hour12: false },
  slotMinTime: "07:00:00",
  slotMaxTime: "23:00:00",
  scrollTime: "08:00:00",
  nowIndicator: true,
  editable: true,
  eventOverlap: false,
  eventContent: (arg) => ({ domNodes: [cardFor(arg.event)] }),
  eventAllow: (dropInfo, draggedEvent) => {
    if (!draggedEvent) return true;
    if (draggedEvent.extendedProps.kind === "external") return false;
    const start = dropInfo.start ? dropInfo.start.getTime() : 0;
    const end = dropInfo.end
      ? dropInfo.end.getTime()
      : start + (draggedJob?.duration_min || 50) * 60000;
    if (overlapsExternal(start, end, draggedEvent.id)) {
      toast("Can't schedule over an external event", "error");
      return false;
    }
    return true;
  },
  eventDragStart: (info) => {
    draggedJob = info.event.extendedProps.job || null;
  },
  eventDragStop: () => {
    draggedJob = null;
  },
  eventDrop: async (info) => {
    const job = info.event.extendedProps.job;
    if (!job) {
      info.revert();
      return;
    }
    const start = info.event.start ? info.event.start.toISOString() : null;
    const end = info.event.end
      ? info.event.end.toISOString()
      : new Date(new Date(start).getTime() + job.duration_min * 60000).toISOString();
    try {
      await api(`jobs/${job.id}/move`, {
        method: "POST",
        body: { slot_start: start, slot_end: end },
      });
      toast("Job rescheduled", "success");
      await loadEvents();
    } catch (error) {
      info.revert();
      toast(error.message, "error");
    }
  },
  eventClick: (info) => {
    if (info.event.extendedProps.kind === "external") {
      toast(
        `${info.event.title}${info.event.extendedProps.venue ? ` · ${info.event.extendedProps.venue}` : ""} — external event (fixed)`,
        "info"
      );
      return;
    }
    openPopover(info.event, info.el);
  },
  datesSet: loadEvents,
});
calendar.render();

document.getElementById("btn-today").onclick = () => calendar.today();
document.getElementById("btn-prev").onclick = () => calendar.prev();
document.getElementById("btn-next").onclick = () => calendar.next();
document.addEventListener("click", (event) => {
  if (!popover.classList.contains("hidden") && !popover.contains(event.target)) closePopover();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closePopover();
});

initNav({ active: "Week", onDataChanged: loadEvents });
loadEvents();
