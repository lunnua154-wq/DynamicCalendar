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

/** Prompt dialog: title + label + a single text input. Resolves with the
 *  entered string, or null when cancelled / dismissed. User text via textContent. */
export function promptDialog(title, label, value = "") {
  return new Promise((resolve) => {
    const backdrop = document.createElement("div");
    backdrop.className = "fixed inset-0 z-[60] bg-black/60 backdrop-blur-sm flex items-center justify-center";
    const card = document.createElement("div");
    card.className = "bg-white rounded-xl p-6 shadow-2xl w-96 text-sm";
    card.innerHTML = "";
    const h = document.createElement("h3");
    h.className = "font-semibold mb-2";
    h.textContent = title;
    card.appendChild(h);
    const p = document.createElement("p");
    p.className = "text-slate-600 mb-3";
    p.textContent = label;
    card.appendChild(p);
    const input = document.createElement("input");
    input.type = "text";
    input.value = value;
    input.className = "w-full border border-slate-300 rounded px-2 py-1.5 text-sm focus:ring-2 focus:ring-blue-500 outline-none mb-4";
    card.appendChild(input);
    const btns = document.createElement("div");
    btns.className = "flex justify-end gap-2";
    const ok = document.createElement("button");
    ok.className = "px-3 py-1.5 rounded bg-blue-600 text-white text-sm";
    ok.textContent = "Save";
    const cancel = document.createElement("button");
    cancel.className = "px-3 py-1.5 rounded bg-slate-200 text-slate-700 text-sm";
    cancel.textContent = "Cancel";
    btns.appendChild(cancel);
    btns.appendChild(ok);
    card.appendChild(btns);
    backdrop.appendChild(card);
    document.body.appendChild(backdrop);
    const done = (val) => {
      backdrop.remove();
      resolve(val);
    };
    ok.onclick = () => done(input.value);
    cancel.onclick = () => done(null);
    backdrop.onclick = (e) => {
      if (e.target === backdrop) done(null);
    };
    const onkey = (e) => {
      if (e.key === "Escape") { window.removeEventListener("keydown", onkey); done(null); }
      if (e.key === "Enter") { window.removeEventListener("keydown", onkey); done(input.value); }
    };
    window.addEventListener("keydown", onkey);
    input.focus();
  });
}

export async function api(path, options = {}) {
  const body = options.body;
  const isFormData = body instanceof FormData;
  const headers = { ...options.headers };
  // JSON bodies: serialise + declare content type. FormData: leave content-type
  // alone so the browser attaches the multipart boundary, and never
  // JSON-stringify it.
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
        : data && data.detail
        ? typeof data.detail === "string"
          ? data.detail
          : JSON.stringify(data.detail)
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

export function parseApiDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
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

export function formatHours(hours) {
  const h = Number(hours);
  if (!Number.isFinite(h)) return "0h";
  const s = h.toFixed(1);
  return s.replace(/\.0$/, "") + "h";
}

// --------------------------------------------------------------------------- settings panel (shared)
//
// Builds a modal once per page and toggles it from the ⚙ settings button.
// LLM is "on" only when both base URL and model are set; otherwise the app
// runs offline with deterministic fallbacks (AGENTS.md #7).
// Editable keys: llm_base_url, llm_model, llm_api_key,
// daily_capacity_hours (0.5-12), slot_minutes (15-120),
// default_effort_hours (0.1-20),
// ocr_provider (off|cloudflare), cf_account_id, cf_api_token, ocr_model.

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
  const fields = [
    { key: "llm_base_url", label: "LLM base URL", type: "url", placeholder: "http://localhost:11434/v1" },
    { key: "llm_model", label: "Model", type: "text", placeholder: "e.g. gpt-4o-mini" },
    { key: "llm_api_key", label: "API key", type: "password", placeholder: "sk-…" },
    { key: "daily_capacity_hours", label: "Daily capacity (hours)", type: "number", min: "0.5", max: "12", step: "0.5", placeholder: "0.5 – 12" },
    { key: "slot_minutes", label: "Slot length (minutes)", type: "number", min: "15", max: "120", step: "15", placeholder: "15 – 120" },
    { key: "default_effort_hours", label: "Default effort (hours)", type: "number", min: "0.1", max: "20", step: "0.1", placeholder: "0.1 – 20" },
    { key: "ocr_provider", label: "OCR provider", type: "select", placeholder: "off | cloudflare", options: ["off", "cloudflare"] },
    { key: "cf_account_id", label: "Cloudflare account ID", type: "text", placeholder: "account id" },
    { key: "cf_api_token", label: "Cloudflare API token", type: "password", placeholder: "token" },
    { key: "ocr_model", label: "OCR model", type: "text", placeholder: "@cf/..." },
  ];

  const numberKeys = new Set(["daily_capacity_hours", "slot_minutes", "default_effort_hours"]);
  const range = {
    daily_capacity_hours: [0.5, 12],
    slot_minutes: [15, 120],
    default_effort_hours: [0.1, 20],
  };

  let loading = false;

  function validate(payload) {
    for (const [key, [lo, hi]] of Object.entries(range)) {
      if (!(key in payload)) continue;
      const v = Number(payload[key]);
      if (!Number.isFinite(v) || v < lo || v > hi) {
        return `${specLabel(key)} must be between ${lo} and ${hi}`;
      }
    }
    return null;
  }

  function specLabel(key) {
    const f = fields.find((x) => x.key === key);
    return f ? f.label : key;
  }

  async function load() {
    try {
      const rows = await api("settings");
      const values = {};
      rows.forEach((r) => (values[r.key] = r.value));
      fields.forEach((spec) => {
        const el = document.getElementById(`st-${spec.key}`);
        if (!el) return;
        const raw = values[spec.key];
        if (spec.type === "select") {
          el.value = raw == null ? "" : String(raw);
        } else {
          el.value = raw == null ? "" : Array.isArray(raw) ? JSON.stringify(raw) : String(raw);
        }
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

  async function saveSettings() {
    if (loading) return;
    const payload = {};
    fields.forEach((spec) => {
      const el = document.getElementById(`st-${spec.key}`);
      if (!el) return;
      let value = el.value;
      if (spec.type === "number") {
        if (value === "") return;
        value = Number(value);
      }
      if (value === "" || value === null || value === undefined) return;
      payload[spec.key] = value;
    });
    if (Object.keys(payload).length === 0) {
      closeModal();
      return;
    }
    const err = validate(payload);
    if (err) {
      toast(err, "error");
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
      const all = {};
      fields.forEach((spec) => {
        const el = document.getElementById(`st-${spec.key}`);
        all[spec.key] = el ? el.value : "";
      });
      setStatus(all);
      closeModal();
    } catch (err) {
      // No half-saving: the PUT either succeeded (toast above) or failed and
      // nothing was persisted. Surface the server's validation message.
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
  title.textContent = "Settings";
  card.appendChild(title);
  const form = document.createElement("div");
  form.className = "space-y-3";
  fields.forEach((spec) => {
    const wrap = document.createElement("div");
    const label = document.createElement("label");
    label.className = "block text-xs text-slate-500 mb-1";
    label.textContent = spec.label;
    let input;
    if (spec.type === "select") {
      input = document.createElement("select");
      input.className =
        "w-full border border-slate-300 rounded px-2 py-1.5 text-sm focus:ring-2 focus:ring-blue-500 outline-none bg-white";
      (spec.options || []).forEach((opt) => {
        const o = document.createElement("option");
        o.value = opt;
        o.textContent = opt;
        input.appendChild(o);
      });
    } else {
      input = document.createElement("input");
      input.type = spec.type;
      input.placeholder = spec.placeholder;
      input.className =
        "w-full border border-slate-300 rounded px-2 py-1.5 text-sm focus:ring-2 focus:ring-blue-500 outline-none";
      if (spec.min !== undefined) input.min = spec.min;
      if (spec.max !== undefined) input.max = spec.max;
      if (spec.step !== undefined) input.step = spec.step;
    }
    input.id = `st-${spec.key}`;
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
  save.onclick = saveSettings;
  modal.onkeydown = (e) => {
    if (e.key === "Escape") closeModal();
  };
}
