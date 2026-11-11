/** P3 — Inbox: chat + file drop zone + material list + topics. */

import "../tailwind.css";
import { api, toast, setText, initSettings, promptDialog, dialog, formatHours } from "./common.js";
import { initTour } from "./tour.js";

const chatList = document.getElementById("chat-list");
const chatInput = document.getElementById("chat-input");
const chatSend = document.getElementById("chat-send");
const llmBadge = document.getElementById("llm-badge");
const goalSelect = document.getElementById("goal-select");
const newGoalTitle = document.getElementById("new-goal-title");
const newGoalDeadline = document.getElementById("new-goal-deadline");
const btnNewGoal = document.getElementById("btn-new-goal");
const dropZone = document.getElementById("drop-zone");
const fileInput = document.getElementById("file-input");
const btnBrowse = document.getElementById("btn-browse");
const fileList = document.getElementById("file-list");
const attachBanner = document.getElementById("attach-banner");
const topicsStrip = document.getElementById("topics-strip");
const btnSettings = document.getElementById("btn-settings");

let pollTimer = null;
let materials = []; // {id, filename, summary, tags, effort_hours, status, duplicate_of, topic_id, status_reason}
let topics = []; // {id, name, file_count, members: [{filename}]}

function chip(text, kind) {
  const map = {
    ready: "bg-emerald-100 text-emerald-800",
    unattached: "bg-tertiary-container text-on-tertiary-container",
    duplicate: "bg-tertiary-container text-on-tertiary-container",
    superseded: "bg-surface-container-high text-on-surface-variant",
    error: "bg-error-container text-on-error-container",
    processing: "bg-primary-container text-on-primary-container animate-pulse",
  };
  const el = document.createElement("span");
  el.className = `text-[10px] font-medium px-2.5 py-0.5 rounded-full ${map[kind] || "bg-surface-container-high text-on-surface-variant"}`;
  el.textContent = text;
  return el;
}

function appendChat(role, content) {
  const div = document.createElement("div");
  div.className = `flex ${role === "user" ? "justify-end" : "justify-start"}`;
  const bubble = document.createElement("div");
  bubble.className = `max-w-[80%] p-3 rounded-2xl text-sm whitespace-pre-wrap break-words ${
    role === "user"
      ? "bg-primary text-on-primary rounded-tr-lg"
      : "bg-secondary-container text-on-secondary-container rounded-tl-lg"
  }`;
  bubble.textContent = content;
  div.appendChild(bubble);
  chatList.appendChild(div);
  chatList.scrollTop = chatList.scrollHeight;
}

async function setBadgeFromSettings() {
  try {
    const status = await api("llm/status");
    if (status.last_error && status.configured) {
      setLlmBadge("error", status.last_error.message);
    } else {
      setLlmBadge(status.configured ? "on" : "off", status.model);
    }
  } catch {
    setLlmBadge("off");
  }
}

// mode: "on" (configured), "off" (offline), "error" (configured but failing).
function setLlmBadge(mode, detail = "") {
  const styles = {
    on: "bg-emerald-100 text-emerald-800",
    off: "bg-secondary-container text-on-secondary-container",
    error: "bg-error-container text-on-error-container",
  };
  llmBadge.textContent =
    mode === "on" ? `(LLM on${detail ? ` · ${detail}` : ""})` : mode === "error" ? "(LLM error)" : "(LLM off)";
  llmBadge.className = `text-[10px] font-medium px-2.5 py-0.5 rounded-full max-w-[14rem] truncate ${styles[mode] || styles.off}`;
  llmBadge.title =
    mode === "error"
      ? `The configured LLM endpoint failed: ${detail}. Replies use offline answers.`
      : mode === "on"
      ? `LLM endpoint configured: ${detail}`
      : "No LLM endpoint configured — deterministic offline answers.";
}

async function loadChatHistory() {
  // Render persisted history (oldest first). Endpoint may not exist yet.
  try {
    const messages = await api("chat/messages");
    (Array.isArray(messages) ? messages : []).forEach((m) => appendChat(m.role || "user", m.content || m.message || ""));
  } catch {
    // no history yet
  }
}

async function sendChat() {
  const text = chatInput.value.trim();
  if (!text) return;
  chatInput.value = "";
  appendChat("user", text);
  try {
    const res = await api("chat", { method: "POST", body: { message: text } });
    appendChat("assistant", res.reply);
    if (res.llm_error) {
      // The reply came from the offline fallback because the configured
      // endpoint failed — say so instead of pretending the LLM answered.
      setLlmBadge("error", res.llm_error.message);
      appendWarning(`LLM unavailable (${res.llm_error.skill}): ${res.llm_error.message}`);
    } else if (res.llm === "on") {
      setLlmBadge("on");
    } else {
      setLlmBadge("off");
    }
    if (Array.isArray(res.actions) && res.actions.length > 0) {
      appendReceipts(res.actions);
    }
  } catch (err) {
    appendChat("assistant", "Failed: " + err.message);
  }
}

/** Small muted warning under the last assistant bubble (never persisted). */
function appendWarning(text) {
  const row = document.createElement("div");
  row.className = "flex justify-start";
  const note = document.createElement("div");
  note.className =
    "text-[11px] text-on-error-container bg-error-container rounded-lg px-3 py-1.5 max-w-[80%]";
  note.textContent = text;
  row.appendChild(note);
  chatList.appendChild(row);
  chatList.scrollTop = chatList.scrollHeight;
}

const READ_LABELS = {
  list_goals: "read goals",
  list_jobs: "read jobs",
  list_events: "read external events",
  list_materials: "read materials",
  get_stats: "read stats",
};

// Human summary for one chat tool result. Never injects result into innerHTML.
function receiptLine(action) {
  const tool = action && action.tool;
  const args = action && action.args || {};
  const result = action && action.result || {};
  if (result && result.error) {
    return { text: `${tool} failed: ${result.error}`, error: true };
  }
  const label = READ_LABELS[tool];
  if (label) return { text: `⚙ ${label}` };
  if (tool === "update_settings") {
    const summary = settingsSummary(result);
    if (summary) return { text: `⚙ ${summary}` };
    return { text: `⚙ ${tool}` };
  }
  if (tool === "set_material_effort") {
    return { text: `⚙ ${effortSummary(args, result)}` };
  }
  return { text: String(tool) };
}

function settingsSummary(result) {
  const updated = result && result.updated;
  if (!updated || typeof updated !== "object") return null;
  const parts = Object.entries(updated)
    .filter(([, v]) => v !== undefined && v !== null)
    .map(([k, v]) => `${k} ${v}`);
  return parts.length ? `settings → ${parts.join(", ")}` : null;
}

function effortSummary(args, result) {
  const name = result.filename || args.material_id || "";
  const hours = Number(args.hours);
  const hs = Number.isFinite(hours) ? formatHours(hours) : "?h";
  return `effort → ${name} → ${hs}`;
}

// Appends a left-aligned (assistant side) muted receipt row under the last
// assistant bubble. Receipts are not persisted — live replies only.
function appendReceipts(actions) {
  if (!Array.isArray(actions) || actions.length === 0) return;
  const wrapper = document.createElement("div");
  wrapper.className = "flex justify-start";
  const row = document.createElement("div");
  row.className =
    "text-[11px] text-on-surface-variant bg-surface-container border border-outline-variant rounded-lg px-3 py-1.5 space-y-0.5 max-w-[80%]";
  let needsReplan = false;
  actions.forEach((action) => {
    const { text, error } = receiptLine(action);
    const line = document.createElement("div");
    line.className = error ? "text-error" : "";
    line.textContent = text;
    row.appendChild(line);
    if (action && action.tool === "update_settings") needsReplan = true;
  });
  if (needsReplan) {
    const hint = document.createElement("div");
    hint.className = "text-[10px] text-on-surface-variant pt-1";
    hint.textContent = "press Re-plan to apply";
    row.appendChild(hint);
  }
  wrapper.appendChild(row);
  chatList.appendChild(wrapper);
  chatList.scrollTop = chatList.scrollHeight;
}

chatSend.onclick = sendChat;
chatInput.onkeydown = (e) => {
  if (e.key === "Enter") sendChat();
};

btnNewGoal.onclick = createGoal;
goalSelect.addEventListener("change", () => renderFiles());
newGoalTitle.onkeydown = (e) => {
  if (e.key === "Enter") createGoal();
};

async function loadGoals() {
  try {
    const prev = goalSelect.value;
    const goals = await api("goals");
    goalSelect.innerHTML = '<option value="">Unattached</option>';
    goals.forEach((g) => {
      const o = document.createElement("option");
      o.value = g.id;
      o.textContent = g.title;
      goalSelect.appendChild(o);
    });
    // Keep the user's current selection if it survived the rebuild, else
    // pre-select the first open goal so drops land attached by default.
    if (prev && [...goalSelect.options].some((o) => o.value === prev)) {
      goalSelect.value = prev;
      return;
    }
    const firstOpen = goals.find((g) => g.status === "open");
    if (firstOpen) goalSelect.value = String(firstOpen.id);
  } catch (err) {
    toast("Failed to load goals: " + err.message, "error");
  }
}

async function createGoal() {
  const title = newGoalTitle.value.trim();
  if (!title) {
    toast("Enter a goal title", "error");
    return;
  }
  const deadline = newGoalDeadline.value;
  if (!deadline) {
    toast("pick a deadline", "error");
    return;
  }
  try {
    const res = await api("goals", {
      method: "POST",
      body: { title, deadline: `${deadline}T00:00:00Z` },
    });
    toast("Goal created", "success");
    newGoalTitle.value = "";
    newGoalDeadline.value = "";
    await loadGoals();
    if (res.id) goalSelect.value = String(res.id);
  } catch (err) {
    toast(err.message, "error");
  }
}

function humanResult(r) {
  if (r.status === "error") return `error: ${r.status_reason || r.error || r.reason || "upload failed"}`;
  if (r.status === "duplicate") return `duplicate of ${r.duplicate_of || r.duplicate_of_title || "an existing file"}`;
  if (r.cluster_relation === "version") return `version update of ${r.duplicate_of || "the previous version"}`;
  if (r.cluster_relation === "new") return `new topic "${r.tags && r.tags.length ? r.tags[0] : r.summary || ""}"`;
  return r.status;
}

async function uploadFiles(files) {
  if (!files.length) return;
  const form = new FormData();
  for (const f of files) form.append("files", f);
  if (goalSelect.value) form.append("goal_id", goalSelect.value);
  try {
    const results = await api("inbox/upload", { method: "POST", body: form });
    const rows = results || [];
    const attached = rows.filter((r) => r.status === "ready").length;
    const lines = rows.map(humanResult);
    let extra = "";
    if (goalSelect.value) {
      const sel = goalSelect.options[goalSelect.selectedIndex];
      if (attached > 0) {
        extra = ` Attached ${attached} ready file(s) to goal "${sel.textContent}".`;
      } else {
        extra = ' Nothing attached — see the list (duplicates/errors are not scheduled).';
      }
    }
    toast(`${rows.length} file(s) uploaded.${extra} ${lines.join("; ")}`, "success");
    refreshFiles();
  } catch (err) {
    toast("Upload failed: " + err.message, "error");
  }
}

// drag & drop
["dragenter", "dragover"].forEach((evt) => {
  dropZone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropZone.classList.add("border-primary", "bg-primary-container/30");
  });
});
["dragleave", "drop"].forEach((evt) => {
  dropZone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropZone.classList.remove("border-primary", "bg-primary-container/30");
  });
});
dropZone.addEventListener("drop", (e) => {
  uploadFiles(Array.from(e.dataTransfer.files));
});
btnBrowse.onclick = () => fileInput.click();
fileInput.onchange = () => uploadFiles(Array.from(fileInput.files));

async function refreshFiles() {
  try {
    materials = await api("inbox/files");
    renderFiles();
    renderTopics();
    startPolling();
  } catch (err) {
    toast("Failed to load files: " + err.message, "error");
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  if (materials.some((m) => m.status === "processing")) {
    pollTimer = setInterval(refreshFiles, 2000);
  } else {
    pollTimer = null;
  }
}

async function attachAllReady() {
  const goal_id = goalSelect.value;
  if (!goal_id) return;
  const sel = goalSelect.options[goalSelect.selectedIndex];
  try {
    const res = await api("inbox/attach_ready", { method: "POST", body: { goal_id } });
    toast(`Attached ${res.attached} ready file(s) to "${sel.textContent}".`, "success");
    refreshFiles();
  } catch (err) {
    toast("Attach failed: " + err.message, "error");
  }
}

function updateAttachBanner() {
  const unattached = materials.filter((m) => m.status === "ready" && !m.goal_id);
  attachBanner.textContent = "";
  if (unattached.length === 0) {
    attachBanner.classList.add("hidden");
    return;
  }
  const msg = document.createElement("span");
  msg.textContent =
    `${unattached.length} ready file(s) aren't attached to a goal — they won't be scheduled.`;
  attachBanner.appendChild(msg);
  const goalId = goalSelect.value;
  if (goalId) {
    const sel = goalSelect.options[goalSelect.selectedIndex];
    const btn = document.createElement("button");
    btn.className = "ml-auto underline font-semibold text-on-tertiary-container shrink-0";
    btn.textContent = `Attach all to "${sel.textContent}"`;
    btn.onclick = attachAllReady;
    attachBanner.appendChild(btn);
  }
  attachBanner.classList.remove("hidden");
}

function renderFiles() {
  fileList.innerHTML = "";
  updateAttachBanner();
  if (materials.length === 0) {
    fileList.textContent = "No files yet. Drop some in.";
    return;
  }
  const goalSelected = goalSelect.value;
  materials.forEach((m) => {
    const row = document.createElement("div");
    row.className = "flex items-start gap-3 p-4 rounded-xl border border-outline-variant bg-surface-container-lowest";
    const left = document.createElement("div");
    left.className = "flex-1 min-w-0";
    const name = document.createElement("div");
    name.className = "text-sm font-medium text-on-surface truncate";
    name.textContent = m.filename;
    left.appendChild(name);
    const statusBar = document.createElement("div");
    statusBar.className = "flex items-center gap-3 mt-1 text-xs flex-wrap";
    statusBar.appendChild(chip(m.status, m.status));
    if (m.status === "ready" && !m.goal_id) {
      statusBar.appendChild(chip("unattached", "unattached"));
    }
    if (m.status_reason) {
      const reason = document.createElement("span");
      reason.className = "text-[10px] text-on-surface-variant";
      reason.textContent = `· ${m.status_reason}`;
      statusBar.appendChild(reason);
    }
    if (m.summary) {
      const s = document.createElement("span");
      s.className = "text-on-surface-variant truncate min-w-0";
      s.textContent = m.summary;
      statusBar.appendChild(s);
    }
    left.appendChild(statusBar);
    if (m.tags && m.tags.length) {
      const tagsRow = document.createElement("div");
      tagsRow.className = "flex flex-wrap gap-1 mt-1";
      m.tags.forEach((t) => {
        const tag = document.createElement("span");
        tag.className = "text-[10px] bg-surface-container-high text-on-surface-variant px-2.5 py-0.5 rounded-full";
        tag.textContent = t;
        tagsRow.appendChild(tag);
      });
      left.appendChild(tagsRow);
    }
    const effortRow = document.createElement("div");
    effortRow.className = "flex items-center gap-2 mt-2";
    const effortLabel = document.createElement("span");
    effortLabel.className = "text-[11px] text-on-surface-variant";
    effortLabel.textContent = "Effort (h)";
    const effortInput = document.createElement("input");
    effortInput.type = "number";
    effortInput.step = "0.1";
    effortInput.min = "0.1";
    effortInput.value = m.effort_hours ?? "";
    effortInput.className = "w-20 border border-outline rounded-lg bg-transparent text-on-surface px-2 py-1 text-[11px] focus:outline-none focus:ring-2 focus:ring-primary";
    const oldEffort = effortInput.value;
    effortInput.onchange = async () => {
      const v = Number(effortInput.value);
      if (effortInput.value === "" || Number.isNaN(v) || v < 0.1) {
        effortInput.value = oldEffort;
        return;
      }
      try {
        await api(`inbox/files/${m.id}/effort`, { method: "POST", body: { hours: v } });
        toast("Effort updated", "success");
        refreshFiles();
      } catch (err) {
        effortInput.value = oldEffort;
        toast("Failed: " + err.message, "error");
      }
    };
    effortRow.appendChild(effortLabel);
    effortRow.appendChild(effortInput);
    left.appendChild(effortRow);

    const right = document.createElement("div");
    right.className = "flex flex-col gap-2 items-end";

    let dismissAction = "duplicate";
    let dismissLabel = "Dismiss";
    if (m.status === "duplicate") { dismissAction = "new"; dismissLabel = "Undo duplicate"; }
    else if (m.status === "superseded") { dismissAction = "new"; dismissLabel = "Undo"; }
    const dismiss = document.createElement("button");
    dismiss.className = "text-[11px] text-on-surface-variant underline hover:text-error";
    dismiss.textContent = dismissLabel;
    dismiss.onclick = async () => {
      try {
        await api(`inbox/files/${m.id}/dismiss`, { method: "POST", body: { action: dismissAction } });
        toast("Updated", "success");
        refreshFiles();
      } catch (err) {
        toast("Failed: " + err.message, "error");
      }
    };

    const openBtn = document.createElement("button");
    openBtn.className = "text-[11px] font-medium text-primary underline";
    openBtn.textContent = "Open";
    openBtn.onclick = () => dialog(m.filename, m.summary || "No summary available.");

    right.appendChild(dismiss);

    if (goalSelected && m.status === "ready") {
      const attach = document.createElement("button");
      const sel = goalSelect.options[goalSelect.selectedIndex];
      attach.className = "text-[11px] font-medium text-primary underline";
      attach.textContent = `Attach to "${sel.textContent}"`;
      attach.onclick = async () => {
        try {
          await api(`inbox/files/${m.id}/attach`, { method: "POST", body: { goal_id: goalSelected } });
          toast("Attached", "success");
          refreshFiles();
        } catch (err) {
          toast("Attach failed: " + err.message, "error");
        }
      };
      right.appendChild(attach);
    }

    right.appendChild(openBtn);
    row.appendChild(left);
    row.appendChild(right);
    fileList.appendChild(row);
  });
}

let mergeMenuEl = null;
let mergeCloseListener = null;

function closeMergeMenu() {
  if (mergeCloseListener) {
    window.removeEventListener("click", mergeCloseListener);
    mergeCloseListener = null;
  }
  if (mergeMenuEl) {
    mergeMenuEl.remove();
    mergeMenuEl = null;
  }
}

function openMergeMenu(anchor, source) {
  closeMergeMenu();
  const menu = document.createElement("div");
  menu.className = "absolute z-20 mt-1 border border-outline-variant rounded-xl bg-surface-container-lowest shadow-lg overflow-hidden";
  const targets = topics.filter((t) => t.id !== source.id);
  if (targets.length === 0) return;
  targets.forEach((t) => {
    const item = document.createElement("button");
    item.className = "w-full text-left px-4 py-2 text-on-surface hover:bg-primary/10 text-[11px]";
    item.textContent = `Merge into "${t.name || "Unnamed"}" (${t.file_count ?? 0})`;
    item.onclick = async (e) => {
      e.stopPropagation();
      closeMergeMenu();
      try {
        await api("topics/merge", { method: "POST", body: { source_id: source.id, target_id: t.id } });
        toast("Topics merged", "success");
        refreshFiles();
      } catch (err) {
        toast("Merge failed: " + err.message, "error");
      }
    };
    menu.appendChild(item);
  });
  const rect = anchor.getBoundingClientRect();
  menu.style.left = rect.left + "px";
  menu.style.top = rect.bottom + 4 + "px";
  document.body.appendChild(menu);
  mergeMenuEl = menu;
  const closeOnOutside = (e) => {
    if (!menu.contains(e.target)) closeMergeMenu();
  };
  setTimeout(() => {
    window.addEventListener("click", closeOnOutside);
    mergeCloseListener = closeOnOutside;
  }, 0);
}

async function loadTopics() {
  try {
    topics = await api("topics");
    renderTopics();
  } catch {
    // topics endpoint may not exist yet; strip stays empty
  }
}

function renderTopics() {
  topicsStrip.innerHTML = "";
  topics.forEach((t) => {
    const pill = document.createElement("div");
    pill.className = "flex items-center gap-2 px-3 py-1 rounded-full border border-outline-variant bg-surface-container-lowest text-sm text-on-surface";
    const name = document.createElement("span");
    name.className = "truncate";
    name.style.maxWidth = "160px";
    name.textContent = `${t.name || "Unnamed"} (${t.file_count ?? 0})`;
    const members = (t.members || []).map((m) => m.filename || m.name || "").join("\n");
    name.title = members || "No files";
    pill.appendChild(name);

    const rename = document.createElement("button");
    rename.className = "text-[11px] text-on-surface-variant underline hover:text-primary";
    rename.textContent = "rename";
    rename.onclick = async () => {
      const value = await promptDialog("Rename topic", `New name for "${t.name || "Unnamed"}":`, t.name || "");
      if (value === null || value === t.name) return;
      try {
        await api(`topics/${t.id}/rename`, { method: "POST", body: { name: value } });
        toast("Topic renamed", "success");
        refreshFiles();
      } catch (err) {
        toast("Rename failed: " + err.message, "error");
      }
    };
    pill.appendChild(rename);

    const merge = document.createElement("button");
    merge.className = "text-[11px] text-on-surface-variant underline hover:text-primary ml-2";
    merge.textContent = "merge ▾";
    merge.onclick = (e) => {
      e.stopPropagation();
      openMergeMenu(merge, t);
    };
    pill.appendChild(merge);

    topicsStrip.appendChild(pill);
  });
}

async function init() {
  await loadGoals();
  await setBadgeFromSettings();
  await loadChatHistory();
  await loadTopics();
  await refreshFiles();
  initSettings(btnSettings);
  initTour("inbox");
}

init();
