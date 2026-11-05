/** P1 — Calendar (month view) + day panel with checkboxes. */

import { Calendar } from "@fullcalendar/core";
import dayGridPlugin from "@fullcalendar/daygrid";
import interactionPlugin from "@fullcalendar/interaction";
import "../tailwind.css";
import {
  api,
  busy,
  dialog,
  el,
  fmtDay,
  fmtMonth,
  fmtTime,
  initNav,
  localDayString,
  show,
  toast,
  toggle,
} from "./common.js";

const monthEl = document.getElementById("calendar-month");
const dayPanel = document.getElementById("day-panel");
const dayPanelDate = document.getElementById("day-panel-date");
const dayPanelList = document.getElementById("day-panel-list");
const examBanner = document.getElementById("exam-banner");
const examBannerText = document.getElementById("exam-banner-text");
const emptyState = document.getElementById("empty-state");
const fileIcs = document.getElementById("file-ics");
const btnImport = document.getElementById("btn-import");
const btnReplan = document.getElementById("btn-replan");

let calendar;
let events = [];
let pendingExam = null;
let selectedDay = null;

const CHIP = {
  base: "px-1 py-0.5 rounded border text-[10px] leading-tight overflow-hidden",
  external: "bg-sky-50 border-sky-400 text-sky-900",
  study: "bg-amber-50 border-amber-400 text-amber-900",
  revision: "bg-violet-50 border-violet-400 border-dashed text-violet-900",
  done: "bg-slate-100 border-slate-300 text-slate-500 line-through opacity-70",
};

function chipClass(event) {
  const job = event.extendedProps.job;
  if (job && job.status === "done") return `${CHIP.base} ${CHIP.done}`;
  if (event.extendedProps.kind === "revision") return `${CHIP.base} ${CHIP.revision}`;
  if (event.extendedProps.kind === "external") return `${CHIP.base} ${CHIP.external}`;
  return `${CHIP.base} ${CHIP.study}`;
}

function renderEvent(arg) {
  const event = arg.event;
  const job = event.extendedProps.job;
  const chip = el("div", chipClass(event));

  const time = el(
    "div",
    "font-medium",
    event.allDay ? "All day" : `${fmtTime(event.start)}–${fmtTime(event.end)}`
  );
  chip.appendChild(time);
  chip.appendChild(el("div", "truncate", event.title));
  if (event.extendedProps.venue) {
    chip.appendChild(el("div", "truncate text-[9px] opacity-80", event.extendedProps.venue));
  }

  if (job && job.status !== "done") {
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "mt-1 cursor-pointer";
    checkbox.title = "Mark done";
    checkbox.setAttribute("aria-label", `Mark ${event.title} done`);
    checkbox.onclick = async (clickEvent) => {
      clickEvent.stopPropagation();
      checkbox.disabled = true;
      const ok = await tick(job.id);
      if (!ok) checkbox.disabled = false;
    };
    chip.appendChild(checkbox);
  }
  return { domNodes: [chip] };
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
      calendar.addEvent({
        id: event.id,
        title: event.title,
        start: event.dtstart,
        end: event.dtend,
        allDay: event.all_day === 1,
        extendedProps: {
          kind: event.kind,
          venue: event.venue || "",
          job: event.job || null,
          categories: event.categories || [],
        },
      });
    });
    monthEl.textContent = fmtMonth(view.currentStart);
    toggle(emptyState, events.length === 0);
    emptyState.classList.toggle("flex", events.length === 0);
    if (selectedDay) renderDayPanel(selectedDay);
  } catch (error) {
    toast(`Failed to load calendar: ${error.message}`, "error");
  }
}

async function tick(jobId) {
  try {
    const result = await api(`jobs/${jobId}/complete`, { method: "POST" });
    const revisions = (result.revisions_added || []).length;
    toast(
      revisions ? `Done — ${revisions} revision job(s) scheduled` : "Job marked done",
      "success"
    );
    await loadEvents();
    return true;
  } catch (error) {
    toast(error.message, "error");
    return false;
  }
}

function dayKey(iso) {
  return localDayString(new Date(iso));
}

function renderDayPanel(day) {
  selectedDay = day;
  const key = localDayString(day);
  dayPanelDate.textContent = new Date(day).toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "short",
  });
  dayPanelList.innerHTML = "";
  const dayEvents = events
    .filter((event) => dayKey(event.dtstart) === key)
    .sort((a, b) => a.dtstart.localeCompare(b.dtstart));
  if (!dayEvents.length) {
    dayPanelList.appendChild(el("p", "text-sm text-slate-400", "Nothing on this day."));
    return;
  }
  dayEvents.forEach((event) => {
    const job = event.job;
    const row = el(
      "div",
      "flex items-start gap-2 p-2 rounded-lg border border-slate-200 bg-slate-50"
    );
    const info = el("div", "flex-1 min-w-0");
    const time = el(
      "div",
      "text-[11px] text-slate-500",
      event.all_day === 1 ? "All day" : `${fmtTime(event.dtstart)}–${fmtTime(event.dtend)}`
    );
    const title = el("div", "text-sm font-medium truncate", event.title);
    info.append(time, title);

    const metaText = [];
    if (event.venue) metaText.push(event.venue);
    if (job) metaText.push(job.kind === "revision" ? `revision R${job.revision_round}` : "study job");
    if (job) metaText.push(`${job.duration_min} min`);
    if (metaText.length) {
      info.appendChild(el("div", "text-[11px] text-slate-500 truncate", metaText.join(" · ")));
    }
    row.appendChild(info);

    if (job) {
      if (job.status === "done") {
        row.classList.add("opacity-60");
        title.classList.add("line-through");
        row.appendChild(el("span", "text-[10px] text-emerald-600 font-medium", "done"));
      } else if (job.status === "conflicted") {
        row.classList.add("border-rose-300", "bg-rose-50");
        row.appendChild(el("span", "text-[10px] text-rose-600 font-medium", "conflicted"));
      } else {
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.className = "mt-0.5 shrink-0 cursor-pointer";
        checkbox.title = "Mark done";
        checkbox.setAttribute("aria-label", `Mark ${event.title} done`);
        checkbox.onclick = async () => {
          checkbox.disabled = true;
          const ok = await tick(job.id);
          if (!ok) {
            checkbox.checked = false;
            checkbox.disabled = false;
          }
        };
        row.appendChild(checkbox);
      }
    }
    dayPanelList.appendChild(row);
  });
}

function openDayPanel(date) {
  show(dayPanel);
  dayPanel.classList.add("flex");
  dayPanel.classList.remove("hidden");
  renderDayPanel(date);
}

function closeDayPanel() {
  dayPanel.classList.add("hidden");
  dayPanel.classList.remove("flex");
  selectedDay = null;
}

// ------------------------------------------------------------------ exam banner

function showExamBanner(exam) {
  pendingExam = exam;
  examBannerText.textContent = `Exam detected: ${exam.title} — ${fmtDay(exam.dtstart)} ${fmtTime(
    exam.dtstart
  )}${exam.venue ? ` @ ${exam.venue}` : ""}`;
  show(examBanner);
  examBanner.classList.add("flex");
}

function hideExamBanner() {
  pendingExam = null;
  examBanner.classList.add("hidden");
  examBanner.classList.remove("flex");
}

async function detectUnlinkedExam() {
  try {
    const [allEvents, goals] = await Promise.all([
      api("events?from=1970-01-01&to=2100-01-01"),
      api("goals"),
    ]);
    const linked = new Set(goals.map((goal) => goal.exam_event_id).filter(Boolean));
    const exam = allEvents.find(
      (event) =>
        event.kind === "external" &&
        (event.categories || []).some((category) => category.toLowerCase() === "exam") &&
        !linked.has(event.id)
    );
    if (exam) showExamBanner(exam);
  } catch {
    /* non-fatal */
  }
}

// ------------------------------------------------------------------ actions

btnImport.onclick = () => fileIcs.click();

fileIcs.onchange = async () => {
  const file = fileIcs.files?.[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  btnImport.disabled = true;
  btnImport.textContent = "Importing…";
  try {
    const result = await api("ics/import", { method: "POST", body: form });
    toast(`Imported ${result.imported} event(s), ${result.skipped} unchanged`, "success");
    fileIcs.value = "";
    await loadEvents();
    if (result.exam_events?.length) showExamBanner(result.exam_events[0]);
  } catch (error) {
    toast(`Import failed: ${error.message}`, "error");
  } finally {
    btnImport.disabled = false;
    btnImport.textContent = "Import .ics";
  }
};

btnReplan.onclick = async () => {
  const confirmed = await dialog({
    title: "Re-plan",
    message: "Recompute all pending future study work for every open goal?",
    confirmLabel: "Re-plan",
  });
  if (!confirmed) return;
  await busy(btnReplan, async () => {
    const goals = await api("goals");
    let moved = 0;
    for (const goal of goals.filter((g) => g.status === "open")) {
      const result = await api(`goals/${goal.id}/replan`, { method: "POST" });
      moved += (result.moved_jobs || []).length;
    }
    toast(`Re-planned — ${moved} job(s) moved`, "success");
    await loadEvents();
  });
};

document.getElementById("btn-exam-dismiss").onclick = hideExamBanner;
document.getElementById("btn-exam-create").onclick = async () => {
  if (!pendingExam) return;
  const confirmed = await dialog({
    title: "Create goal",
    message: `Create a study goal for "${pendingExam.title}" with the exam as its deadline?`,
    confirmLabel: "Create goal",
  });
  if (!confirmed) return;
  try {
    await api("goals", {
      method: "POST",
      body: { title: pendingExam.title, exam_event_id: pendingExam.id },
    });
    toast("Goal created", "success");
    hideExamBanner();
    await loadEvents();
  } catch (error) {
    toast(`Failed to create goal: ${error.message}`, "error");
  }
};

// ------------------------------------------------------------------ boot

calendar = new Calendar(document.getElementById("calendar"), {
  plugins: [dayGridPlugin, interactionPlugin],
  initialView: "dayGridMonth",
  headerToolbar: false,
  height: "100%",
  dayMaxEvents: 3,
  fixedWeekCount: false,
  eventContent: renderEvent,
  eventDisplay: "block",
  dateClick: (info) => openDayPanel(info.date),
  eventClick: (info) => openDayPanel(info.event.start || new Date()),
  datesSet: loadEvents,
});
calendar.render();

document.getElementById("btn-today").onclick = () => calendar.today();
document.getElementById("btn-prev").onclick = () => calendar.prev();
document.getElementById("btn-next").onclick = () => calendar.next();
document.getElementById("btn-day-close").onclick = closeDayPanel;

initNav({ active: "Calendar", onDataChanged: loadEvents });
loadEvents();
detectUnlinkedExam();
