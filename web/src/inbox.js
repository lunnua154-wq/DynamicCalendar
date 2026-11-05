/** P3 — Inbox: chat, file drop pipeline, topics strip. */

import "../tailwind.css";
import { api, el, fmtDay, hide, initNav, show, toast } from "./common.js";

const chatList = document.getElementById("chat-list");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const llmBadge = document.getElementById("llm-badge");
const goalSelect = document.getElementById("goal-select");
const dropZone = document.getElementById("drop-zone");
const fileInput = document.getElementById("file-input");
const fileList = document.getElementById("file-list");
const topicsStrip = document.getElementById("topics-strip");
const materialModal = document.getElementById("material-modal");
const materialTitle = document.getElementById("material-title");
const materialBody = document.getElementById("material-body");

let materials = [];
let topics = [];
let goals = [];
let pollTimer = null;
let mergeSource = null;

const STATUS_STYLE = {
  processing: "bg-sky-100 text-sky-700 animate-pulse",
  ready: "bg-emerald-100 text-emerald-700",
  duplicate: "bg-amber-100 text-amber-700",
  superseded: "bg-slate-200 text-slate-600",
  error: "bg-rose-100 text-rose-700",
};

// ------------------------------------------------------------------ chat

function appendChat(role, content) {
  const row = el("div", `flex ${role === "user" ? "justify-end" : "justify-start"}`);
  const bubble = el(
    "div",
    `max-w-[85%] px-3 py-2 rounded-2xl text-sm whitespace-pre-line ${
      role === "user"
        ? "bg-blue-600 text-white rounded-br-none"
        : "bg-slate-100 text-slate-800 rounded-bl-none"
    }`,
    content
  );
  row.appendChild(bubble);
  chatList.appendChild(row);
  chatList.scrollTop = chatList.scrollHeight;
}

async function loadChat() {
  try {
    const history = await api("chat");
    chatList.innerHTML = "";
    if (!history.length) {
      appendChat(
        "assistant",
        "Hi — this is the offline assistant. Ask \"what's due this week?\" or drop study files on the right."
      );
    }
    history.forEach((message) => appendChat(message.role, message.content));
  } catch (error) {
    toast(`Failed to load chat: ${error.message}`, "error");
  }
}

chatForm.onsubmit = async (event) => {
  event.preventDefault();
  const text = chatInput.value.trim();
  if (!text) return;
  chatInput.value = "";
  appendChat("user", text);
  try {
    const result = await api("chat", { method: "POST", body: { message: text } });
    appendChat("assistant", result.reply);
    llmBadge.textContent = result.llm === "on" ? "(LLM on)" : "(LLM off)";
  } catch (error) {
    appendChat("assistant", `That failed: ${error.message}`);
  }
};

// ------------------------------------------------------------------ goals

async function loadGoals() {
  try {
    goals = await api("goals");
    const previous = goalSelect.value;
    goalSelect.innerHTML = "";
    const unattached = el("option", "", "Unattached");
    unattached.value = "";
    goalSelect.appendChild(unattached);
    goals
      .filter((goal) => goal.status === "open")
      .forEach((goal) => {
        const option = el("option", "", `${goal.title} — due ${fmtDay(goal.deadline)}`);
        option.value = goal.id;
        goalSelect.appendChild(option);
      });
    if (previous && [...goalSelect.options].some((option) => option.value === previous)) {
      goalSelect.value = previous;
    }
  } catch (error) {
    toast(`Failed to load goals: ${error.message}`, "error");
  }
}

// ------------------------------------------------------------------ upload

function uploadFiles(fileListLike) {
  const files = Array.from(fileListLike || []);
  if (!files.length) return;
  const form = new FormData();
  files.forEach((file) => form.append("files[]", file, file.name));
  if (goalSelect.value) form.append("goal_id", goalSelect.value);

  files.forEach((file) => renderPendingRow(file.name));
  api("inbox/upload", { method: "POST", body: form })
    .then((results) => {
      const duplicates = results.filter((result) => result.status === "duplicate").length;
      const fresh = results.length - duplicates;
      toast(
        duplicates
          ? `${fresh} file(s) accepted, ${duplicates} already known`
          : `${fresh} file(s) accepted`,
        "success"
      );
      return refreshMaterials();
    })
    .catch((error) => toast(`Upload failed: ${error.message}`, "error"));
}

function renderPendingRow(name) {
  const row = el("div", "flex items-center gap-3 p-3 rounded-lg border border-slate-200 bg-white");
  row.appendChild(el("span", "text-sm truncate flex-1", name));
  row.appendChild(el("span", "text-[10px] px-2 py-0.5 rounded-full bg-sky-100 text-sky-700 animate-pulse", "processing"));
  fileList.prepend(row);
}

["dragenter", "dragover"].forEach((type) =>
  dropZone.addEventListener(type, (event) => {
    event.preventDefault();
    dropZone.classList.add("border-blue-500", "bg-blue-50");
  })
);
["dragleave", "drop"].forEach((type) =>
  dropZone.addEventListener(type, (event) => {
    event.preventDefault();
    dropZone.classList.remove("border-blue-500", "bg-blue-50");
  })
);
dropZone.addEventListener("drop", (event) => uploadFiles(event.dataTransfer.files));
dropZone.addEventListener("click", () => fileInput.click());
fileInput.onchange = () => {
  uploadFiles(fileInput.files);
  fileInput.value = "";
};

// ------------------------------------------------------------------ materials

async function refreshMaterials() {
  try {
    materials = await api("inbox/files");
    topics = await api("topics");
    renderMaterials();
    renderTopics();
    schedulePolling();
  } catch (error) {
    toast(`Failed to load files: ${error.message}`, "error");
  }
}

function schedulePolling() {
  const processing = materials.some((material) => material.status === "processing");
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (processing) {
    pollTimer = setInterval(async () => {
      try {
        materials = await api("inbox/files");
        topics = await api("topics");
        renderMaterials();
        renderTopics();
        if (!materials.some((material) => material.status === "processing")) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
      } catch {
        /* keep polling */
      }
    }, 2000);
  }
}

function resultLine(material) {
  if (material.status === "duplicate" && material.duplicate_of) {
    const original = materials.find((item) => item.id === material.duplicate_of);
    return `duplicate of ${original ? original.filename : "an earlier file"}`;
  }
  if (material.status === "superseded" && material.superseded_by) {
    const newer = materials.find((item) => item.id === material.superseded_by);
    return newer ? `superseded by ${newer.filename}` : "superseded by a newer version";
  }
  return material.result || "";
}

function renderMaterials() {
  fileList.innerHTML = "";
  if (!materials.length) {
    fileList.appendChild(el("p", "text-sm text-slate-400", "No files yet — drop something in."));
    return;
  }
  materials.forEach((material) => {
    const row = el("div", "flex items-start gap-3 p-3 rounded-lg border border-slate-200 bg-white");
    const left = el("div", "flex-1 min-w-0");

    const titleRow = el("div", "flex items-center gap-2");
    titleRow.appendChild(el("span", "text-sm font-medium truncate", material.filename));
    titleRow.appendChild(
      el(
        "span",
        `text-[10px] px-2 py-0.5 rounded-full shrink-0 ${
          STATUS_STYLE[material.status] || "bg-slate-100 text-slate-600"
        }`,
        material.status
      )
    );
    left.appendChild(titleRow);
    left.appendChild(el("div", "text-xs text-slate-500 mt-0.5", resultLine(material) || material.summary || ""));

    // For a ready row the result line already names the topic; the extra line
    // is only useful once the row means something else (duplicate/superseded).
    if (material.topic_name && material.status !== "ready") {
      left.appendChild(el("div", "text-[11px] text-slate-400 mt-0.5", `topic: ${material.topic_name}`));
    }
    if (material.tags?.length) {
      const tags = el("div", "flex flex-wrap gap-1 mt-1");
      material.tags.forEach((tag) => {
        tags.appendChild(el("span", "text-[10px] bg-slate-100 text-slate-600 px-2 py-0.5 rounded", tag));
      });
      left.appendChild(tags);
    }

    const controls = el("div", "flex items-center gap-2 mt-2");
    const effortWrap = el("label", "flex items-center gap-1 text-[11px] text-slate-500");
    effortWrap.appendChild(document.createTextNode("effort (h)"));
    const effortInput = document.createElement("input");
    effortInput.type = "number";
    effortInput.min = "0.1";
    effortInput.max = "20";
    effortInput.step = "0.1";
    effortInput.value = material.effort_hours ?? "";
    effortInput.disabled = material.status !== "ready";
    effortInput.className =
      "w-16 border border-slate-300 rounded px-1.5 py-0.5 text-[11px] text-right focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-slate-100";
    effortInput.onchange = async () => {
      try {
        await api(`inbox/files/${material.id}/effort`, {
          method: "POST",
          body: { hours: Number(effortInput.value) },
        });
        toast("Effort updated — plan recomputed", "success");
        await refreshMaterials();
      } catch (error) {
        effortInput.value = material.effort_hours ?? "";
        toast(error.message, "error");
      }
    };
    effortWrap.appendChild(effortInput);
    controls.appendChild(effortWrap);

    const attachSelect = document.createElement("select");
    attachSelect.className =
      "text-[11px] border border-slate-300 rounded px-1.5 py-0.5 bg-white max-w-[9rem]";
    attachSelect.appendChild(el("option", "", "Unattached"));
    attachSelect.firstChild.value = "";
    goals.forEach((goal) => {
      const option = el("option", "", goal.title);
      option.value = goal.id;
      attachSelect.appendChild(option);
    });
    attachSelect.value = material.goal_id || "";
    attachSelect.onchange = async () => {
      try {
        await api(`inbox/files/${material.id}/attach`, {
          method: "POST",
          body: { goal_id: attachSelect.value || null },
        });
        toast("Material attached — plan recomputed", "success");
        await refreshMaterials();
      } catch (error) {
        attachSelect.value = material.goal_id || "";
        toast(error.message, "error");
      }
    };
    controls.appendChild(attachSelect);
    left.appendChild(controls);

    const actions = el("div", "flex flex-col items-end gap-1 shrink-0");
    const open = el("button", "text-[11px] text-blue-600 hover:underline", "Open");
    open.onclick = () => openMaterial(material.id);
    actions.appendChild(open);

    if (material.status === "ready") {
      const dup = el("button", "text-[11px] text-slate-500 hover:underline", "Mark duplicate");
      dup.onclick = () => dismiss(material.id, "duplicate");
      const sup = el("button", "text-[11px] text-slate-500 hover:underline", "Mark superseded");
      sup.onclick = () => dismiss(material.id, "superseded");
      actions.append(dup, sup);
    } else if (material.status === "duplicate" || material.status === "superseded") {
      const restore = el("button", "text-[11px] text-slate-500 hover:underline", "Restore");
      restore.onclick = () => dismiss(material.id, "ready");
      actions.appendChild(restore);
    }

    row.append(left, actions);
    fileList.appendChild(row);
  });
}

async function dismiss(materialId, action) {
  try {
    await api(`inbox/files/${materialId}/dismiss`, { method: "POST", body: { action } });
    toast("Material updated — plan recomputed", "success");
    await refreshMaterials();
  } catch (error) {
    toast(error.message, "error");
  }
}

// ------------------------------------------------------------------ material modal

async function openMaterial(materialId) {
  try {
    const material = await api(`materials/${materialId}`);
    materialTitle.textContent = material.filename;
    materialBody.textContent = material.markdown || "(no markdown stored)";
    show(materialModal);
    materialModal.classList.add("flex");
  } catch (error) {
    toast(error.message, "error");
  }
}

function closeMaterial() {
  hide(materialModal);
  materialModal.classList.remove("flex");
}

document.getElementById("material-close").onclick = closeMaterial;
materialModal.addEventListener("click", (event) => {
  if (event.target === materialModal) closeMaterial();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeMaterial();
});

// ------------------------------------------------------------------ topics

function renderTopics() {
  topicsStrip.innerHTML = "";
  if (!topics.length) {
    topicsStrip.appendChild(el("p", "text-sm text-slate-400", "Topics appear once files are tagged."));
    return;
  }
  if (mergeSource) {
    topicsStrip.appendChild(
      el(
        "p",
        "text-xs text-slate-500 w-full",
        `Merge "${mergeSource.name}" into:`
      )
    );
    topics
      .filter((topic) => topic.id !== mergeSource.id)
      .forEach((topic) => {
        const button = el(
          "button",
          "px-3 py-1 rounded-full border border-blue-300 bg-blue-50 text-blue-700 text-sm hover:bg-blue-100",
          topic.name
        );
        button.onclick = async () => {
          try {
            await api(`topics/${mergeSource.id}/merge`, {
              method: "POST",
              body: { target_id: topic.id },
            });
            toast("Topics merged", "success");
            mergeSource = null;
            await refreshMaterials();
          } catch (error) {
            toast(error.message, "error");
          }
        };
        topicsStrip.appendChild(button);
      });
    const cancel = el("button", "px-3 py-1 rounded-full border border-slate-300 text-sm text-slate-600", "Cancel");
    cancel.onclick = () => {
      mergeSource = null;
      renderTopics();
    };
    topicsStrip.appendChild(cancel);
    return;
  }

  topics.forEach((topic) => {
    const chip = el("div", "flex items-center gap-2 px-3 py-1 rounded-full border border-slate-200 bg-white text-sm");
    const label = el("span", "", `${topic.name} (${topic.file_count})`);
    chip.appendChild(label);

    const rename = el("button", "text-[11px] text-slate-400 hover:text-blue-600", "rename");
    rename.onclick = () => {
      const input = document.createElement("input");
      input.value = topic.name;
      input.className = "text-sm border border-blue-400 rounded px-1 py-0.5 w-32";
      chip.replaceChild(input, label);
      input.focus();
      const save = async () => {
        const name = input.value.trim();
        if (!name || name === topic.name) {
          await refreshMaterials();
          return;
        }
        try {
          await api(`topics/${topic.id}/rename`, { method: "POST", body: { name } });
          toast("Topic renamed", "success");
          await refreshMaterials();
        } catch (error) {
          toast(error.message, "error");
          await refreshMaterials();
        }
      };
      input.onblur = save;
      input.onkeydown = (event) => {
        if (event.key === "Enter") input.blur();
        if (event.key === "Escape") refreshMaterials();
      };
    };
    chip.appendChild(rename);

    if (topics.length > 1) {
      const merge = el("button", "text-[11px] text-slate-400 hover:text-blue-600", "merge");
      merge.onclick = () => {
        mergeSource = topic;
        renderTopics();
      };
      chip.appendChild(merge);
    }
    topicsStrip.appendChild(chip);
  });
}

// ------------------------------------------------------------------ boot

async function init() {
  initNav({ active: "Inbox", onDataChanged: refreshMaterials });
  await loadGoals();
  await loadChat();
  await refreshMaterials();
  const requested = new URLSearchParams(window.location.search).get("material");
  if (requested) openMaterial(requested);
}

init();
