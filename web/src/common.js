/** Shared API client + small render helpers. ES2022, no framework. */

const API_BASE = "/api";

let toastContainer;

function ensureToastContainer() {
  if (toastContainer) return toastContainer;
  toastContainer = document.createElement("div");
  toastContainer.className =
    "fixed top-4 right-4 z-50 flex flex-col gap-2 pointer-events-none";
  document.body.appendChild(toastContainer);
  return toastContainer;
}

export function toast(message, kind = "info") {
  ensureToastContainer();
  const el = document.createElement("div");
  const base = "px-4 py-3 rounded shadow-lg text-sm flex items-center gap-3 animate-in fade-in slide-in-from-top-2 duration-300";
  const kindMap = {
    info: "bg-blue-500/90 text-white",
    success: "bg-emerald-500/90 text-white",
    error: "bg-rose-500/90 text-white",
  };
  el.className = base + " " + kindMap[kind];
  el.textContent = String(message);
  toastContainer.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 300);
  }, 4000);
}

export async function dialog(title, message) {
  return new Promise((resolve) => {
    const backdrop = document.createElement("div");
    backdrop.className = "fixed inset-0 z-[60] bg-black/60 backdrop-blur-sm flex items-center justify-center";
    const card = document.createElement("div");
    card.className = "bg-white rounded-xl p-6 shadow-2xl w-96 text-sm";
    card.innerHTML = ""; // cleared below; we use textContent for user strings
    const h = document.createElement("h3");
    h.className = "font-semibold mb-2";
    h.textContent = title;
    const p = document.createElement("p");
    p.className = "text-slate-600 mb-4";
    p.textContent = message;
    const btns = document.createElement("div");
    btns.className = "flex justify-end gap-2";
    const ok = document.createElement("button");
    ok.className = "px-3 py-1.5 rounded bg-blue-600 text-white text-sm";
    ok.textContent = "OK";
    const cancel = document.createElement("button");
    cancel.className = "px-3 py-1.5 rounded bg-slate-200 text-slate-700 text-sm";
    cancel.textContent = "Cancel";
    btns.appendChild(cancel);
    btns.appendChild(ok);
    card.appendChild(h);
    card.appendChild(p);
    card.appendChild(btns);
    backdrop.appendChild(card);
    document.body.appendChild(backdrop);
    const done = (val) => {
      backdrop.remove();
      resolve(val);
    };
    ok.onclick = () => done(true);
    cancel.onclick = () => done(false);
    backdrop.onclick = (e) => {
      if (e.target === backdrop) done(false);
    };
  });
}

export async function api(path, options = {}) {
  const body = options.body;
  const isFormData = body instanceof FormData;
  const headers = { ...options.headers };
  // JSON bodies: serialise + declare content type (unless already set, e.g.
  // application/x-www-form-urlencoded). FormData: leave content-type alone so
  // the browser attaches the multipart boundary, and never JSON-stringify it.
  if (!isFormData && !headers["Content-Type"] && body !== null && typeof body === "object") {
    headers["Content-Type"] = "application/json";
  }
  const init = { ...options, headers };
  if (!isFormData && init.body && typeof init.body === "object") {
    init.body = JSON.stringify(init.body);
  }
  const res = await fetch(`${API_BASE}/${path}`, init);
  let data;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    data = await res.json();
  } else {
    data = await res.text();
  }
  if (!res.ok) {
    const msg =
      data && data.error
        ? data.error
        : typeof data === "string"
        ? data
        : `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

export function utcTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function utcDay(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

export function escapeHtml(unsafe) {
  return String(unsafe)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

export function setText(el, text) {
  el.textContent = String(text ?? "");
}

// --------------------------------------------------------------------------- settings panel (M2: LLM endpoint config)
//
// Builds a modal once per page and toggles it from the ⚙ settings button.
// LLM is "on" only when both base URL and model are set; otherwise the app
// runs offline with deterministic fallbacks (AGENTS.md #7).

export function initSettings(btnEl) {
  if (!btnEl) return;
  let modal = document.getElementById("settings-modal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "settings-modal";
    modal.className =
      "hidden fixed inset-0 z-[70] bg-black/60 backdrop-blur-sm flex items-center justify-center";
    const backdrop = document.createElement("div");
    backdrop.className = "absolute inset-0";
    const card = document.createElement("div");
    card.className =
      "bg-white rounded-xl shadow-2xl border border-slate-200 w-96 p-6 max-w-md mx-4";
    modal.appendChild(backdrop);
    modal.appendChild(card);
    document.body.appendChild(modal);
    backdrop.onclick = () => closeModal();
  }
  const card = modal.children[1];
  const fields = {
    llm_base_url: { label: "LLM base URL", type: "url", placeholder: "http://localhost:11434/v1" },
    llm_model: { label: "Model", type: "text", placeholder: "e.g. gpt-4o-mini" },
    llm_api_key: { label: "API key", type: "password", placeholder: "sk-…" },
  };

  let loading = false;

  async function load() {
    try {
      const rows = await api("settings");
      const values = {};
      rows.forEach((r) => (values[r.key] = r.value));
      Object.entries(fields).forEach(([key, spec]) => {
        const el = document.getElementById(`st-${key}`);
        if (el) el.value = values[key] || "";
      });
      setStatus(values);
    } catch (err) {
      toast("Failed to load settings: " + err.message, "error");
    }
  }

  function setStatus(values) {
    const enabled = Boolean(values.llm_base_url && values.llm_model);
    const status = document.getElementById("st-status");
    status.textContent = `LLM: ${enabled ? "on" : "off"} ${enabled ? "" : "(offline mode)"}`;
    status.className = `text-xs mb-4 ${enabled ? "text-emerald-700" : "text-slate-500"}`;
  }

  function openModal() {
    load();
    modal.classList.remove("hidden");
    btnEl.setAttribute("aria-expanded", "true");
  }

  function closeModal() {
    modal.classList.add("hidden");
    btnEl.setAttribute("aria-expanded", "false");
  }

  async function save() {
    if (loading) return;
    const payload = {};
    Object.keys(fields).forEach((key) => {
      const el = document.getElementById(`st-${key}`);
      if (el && el.value !== "") payload[key] = el.value;
    });
    if (Object.keys(payload).length === 0) {
      closeModal();
      return;
    }
    loading = true;
    const saveBtn = document.getElementById("st-save");
    const saveText = saveBtn.textContent;
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving…";
    try {
      await api("settings", { method: "PUT", body: payload });
      toast("Settings saved", "success");
      setStatus(payload);
      closeModal();
    } catch (err) {
      toast("Failed to save: " + err.message, "error");
    } finally {
      loading = false;
      saveBtn.disabled = false;
      saveBtn.textContent = saveText;
    }
  }

  // render form
  card.innerHTML = "";
  const title = document.createElement("h3");
  title.className = "font-semibold mb-4";
  title.textContent = "Settings · LLM endpoint";
  card.appendChild(title);
  const form = document.createElement("div");
  form.className = "space-y-3";
  Object.entries(fields).forEach(([key, spec]) => {
    const wrap = document.createElement("div");
    const label = document.createElement("label");
    label.className = "block text-xs text-slate-500 mb-1";
    label.textContent = spec.label;
    const input = document.createElement("input");
    input.id = `st-${key}`;
    input.type = spec.type;
    input.placeholder = spec.placeholder;
    input.className =
      "w-full border border-slate-300 rounded px-2 py-1.5 text-sm focus:ring-2 focus:ring-blue-500 outline-none";
    wrap.appendChild(label);
    wrap.appendChild(input);
    form.appendChild(wrap);
  });
  const status = document.createElement("p");
  status.id = "st-status";
  status.className = "text-xs mb-4 text-slate-500";
  status.textContent = "Loading…";
  card.appendChild(form);
  card.appendChild(status);
  const btns = document.createElement("div");
  btns.className = "flex justify-end gap-2 pt-3 border-t border-slate-100";
  const cancel = document.createElement("button");
  cancel.id = "st-cancel";
  cancel.className = "px-3 py-1.5 rounded border border-slate-300 text-sm hover:bg-slate-50";
  cancel.textContent = "Cancel";
  const save = document.createElement("button");
  save.id = "st-save";
  save.className = "px-3 py-1.5 rounded bg-blue-600 text-white text-sm hover:bg-blue-700";
  save.textContent = "Save";
  btns.appendChild(cancel);
  btns.appendChild(save);
  card.appendChild(btns);

  btnEl.onclick = openModal;
  cancel.onclick = closeModal;
  save.onclick = save;
  modal.onkeydown = (e) => {
    if (e.key === "Escape") closeModal();
  };
}
