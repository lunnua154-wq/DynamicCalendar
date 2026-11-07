/** P1 — Calendar (month view). */

import { Calendar } from "@fullcalendar/core";
import dayGridPlugin from "@fullcalendar/daygrid";
import interactionPlugin from "@fullcalendar/interaction";
import "@fullcalendar/daygrid";
import "../tailwind.css";
import { api, toast, dialog, utcTime, utcDay, setText, formatHours, initSettings, localDateString, formatMonth } from "./common.js";

const calendarEl = document.getElementById("calendar");
const monthEl = document.getElementById("calendar-month");
const dayPanel = document.getElementById("day-panel");
const dayPanelDate = document.getElementById("day-panel-date");
const dayPanelList = document.getElementById("day-panel-list");
const btnClose = document.getElementById("btn-day-close");
const btnImport = document.getElementById("btn-import");
const btnReplan = document.getElementById("btn-replan");
const btnToday = document.getElementById("btn-today");
const btnSettings = document.getElementById("btn-settings");
const fileIcs = document.getElementById("file-ics");
const examBanner = document.getElementById("exam-banner");
const examBannerText = document.getElementById("exam-banner-text");
const btnExamCreate = document.getElementById("btn-exam-create");
const btnExamDismiss = document.getElementById("btn-exam-dismiss");
const emptyState = document.getElementById("empty-state");

let calendar;
let pendingExam = null; // {event_id, title, dtstart, dtend, venue} — full exam object from the import response
const statsPanel = document.getElementById("stats-panel");
const statsList = document.getElementById("stats-list");

function monthRange(date) {
  // Local calendar days: toISOString() would shift by the UTC offset and drop
  // the first/last day of the visible month.
  const start = new Date(date.getFullYear(), date.getMonth(), 1);
  const end = new Date(date.getFullYear(), date.getMonth() + 1, 0);
  return { from: localDateString(start), to: localDateString(end) };
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
    // checkbox for pending jobs (optimistic tick, reverts on failure)
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "mt-1.5 shrink-0";
    cb.title = "Mark done";
    cb.onclick = async (ev) => {
      ev.stopPropagation();
      cb.disabled = true;
      cb.checked = true;
      container.classList.add("line-through", "text-slate-400", "opacity-60");
      try {
        await tickJob(job.id);
        toast("Job marked done", "success");
      } catch (err) {
        cb.checked = false;
        container.classList.remove("line-through", "text-slate-400", "opacity-60");
        toast("Failed: " + err.message, "error");
      } finally {
        cb.disabled = false;
      }
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
    setText(monthEl, formatMonth(view.currentStart));
    // Hide the empty-state overlay when there is anything to show (the old
    // condition inverted this and covered a populated calendar).
    emptyState.classList.toggle("hidden", events.length > 0);
    renderStats();
  } catch (err) {
    toast("Failed to load calendar: " + err.message, "error");
  }
}

/** Goal progress / stats panel (M4/M5). Populated from GET /api/stats.
 *  Hidden when there are no open goals. */
async function renderStats() {
  if (!statsPanel || !statsList) return;
  try {
    const raw = await api("stats");
    const payload = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
    const goals = Array.isArray(payload.goals) ? payload.goals : [];
    const open = goals.filter((g) => g.status === "open");
    statsPanel.classList.toggle("hidden", open.length === 0);
    statsList.innerHTML = "";
    const totals = payload.totals || {};
    const panelRevisionsDue = totals.revisions_due != null ? totals.revisions_due : null;
    open.forEach((g) => {
      const total = g.total_effort_hours != null ? Number(g.total_effort_hours) : 0;
      const done = g.done_effort_hours != null ? Number(g.done_effort_hours) : 0;
      let progress = g.progress;
      if (progress == null) progress = total > 0 ? done / total : 0;
      if (progress > 1) progress = progress / 100;
      const pct = Math.round(Math.max(0, Math.min(1, progress)) * 100);

      const row = document.createElement("div");
      row.className = "flex items-center gap-3 px-3 py-2 rounded border border-slate-200 bg-slate-50";

      const left = document.createElement("div");
      left.className = "flex-1 min-w-0";
      const name = document.createElement("div");
      name.className = "text-xs font-medium text-slate-900 truncate";
      name.textContent = g.title || "Goal";
      left.appendChild(name);
      const sub = document.createElement("div");
      sub.className = "flex items-center gap-2 text-[10px] text-slate-500";
      const bar = document.createElement("div");
      bar.className = "h-1.5 flex-1 min-w-[120px] bg-slate-200 rounded overflow-hidden";
      const fill = document.createElement("div");
      fill.className = "h-full bg-blue-600";
      fill.style.width = `${pct}%`;
      bar.appendChild(fill);
      sub.appendChild(bar);
      const doneText = document.createElement("span");
      doneText.textContent = `${formatHours(done)} / ${formatHours(total)}`;
      sub.appendChild(doneText);
      left.appendChild(sub);

      const right = document.createElement("div");
      right.className = "text-[10px] text-slate-500 whitespace-nowrap";
      const due = g.revisions_due != null ? g.revisions_due : panelRevisionsDue;
      right.textContent = `planned ${g.planned_jobs ?? 0}${due != null ? ` · due in 7d ${due}` : ""}`;

      row.appendChild(left);
      row.appendChild(right);
      statsList.appendChild(row);
    });
  } catch {
    // stats endpoint may not exist yet; keep the panel hidden rather than
    // breaking the month grid.
    statsPanel.classList.toggle("hidden", true);
  }
}

async function tickJob(jobId) {
  await api(`jobs/${jobId}/complete`, { method: "POST" });
  loadEvents();
}

function openDay(date) {
  dayPanel.classList.toggle("hidden", false);
  setText(dayPanelDate, utcDay(date));
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
    let venueText = "";
    if (e.extendedProps.venue) venueText = `· ${e.extendedProps.venue}`;
    else if (job) venueText = `· ${job.duration_min != null ? job.duration_min + "m" : ""} ${job.kind || ""}`.trim();
    venue.textContent = venueText;
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
        cb.checked = true;
        row.classList.add("line-through", "text-slate-400", "opacity-60");
        try {
          await tickJob(job.id);
          toast("Job marked done", "success");
        } catch (err) {
          cb.checked = false;
          row.classList.remove("line-through", "text-slate-400", "opacity-60");
          toast("Failed: " + err.message, "error");
        } finally {
          cb.disabled = false;
        }
      };
      row.appendChild(cb);
    } else if (job && job.status === "done") {
      row.classList.add("line-through", "text-slate-400", "opacity-60");
    }
    dayPanelList.appendChild(row);
  });
}

btnClose.onclick = () => dayPanel.classList.toggle("hidden", true);
btnToday.onclick = () => calendar.today();
btnImport.onclick = () => fileIcs.click();
fileIcs.onchange = async () => {
  const file = fileIcs.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  try {
    const d = await api("ics/import", { method: "POST", body: form });
    toast(`Imported ${d.imported ?? 0} events (${d.skipped ?? 0} skipped)`, "success");
    fileIcs.value = "";
    loadEvents();
    const exams =
      d.exams ||
      (d.exams_found && Array.isArray(d.exams_found)
        ? d.exams_found.map((t) => ({ title: t }))
        : []);
    if (exams.length && !pendingExam) {
      pendingExam = exams[0];
      showExamBanner(exams);
    }
  } catch (err) {
    toast("Import failed: " + err.message, "error");
  }
};

function showExamBanner(exams) {
  const ex = exams[0];
  pendingExam = ex;
  setText(
    examBannerText,
    `Exam detected: ${ex.title} — ${utcDay(ex.dtstart)} ${utcTime(ex.dtstart)}–${utcTime(ex.dtend)} @ ${ex.venue || "TBD"}`,
  );
  examBanner.classList.toggle("hidden", false);
}
btnExamDismiss.onclick = () => examBanner.classList.toggle("hidden", true);
btnExamCreate.onclick = async () => {
  if (!pendingExam) return;
  const ok = await dialog("Create goal", `Create a goal for "${pendingExam.title}"?`);
  if (!ok) return;
  try {
    await api("goals", {
      method: "POST",
      body: { title: pendingExam.title, exam_event_id: pendingExam.event_id },
    });
    toast("Goal created", "success");
    pendingExam = null;
    examBanner.classList.toggle("hidden", true);
    loadEvents();
  } catch (err) {
    toast("Failed to create goal: " + err.message, "error");
  }
};

btnReplan.onclick = async () => {
  const ok = await dialog("Re-plan", "Recompute all pending future study work?");
  if (!ok) return;
  // Re-plan every open goal; sum up moved jobs.
  try {
    const goals = await api("goals");
    let moved = 0;
    for (const g of goals) {
      if (g.status === "closed" || g.status === "done" || g.status === "completed") continue;
      const res = await api(`goals/${g.id}/replan`, { method: "POST" });
      moved += res.moved_count ?? res.moved_jobs?.length ?? 0;
    }
    toast(`Re-planned ${moved} jobs`, "success");
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
  // Clicking anywhere in a day cell opens the day panel (docs/UX_SPEC.md P1).
  // Without this only clicking an event chip worked, so empty days — and days
  // whose events were collapsed behind "+n more" — could not be opened at all.
  dateClick: (info) => openDay(info.date),
  eventClick: (info) => openDay(info.event.start || new Date()),
  datesSet: loadEvents,
});
calendar.render();
loadEvents();
initSettings(btnSettings);
