/** P1 — Calendar (month view). */

import { Calendar } from "@fullcalendar/core";
import dayGridPlugin from "@fullcalendar/daygrid";
import interactionPlugin from "@fullcalendar/interaction";
import "@fullcalendar/daygrid";
import "../tailwind.css";
import { api, toast, dialog, utcTime, utcDay, setText } from "./common.js";

const calendarEl = document.getElementById("calendar");
const monthEl = document.getElementById("calendar-month");
const dayPanel = document.getElementById("day-panel");
const dayPanelDate = document.getElementById("day-panel-date");
const dayPanelList = document.getElementById("day-panel-list");
const btnClose = document.getElementById("btn-day-close");
const btnImport = document.getElementById("btn-import");
const btnReplan = document.getElementById("btn-replan");
const btnToday = document.getElementById("btn-today");
const fileIcs = document.getElementById("file-ics");
const examBanner = document.getElementById("exam-banner");
const examBannerText = document.getElementById("exam-banner-text");
const btnExamCreate = document.getElementById("btn-exam-create");
const btnExamDismiss = document.getElementById("btn-exam-dismiss");
const emptyState = document.getElementById("empty-state");

let calendar;
let pendingExam = null; // {title, dtstart, venue}

function monthRange(date) {
  const start = new Date(date.getFullYear(), date.getMonth(), 1);
  const end = new Date(date.getFullYear(), date.getMonth() + 1, 0);
  const iso = (d) => d.toISOString().slice(0, 10);
  return { from: iso(start), to: iso(end) };
}

function eventKindClass(kind) {
  if (kind === "external") return "border-external bg-external/10";
  if (kind === "revision") return "border-revision bg-revision/10 border-dashed";
  return "border-study bg-study/10";
}

function renderEvent(arg) {
  const e = arg.event;
  const job = e.extendedProps.job;
  const isExternal = e.extendedProps.kind === "external";
  const container = document.createElement("div");
  container.className = `p-1 text-[10px] leading-tight ${eventKindClass(e.extendedProps.kind)} rounded border`;

  const timeLine = document.createElement("div");
  timeLine.className = isExternal ? "font-medium text-slate-900" : "text-slate-700 font-medium";
  if (!e.allDay) {
    timeLine.textContent = `${utcTime(e.startStr)}–${utcTime(e.endStr)}`;
  } else {
    timeLine.textContent = "All day";
  }

  const titleLine = document.createElement("div");
  titleLine.className = "truncate text-slate-900";
  titleLine.textContent = e.title;

  const venueLine = document.createElement("div");
  venueLine.className = "text-[9px] text-slate-500 truncate";
  venueLine.textContent = e.extendedProps.venue ? `· ${e.extendedProps.venue}` : "";

  container.appendChild(timeLine);
  container.appendChild(titleLine);
  container.appendChild(venueLine);

  if (job && job.status !== "done") {
    // checkbox for pending jobs
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "mt-1.5";
    cb.title = "Mark done";
    cb.onclick = async (ev) => {
      ev.stopPropagation();
      await tickJob(job.id);
    };
    container.appendChild(cb);
  }

  return { domNodes: [container] };
}

async function loadEvents() {
  if (!calendar) return;
  const view = calendar.view;
  const range = monthRange(view.currentStart || new Date());
  try {
    const events = await api(`events?from=${range.from}&to=${range.to}`);
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
    setText(monthEl, view.currentStart.toLocaleDateString(undefined, { month: "long", year: "numeric" }));
    emptyState.hidden = events.length === 0;
    // exam banner: any external event with EXAM category isn't stored, so we
    // detect via goals/exams is handled server-side; here we just show banner
    // if an exam-ish title exists and no goal yet. Kept simple for M0.
  } catch (err) {
    toast("Failed to load calendar: " + err.message, "error");
  }
}

async function tickJob(jobId) {
  try {
    await api(`jobs/${jobId}/complete`, { method: "POST" });
    toast("Job marked done", "success");
    loadEvents();
  } catch (err) {
    toast("Failed to mark done: " + err.message, "error");
  }
}

function openDay(date, event) {
  dayPanel.hidden = false;
  setText(dayPanelDate, utcDay(date) + " · " + date.toLocaleDateString(undefined, { weekday: "long" }));
  dayPanelList.innerHTML = "";
  // gather events for that day
  const startOfDay = new Date(date);
  startOfDay.setHours(0, 0, 0, 0);
  const endOfDay = new Date(date);
  endOfDay.setHours(23, 59, 59, 999);
  const dayEvents = calendar.getEvents().filter((e) => {
    const es = new Date(e.start);
    return es >= startOfDay && es <= endOfDay;
  });
  dayEvents.sort((a, b) => new Date(a.start) - new Date(b.start));
  if (dayEvents.length === 0) {
    dayPanelList.textContent = "No events.";
    return;
  }
  dayEvents.forEach((e) => {
    const job = e.extendedProps.job;
    const row = document.createElement("div");
    row.className = "flex items-start gap-2 p-2 rounded border border-slate-200 bg-slate-50";
    const info = document.createElement("div");
    info.className = "flex-1 min-w-0";
    const time = document.createElement("div");
    time.className = "text-[11px] text-slate-500";
    time.textContent = e.allDay ? "All day" : `${utcTime(e.start)}–${utcTime(e.end)}`;
    const title = document.createElement("div");
    title.className = "text-sm font-medium truncate";
    title.textContent = e.title;
    const venue = document.createElement("div");
    venue.className = "text-[11px] text-slate-500 truncate";
    venue.textContent = e.extendedProps.venue ? `· ${e.extendedProps.venue}` : (job ? `· ${job.kind}` : "");
    info.appendChild(time);
    info.appendChild(title);
    info.appendChild(venue);
    row.appendChild(info);
    if (job && job.status !== "done") {
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "mt-0.5 shrink-0";
      cb.title = "Mark done";
      cb.onclick = async (ev) => {
        ev.stopPropagation();
        cb.disabled = true;
        try {
          await api(`jobs/${job.id}/complete`, { method: "POST" });
          cb.checked = true;
          row.classList.add("line-through", "text-slate-400", "opacity-60");
          toast("Job marked done", "success");
          loadEvents();
        } catch (err) {
          cb.disabled = false;
          toast("Failed: " + err.message, "error");
        }
      };
      row.appendChild(cb);
    } else if (job && job.status === "done") {
      row.classList.add("line-through", "text-slate-400", "opacity-60");
    }
    dayPanelList.appendChild(row);
  });
}

btnClose.onclick = () => (dayPanel.hidden = true);
btnToday.onclick = () => calendar.today();
btnImport.onclick = () => fileIcs.click();
fileIcs.onchange = async () => {
  const file = fileIcs.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  try {
    const res = await fetch("/api/ics/import", { method: "POST", body: form });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.error || "import failed");
    }
    const d = await res.json();
    toast(`Imported ${d.imported} events (${d.skipped} skipped)`, "success");
    fileIcs.value = "";
    loadEvents();
    if (d.exams_found && d.exams_found.length && !pendingExam) {
      pendingExam = d.exams_found[0];
      showExamBanner(d.exams_found);
    }
  } catch (err) {
    toast("Import failed: " + err.message, "error");
  }
};

function showExamBanner(titles) {
  setText(examBannerText, `Exam detected: ${titles.join(", ")} — create a goal to schedule study around it.`);
  examBanner.hidden = false;
}
btnExamDismiss.onclick = () => (examBanner.hidden = true);
btnExamCreate.onclick = async () => {
  if (!pendingExam) return;
  const ok = await dialog("Create goal", `Create a goal for "${pendingExam}"?`);
  if (!ok) return;
  try {
    // deadline default: the exam date; user can adjust later. Use a placeholder
    // goal; server replans. We need the exam event id — not available here, so
    // create goal with the exam title as deadline = today+0 (placeholder).
    const res = await fetch("/api/goals", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: pendingExam, deadline: new Date().toISOString().slice(0, 10) }),
    });
    if (!res.ok) throw new Error("goal create failed");
    toast("Goal created", "success");
    pendingExam = null;
    examBanner.hidden = true;
    loadEvents();
  } catch (err) {
    toast("Failed to create goal: " + err.message, "error");
  }
};

btnReplan.onclick = async () => {
  const ok = await dialog("Re-plan", "Recompute all pending future study work?");
  if (!ok) return;
  // Re-plan every open goal.
  try {
    const goals = await api("goals");
    for (const g of goals) {
      if (g.status !== "open") continue;
      await api(`goals/${g.id}/replan`, { method: "POST" });
    }
    toast("Re-planned all goals", "success");
    loadEvents();
  } catch (err) {
    toast("Re-plan failed: " + err.message, "error");
  }
};

calendar = new Calendar(calendarEl, {
  plugins: [dayGridPlugin, interactionPlugin],
  initialView: "dayGridMonth",
  height: "auto",
  headerToolbar: false,
  initialDate: new Date(),
  events: [],
  eventContent: renderEvent,
  eventClick: (info) => {
    const job = info.event.extendedProps.job;
    if (job && job.status !== "done") {
      // let the checkbox handle ticks; clicking elsewhere opens the day panel
      openDay(info.event.start, info.event);
    } else {
      openDay(info.event.start, info.event);
    }
  },
  datesSet: loadEvents,
});
calendar.render();
loadEvents();
