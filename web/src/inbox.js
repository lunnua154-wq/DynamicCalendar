/** P3 — Inbox: chat + file drop zone + material list + topics. */

import "../tailwind.css";
import { api, toast, setText, initSettings } from "./common.js";

const chatList = document.getElementById("chat-list");
const chatInput = document.getElementById("chat-input");
const chatSend = document.getElementById("chat-send");
const llmBadge = document.getElementById("llm-badge");
const goalSelect = document.getElementById("goal-select");
const dropZone = document.getElementById("drop-zone");
const fileInput = document.getElementById("file-input");
const btnBrowse = document.getElementById("btn-browse");
const fileList = document.getElementById("file-list");
const topicsStrip = document.getElementById("topics-strip");
const btnSettings = document.getElementById("btn-settings");

let pollTimer = null;
let materials = []; // {id, filename, summary, tags, effort_hours, status, duplicate_of, topic_id}

function chip(text, kind) {
  const map = {
    ready: "bg-emerald-100 text-emerald-700",
    duplicate: "bg-amber-100 text-amber-700",
    superseded: "bg-slate-100 text-slate-500",
    error: "bg-rose-100 text-rose-700",
    processing: "bg-blue-100 text-blue-700 animate-pulse",
  };
  const el = document.createElement("span");
  el.className = `text-[10px] px-2 py-0.5 rounded-full ${map[kind] || "bg-slate-100 text-slate-500"}`;
  el.textContent = text;
  return el;
}

function renderChat() {
  // populated by appendChat
}

async function appendChat(role, content) {
  const div = document.createElement("div");
  div.className = `flex ${role === "user" ? "justify-end" : "justify-start"}`;
  const bubble = document.createElement("div");
  bubble.className = `max-w-[80%] p-3 rounded-2xl text-sm ${
    role === "user"
      ? "bg-blue-600 text-white rounded-tr-none"
      : "bg-slate-200 text-slate-800 rounded-tl-none"
  }`;
  bubble.textContent = content;
  div.appendChild(bubble);
  chatList.appendChild(div);
  chatList.scrollTop = chatList.scrollHeight;
}

async function loadChat() {
  try {
    const messages = await api("chat/messages"); // not an endpoint; we render from local
  } catch (e) {
    // ignore
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
    if (res.llm === "off") llmBadge.textContent = "(LLM off)";
    else llmBadge.textContent = "(LLM on)";
  } catch (err) {
    appendChat("assistant", "Failed: " + err.message);
  }
}

chatSend.onclick = sendChat;
chatInput.onkeydown = (e) => {
  if (e.key === "Enter") sendChat();
};

async function loadGoals() {
  try {
    const goals = await api("goals");
    goalSelect.innerHTML = '<option value="">Unattached</option>';
    goals.forEach((g) => {
      const o = document.createElement("option");
      o.value = g.id;
      o.textContent = g.title;
      goalSelect.appendChild(o);
    });
  } catch (err) {
    toast("Failed to load goals: " + err.message, "error");
  }
}

async function uploadFiles(files) {
  if (!files.length) return;
  const form = new FormData();
  for (const f of files) form.append("files", f);
  if (goalSelect.value) form.append("goal_id", goalSelect.value);
  try {
    await api("inbox/upload", { method: "POST", body: form });
    toast(`${files.length} file(s) uploaded`, "success");
    refreshFiles();
  } catch (err) {
    toast("Upload failed: " + err.message, "error");
  }
}

// drag & drop
["dragenter", "dragover"].forEach((evt) => {
  dropZone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropZone.classList.add("border-blue-500", "bg-blue-50");
  });
});
["dragleave", "drop"].forEach((evt) => {
  dropZone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropZone.classList.remove("border-blue-500", "bg-blue-50");
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
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }
}

function renderFiles() {
  fileList.innerHTML = "";
  if (materials.length === 0) {
    fileList.textContent = "No files yet. Drop some in.";
    return;
  }
  materials.forEach((m) => {
    const row = document.createElement("div");
    row.className = "flex items-start gap-3 p-3 rounded border border-slate-200 bg-white";
    const left = document.createElement("div");
    left.className = "flex-1 min-w-0";
    const name = document.createElement("div");
    name.className = "text-sm font-medium truncate";
    name.textContent = m.filename;
    left.appendChild(name);
    const statusBar = document.createElement("div");
    statusBar.className = "flex items-center gap-3 mt-1 text-xs";
    statusBar.appendChild(chip(m.status, m.status));
    if (m.summary) {
      const s = document.createElement("span");
      s.className = "text-slate-500 truncate";
      s.textContent = m.summary;
      statusBar.appendChild(s);
    }
    left.appendChild(statusBar);
    // tags
    if (m.tags && m.tags.length) {
      const tagsRow = document.createElement("div");
      tagsRow.className = "flex gap-1 mt-1";
      m.tags.forEach((t) => {
        const tag = document.createElement("span");
        tag.className = "text-[10px] bg-slate-100 text-slate-600 px-2 py-0.5 rounded";
        tag.textContent = t;
        tagsRow.appendChild(tag);
      });
      left.appendChild(tagsRow);
    }
    // effort
    const effortRow = document.createElement("div");
    effortRow.className = "flex items-center gap-2 mt-2";
    const effortLabel = document.createElement("span");
    effortLabel.className = "text-[11px] text-slate-500";
    effortLabel.textContent = "Effort (h)";
    const effortInput = document.createElement("input");
    effortInput.type = "number";
    effortInput.step = "0.1";
    effortInput.min = "0.1";
    effortInput.value = m.effort_hours ?? "";
    effortInput.className = "w-20 border border-slate-300 rounded px-1.5 py-1 text-[11px] focus:ring-2 focus:ring-blue-500 outline-none";
    effortInput.onchange = async () => {
      try {
        await api(`inbox/files/${m.id}/effort`, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: `hours=${encodeURIComponent(effortInput.value)}`,
        });
        toast("Effort updated", "success");
        refreshFiles();
      } catch (err) {
        toast("Failed: " + err.message, "error");
      }
    };
    effortRow.appendChild(effortLabel);
    effortRow.appendChild(effortInput);
    left.appendChild(effortRow);

    const right = document.createElement("div");
    right.className = "flex flex-col gap-2 items-end";
    const dismiss = document.createElement("button");
    dismiss.className = "text-[11px] underline text-slate-500 hover:text-rose-600";
    dismiss.textContent = m.status === "duplicate" ? "Undo duplicate" : "Dismiss";
    dismiss.onclick = async () => {
      const action = m.status === "duplicate" ? "new" : "duplicate";
      try {
        await api(`inbox/files/${m.id}/dismiss`, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: `action=${encodeURIComponent(action)}`,
        });
        toast("Updated", "success");
        refreshFiles();
      } catch (err) {
        toast("Failed: " + err.message, "error");
      }
    };
    const openBtn = document.createElement("button");
    openBtn.className = "text-[11px] text-blue-600 underline hover:underline";
    openBtn.textContent = "Open";
    right.appendChild(dismiss);
    right.appendChild(openBtn);
    row.appendChild(left);
    row.appendChild(right);
    fileList.appendChild(row);
  });
}

function renderTopics() {
  topicsStrip.innerHTML = "";
  const byTopic = {};
  materials.forEach((m) => {
    const key = m.topic_id || "unattached";
    if (!byTopic[key]) byTopic[key] = { name: (m.tags && m.tags[0]) || "Unattached", count: 0, id: m.topic_id };
    byTopic[key].count += 1;
  });
  Object.values(byTopic).forEach((t) => {
    const pill = document.createElement("div");
    pill.className = "flex items-center gap-2 px-3 py-1 rounded border border-slate-200 bg-white text-sm";
    const name = document.createElement("span");
    name.textContent = `${t.name} (${t.count})`;
    pill.appendChild(name);
    topicsStrip.appendChild(pill);
  });
}

async function init() {
  await loadGoals();
  await refreshFiles();
  initSettings(btnSettings);
}

init();
