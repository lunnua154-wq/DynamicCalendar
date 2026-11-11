/** Shared API client + small render helpers. ES2022, no framework. */

const API_BASE = "/api";

let toastContainer;

function ensureToastContainer() {
  if (toastContainer) return toastContainer;
  toastContainer = document.createElement("div");
  toastContainer.className =
    "fixed top-20 right-4 z-50 flex flex-col gap-2 items-end pointer-events-none";
  document.body.appendChild(toastContainer);
  return toastContainer;
}

const TOAST_ICONS = { info: "ⓘ", success: "✓", error: "⚠" };

export function toast(message, kind = "info") {
  ensureToastContainer();
  const el = document.createElement("div");
  // Material 3 snackbar: single inverse-surface container, one short message,
  // kind conveyed by a leading glyph rather than a saturated background.
  // max-w + break-words: an unbounded toast with a long server message used to
  // stretch across the whole viewport (covering the header and the Saturday
  // column) and get clipped on the left.
  el.className =
    "px-4 py-3 rounded-md shadow-lg text-sm flex items-center gap-3 max-w-sm break-words bg-inverse-surface text-inverse-on-surface";
  const icon = document.createElement("span");
  icon.setAttribute("aria-hidden", "true");
  icon.className = "shrink-0 text-inverse-primary";
  icon.textContent = TOAST_ICONS[kind] || TOAST_ICONS.info;
  const text = document.createElement("span");
  text.textContent = String(message);
  el.appendChild(icon);
  el.appendChild(text);
  toastContainer.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 300);
  }, 4000);
}

export async function dialog(title, message) {
  return new Promise((resolve) => {
    const backdrop = document.createElement("div");
    backdrop.className = "fixed inset-0 z-[60] bg-on-surface/40 flex items-center justify-center";
    const card = document.createElement("div");
    card.className = "bg-surface-container-high text-on-surface rounded-[28px] p-6 shadow-xl w-96 text-sm";
    card.innerHTML = ""; // cleared below; we use textContent for user strings
    const h = document.createElement("h3");
    h.className = "text-lg font-medium mb-2 text-on-surface";
    h.textContent = title;
    const p = document.createElement("p");
    p.className = "text-on-surface-variant mb-5";
    p.textContent = message;
    const btns = document.createElement("div");
    btns.className = "flex justify-end gap-2";
    const ok = document.createElement("button");
    ok.className = "px-5 py-2 rounded-full bg-primary text-on-primary text-sm font-medium hover:shadow-md";
    ok.textContent = "OK";
    const cancel = document.createElement("button");
    cancel.className = "px-4 py-2 rounded-full text-primary text-sm font-medium hover:bg-primary/10";
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
    backdrop.className = "fixed inset-0 z-[60] bg-on-surface/40 flex items-center justify-center";
    const card = document.createElement("div");
    card.className = "bg-surface-container-high text-on-surface rounded-[28px] p-6 shadow-xl w-96 text-sm";
    card.innerHTML = "";
    const h = document.createElement("h3");
    h.className = "text-lg font-medium mb-2 text-on-surface";
    h.textContent = title;
    card.appendChild(h);
    const p = document.createElement("p");
    p.className = "text-on-surface-variant mb-3";
    p.textContent = label;
    card.appendChild(p);
    const input = document.createElement("input");
    input.type = "text";
    input.value = value;
    input.className = "w-full border border-outline rounded-lg bg-transparent text-on-surface px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary mb-5";
    card.appendChild(input);
    const btns = document.createElement("div");
    btns.className = "flex justify-end gap-2";
    const ok = document.createElement("button");
    ok.className = "px-5 py-2 rounded-full bg-primary text-on-primary text-sm font-medium hover:shadow-md";
    ok.textContent = "Save";
    const cancel = document.createElement("button");
    cancel.className = "px-4 py-2 rounded-full text-primary text-sm font-medium hover:bg-primary/10";
    cancel.textContent = "Cancel";
    btns.appendChild(cancel);
    btns.appendChild(ok);
    card.appendChild(btns);
    backdrop.appendChild(card);
    document.body.appendChild(backdrop);
    let keydownRemoved = false;
    function removeKeydown() {
      if (!keydownRemoved) {
        keydownRemoved = true;
        window.removeEventListener("keydown", onkey);
      }
    }
    const done = (val) => {
      removeKeydown();
      backdrop.remove();
      resolve(val);
    };
    ok.onclick = () => done(input.value);
    cancel.onclick = () => done(null);
    backdrop.onclick = (e) => {
      if (e.target === backdrop) done(null);
    };
    const onkey = (e) => {
      if (e.key === "Escape") { removeKeydown(); done(null); }
      if (e.key === "Enter") { removeKeydown(); done(input.value); }
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

// The UI language is English (docs/DECISIONS.md); never the browser locale, or
// the same calendar renders "18:50" for one user and "下午06:50" for another.
const LOCALE = "en-GB";
const TIME_FORMAT = new Intl.DateTimeFormat(LOCALE, {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function utcTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : TIME_FORMAT.format(date);
}

export function utcDay(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString(LOCALE, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

/** Local-time ``YYYY-MM-DD``. ``toISOString()`` shifts by the UTC offset and
 *  silently drops the first/last day of every range — never use it for dates. */
export function localDateString(value) {
  const date = value instanceof Date ? value : new Date(value);
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function formatMonth(value) {
  const date = value instanceof Date ? value : new Date(value);
  return date.toLocaleDateString(LOCALE, { month: "long", year: "numeric" });
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
    // Single dim layer: the modal carries the scrim. The old separate
    // “backdrop” div painted over the card and swallowed every click; the
    // card is now positioned and only clicks on the dimmed area close.
    modal.className =
      "hidden fixed inset-0 z-[70] bg-on-surface/40 flex items-center justify-center";
    const card = document.createElement("div");
    card.className =
      "relative bg-surface-container-high text-on-surface rounded-[28px] shadow-xl w-96 max-w-md mx-4 p-6 max-h-[90vh] overflow-y-auto";
    modal.appendChild(card);
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeModal();
    });
    document.body.appendChild(modal);
  }
  const card = modal.children[0];
  // Write-only credentials: blank means "keep the stored value".
  const SENSITIVE_KEYS = new Set(["llm_api_key", "cf_api_token"]);
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
      await showLlmError();
    } catch (err) {
      toast("Failed to load settings: " + err.message, "error");
    }
  }

  /** Surface the server's last LLM failure, if any, next to the status line. */
  async function showLlmError() {
    const result = document.getElementById("st-llm-result");
    if (!result) return;
    try {
      const status = await api("llm/status");
      const err = status && status.last_error;
      if (err) {
        result.className = "text-[11px] mb-3 text-error whitespace-pre-line";
        result.textContent = `Last LLM error (${err.skill}): ${err.message}`;
      } else {
        result.textContent = "";
      }
    } catch {
      /* diagnostics are best-effort */
    }
  }

  function setStatus(values) {
    const enabled = Boolean(values.llm_base_url && values.llm_model);
    const status = document.getElementById("st-status");
    if (enabled) {
      status.textContent = `LLM: on — ${values.llm_model} @ ${values.llm_base_url}`;
      status.className = "text-xs mb-2 text-emerald-800 whitespace-pre-line break-all";
    } else if (values.llm_base_url || values.llm_model) {
      status.textContent = "LLM: off — both a base URL and a model are required (offline mode)";
      status.className = "text-xs mb-2 text-amber-800 whitespace-pre-line";
    } else {
      status.textContent = "LLM: off — offline mode (deterministic fallbacks)";
      status.className = "text-xs mb-2 text-on-surface-variant whitespace-pre-line";
    }
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
      const value = el.value;
      if (spec.type === "number") {
        if (value === "") return; // blank number = unchanged
        payload[spec.key] = Number(value);
        return;
      }
      // Credentials are write-only: a blank field means "keep the stored key",
      // it must never wipe it. Every other text field is sent even when blank,
      // so the endpoint can be cleared back to offline mode.
      if (SENSITIVE_KEYS.has(spec.key) && value.trim() === "") return;
      payload[spec.key] = typeof value === "string" ? value.trim() : value;
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
      await load(); // re-read what the server actually stored
      closeModal();
    } catch (err) {
      // No half-saving: the PUT either succeeded or failed atomically.
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
  title.className = "text-lg font-medium text-on-surface mb-4";
  title.textContent = "Settings";
  card.appendChild(title);
  const form = document.createElement("div");
  form.className = "space-y-3";
  fields.forEach((spec) => {
    const wrap = document.createElement("div");
    const label = document.createElement("label");
    label.className = "block text-xs text-on-surface-variant mb-1";
    label.textContent = spec.label;
    let input;
    if (spec.type === "select") {
      input = document.createElement("select");
      input.className =
        "w-full border border-outline rounded-lg bg-transparent text-on-surface px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary";
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
        "w-full border border-outline rounded-lg bg-transparent text-on-surface px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary";
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
  status.className = "text-xs mb-2 text-on-surface-variant";
  status.textContent = "Loading…";
  const llmResult = document.createElement("p");
  llmResult.id = "st-llm-result";
  llmResult.className = "text-[11px] mb-3";
  card.appendChild(form);
  card.appendChild(status);
  card.appendChild(llmResult);
  const btns = document.createElement("div");
  btns.className = "flex items-center justify-end gap-2 pt-3 border-t border-outline-variant";
  const test = document.createElement("button");
  test.id = "st-llm-test";
  test.type = "button";
  test.className =
    "mr-auto px-4 h-9 rounded-full border border-outline text-sm font-medium text-primary hover:bg-primary/10";
  test.textContent = "Test connection";
  test.onclick = async () => {
    const previous = test.textContent;
    test.disabled = true;
    test.textContent = "Testing…";
    llmResult.className = "text-[11px] mb-3 text-on-surface-variant";
    llmResult.textContent = "";
    try {
      const result = await api("llm/test", { method: "POST" });
      if (result.ok) {
        llmResult.className = "text-[11px] mb-3 text-emerald-800";
        llmResult.textContent = `OK — ${result.model} replied in ${result.latency_ms} ms`;
      } else {
        llmResult.className = "text-[11px] mb-3 text-error whitespace-pre-line";
        llmResult.textContent = `Failed (${result.kind || "error"}): ${result.error || "no response"}`;
      }
    } catch (err) {
      llmResult.className = "text-[11px] mb-3 text-error";
      llmResult.textContent = "Test failed: " + err.message;
    } finally {
      test.disabled = false;
      test.textContent = previous;
    }
  };
  const cancel = document.createElement("button");
  cancel.id = "st-cancel";
  cancel.className = "px-4 h-9 rounded-full text-sm font-medium text-primary hover:bg-primary/10";
  cancel.textContent = "Cancel";
  const save = document.createElement("button");
  save.id = "st-save";
  save.className = "px-5 h-9 rounded-full bg-primary text-on-primary text-sm font-medium hover:shadow-md";
  save.textContent = "Save";
  btns.appendChild(test);
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
