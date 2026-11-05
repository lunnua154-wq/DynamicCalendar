/** Shared API client + UI helpers. ES2022, vanilla, no framework. */

const API_BASE = "/api";

// ------------------------------------------------------------------ DOM helpers

export function show(el) {
  el?.classList.remove("hidden");
}

export function hide(el) {
  el?.classList.add("hidden");
}

export function toggle(el, visible) {
  el?.classList.toggle("hidden", !visible);
}

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

// ------------------------------------------------------------------ toasts

let toastContainer;

function ensureToastContainer() {
  if (toastContainer) return toastContainer;
  toastContainer = el(
    "div",
    "fixed top-4 right-4 z-[70] flex flex-col gap-2 pointer-events-none"
  );
  document.body.appendChild(toastContainer);
  return toastContainer;
}

export function toast(message, kind = "info") {
  const container = ensureToastContainer();
  const kinds = {
    info: "bg-sky-600 text-white",
    success: "bg-emerald-600 text-white",
    error: "bg-rose-600 text-white",
  };
  const node = el(
    "div",
    `px-4 py-3 rounded-lg shadow-lg text-sm max-w-xs transition-opacity duration-300 ${
      kinds[kind] || kinds.info
    }`,
    message
  );
  node.setAttribute("role", kind === "error" ? "alert" : "status");
  container.appendChild(node);
  setTimeout(() => {
    node.style.opacity = "0";
    setTimeout(() => node.remove(), 300);
  }, 4000);
}

// ------------------------------------------------------------------ dialogs

export function dialog({ title, message, confirmLabel = "OK", cancelLabel = "Cancel", danger = false }) {
  return new Promise((resolve) => {
    const backdrop = el(
      "div",
      "fixed inset-0 z-[65] bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4"
    );
    const card = el("div", "bg-white rounded-xl p-5 shadow-2xl w-full max-w-sm");
    const heading = el("h3", "font-semibold mb-2", title);
    const body = el("p", "text-sm text-slate-600 mb-5 whitespace-pre-line", message);
    const actions = el("div", "flex justify-end gap-2");
    const cancel = el(
      "button",
      "px-3 py-1.5 rounded border border-slate-300 text-sm text-slate-700 hover:bg-slate-50",
      cancelLabel
    );
    const confirm = el(
      "button",
      `px-3 py-1.5 rounded text-sm text-white ${
        danger ? "bg-rose-600 hover:bg-rose-700" : "bg-blue-600 hover:bg-blue-700"
      }`,
      confirmLabel
    );
    actions.append(cancel, confirm);
    card.append(heading, body, actions);
    backdrop.appendChild(card);
    document.body.appendChild(backdrop);

    const close = (value) => {
      backdrop.remove();
      document.removeEventListener("keydown", onKey);
      resolve(value);
    };
    const onKey = (event) => {
      if (event.key === "Escape") close(false);
      if (event.key === "Enter") close(true);
    };
    document.addEventListener("keydown", onKey);
    confirm.onclick = () => close(true);
    cancel.onclick = () => close(false);
    backdrop.onclick = (event) => {
      if (event.target === backdrop) close(false);
    };
    confirm.focus();
  });
}

// ------------------------------------------------------------------ API client

export async function api(path, { method = "GET", body, headers = {} } = {}) {
  const init = { method, headers: { ...headers } };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const response = await fetch(`${API_BASE}/${path.replace(/^\//, "")}`, init);
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const message =
      (data && data.error) ||
      (data && data.detail) ||
      (typeof data === "string" && data) ||
      `HTTP ${response.status}`;
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return data;
}

/** Run an async action with a busy button state; errors become error toasts. */
export async function busy(button, action) {
  if (button) button.disabled = true;
  try {
    return await action();
  } catch (error) {
    toast(error.message || String(error), "error");
    return undefined;
  } finally {
    if (button) button.disabled = false;
  }
}

// ------------------------------------------------------------------ time formatting

/** The app UI language is English (docs/DECISIONS.md) — never the browser locale. */
export const LOCALE = "en-GB";

const timeFormat = new Intl.DateTimeFormat(LOCALE, {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function fmtTime(iso) {
  if (!iso) return "";
  return timeFormat.format(new Date(iso));
}

export function fmtDay(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString(LOCALE, {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}

export function fmtMonth(date) {
  return new Date(date).toLocaleDateString(LOCALE, { month: "long", year: "numeric" });
}

export function fmtHours(hours) {
  const value = Number(hours || 0);
  return `${value.toFixed(value % 1 === 0 ? 0 : 1)}h`;
}

export function localDayString(date = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

// ------------------------------------------------------------------ nav

export function initNav({ active, onDataChanged }) {
  const tabs = [
    ["Calendar", "/"],
    ["Week", "/week"],
    ["Inbox", "/inbox"],
  ];
  const host = document.getElementById("nav-tabs");
  if (host) {
    tabs.forEach(([label, href]) => {
      const current = label === active;
      const button = el(
        "button",
        `px-3 py-1 text-sm ${
          current ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-100"
        }`,
        label
      );
      button.setAttribute("aria-current", current ? "page" : "false");
      button.onclick = () => {
        if (!current) window.location.href = href;
      };
      host.appendChild(button);
    });
  }
  const gear = document.getElementById("btn-settings");
  if (gear) gear.onclick = () => openSettings({ onSaved: onDataChanged });
}

// ------------------------------------------------------------------ settings panel

let settingsModal;

async function openSettings({ onSaved } = {}) {
  if (!settingsModal) {
    settingsModal = buildSettingsModal();
    document.body.appendChild(settingsModal.backdrop);
  }
  const { backdrop, form, status } = settingsModal;
  status.textContent = "loading…";
  try {
    const rows = await api("settings");
    const values = Object.fromEntries(rows.map((row) => [row.key, row.value]));
    form.elements.namedItem("daily_capacity_hours").value = values.daily_capacity_hours ?? "4";
    form.elements.namedItem("slot_minutes").value = values.slot_minutes ?? "50";
    form.elements.namedItem("min_slot_minutes").value = values.min_slot_minutes ?? "30";
    form.elements.namedItem("default_effort_hours").value = values.default_effort_hours ?? "1";
    form.elements.namedItem("utc_offset_minutes").value = values.utc_offset_minutes ?? "0";
    form.elements.namedItem("revision_intervals_days").value = (
      values.revision_intervals_days || "[1,3,7,14]"
    )
      .replace(/[[\]"]/g, "")
      .replace(/\s/g, "");
    form.elements.namedItem("llm_base_url").value = values.llm_base_url ?? "";
    form.elements.namedItem("llm_model").value = values.llm_model ?? "";
    form.elements.namedItem("llm_api_key").value = values.llm_api_key ?? "";
    status.textContent = "";
  } catch (error) {
    status.textContent = error.message;
  }
  show(backdrop);
}

function buildSettingsModal() {
  const backdrop = el(
    "div",
    "hidden fixed inset-0 z-[64] bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4"
  );
  const card = el("div", "bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto");
  const head = el("div", "flex items-center justify-between px-5 py-3 border-b border-slate-200");
  head.append(
    el("h2", "font-semibold", "Settings"),
    (() => {
      const close = el("button", "text-slate-400 hover:text-slate-700 text-lg leading-none", "✕");
      close.setAttribute("aria-label", "Close settings");
      close.onclick = () => hide(backdrop);
      return close;
    })()
  );

  const form = document.createElement("form");
  form.className = "px-5 py-4 space-y-3";

  const numberField = (name, label, hint, attrs = {}) => {
    const wrap = el("div", "flex items-center justify-between gap-3");
    const labelNode = el("label", "text-sm text-slate-700", label);
    labelNode.htmlFor = `setting-${name}`;
    const input = document.createElement("input");
    input.id = `setting-${name}`;
    input.name = name;
    input.type = "number";
    Object.assign(input, attrs);
    input.className =
      "w-28 border border-slate-300 rounded px-2 py-1 text-sm text-right focus:outline-none focus:ring-2 focus:ring-blue-500";
    wrap.append(labelNode, input);
    if (hint) wrap.title = hint;
    form.appendChild(wrap);
    return input;
  };

  const textField = (name, label, placeholder = "") => {
    const wrap = el("div", "space-y-1");
    const labelNode = el("label", "text-xs font-medium text-slate-600", label);
    labelNode.htmlFor = `setting-${name}`;
    const input = document.createElement("input");
    input.id = `setting-${name}`;
    input.name = name;
    input.type = "text";
    input.placeholder = placeholder;
    input.className =
      "w-full border border-slate-300 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500";
    wrap.append(labelNode, input);
    form.appendChild(wrap);
    return input;
  };

  form.append(el("h3", "text-xs font-semibold uppercase tracking-wider text-slate-400", "Schedule"));
  numberField("daily_capacity_hours", "Daily capacity (hours)", "0.5 – 12", {
    min: "0.5",
    max: "12",
    step: "0.5",
  });
  numberField("slot_minutes", "Slot length (minutes)", "15 – 120", {
    min: "15",
    max: "120",
    step: "5",
  });
  numberField("min_slot_minutes", "Minimum slot (minutes)", "15 – 120", {
    min: "15",
    max: "120",
    step: "5",
  });
  numberField("default_effort_hours", "Default effort (hours)", "0.1 – 20", {
    min: "0.1",
    max: "20",
    step: "0.1",
  });
  numberField("utc_offset_minutes", "UTC offset (minutes)", "e.g. 480 for UTC+8", {
    min: "-840",
    max: "840",
    step: "30",
  });
  textField("revision_intervals_days", "Revision intervals (days, ascending)", "1,3,7,14");

  form.append(el("h3", "pt-3 text-xs font-semibold uppercase tracking-wider text-slate-400", "LLM (optional)"));
  textField("llm_base_url", "Base URL", "http://localhost:11434/v1");
  textField("llm_model", "Model", "llama3.2");
  const keyField = textField("llm_api_key", "API key", "leave empty for local endpoints");
  keyField.type = "password";

  const status = el("p", "text-xs text-rose-600 min-h-[1rem]");
  const actions = el("div", "flex justify-end gap-2 pt-2");
  const cancel = el(
    "button",
    "px-3 py-1.5 rounded border border-slate-300 text-sm text-slate-700 hover:bg-slate-50",
    "Close"
  );
  cancel.type = "button";
  cancel.onclick = () => hide(backdrop);
  const save = el("button", "px-3 py-1.5 rounded bg-blue-600 text-white text-sm hover:bg-blue-700", "Save");
  save.type = "submit";
  actions.append(cancel, save);
  form.append(status, actions);
  card.append(head, form);
  backdrop.appendChild(card);

  form.onsubmit = async (event) => {
    event.preventDefault();
    status.textContent = "";
    const payload = {
      daily_capacity_hours: Number(form.elements.namedItem("daily_capacity_hours").value),
      slot_minutes: Number(form.elements.namedItem("slot_minutes").value),
      min_slot_minutes: Number(form.elements.namedItem("min_slot_minutes").value),
      default_effort_hours: Number(form.elements.namedItem("default_effort_hours").value),
      utc_offset_minutes: Number(form.elements.namedItem("utc_offset_minutes").value),
      revision_intervals_days: form.elements
        .namedItem("revision_intervals_days")
        .value.split(",")
        .map((part) => Number(part.trim()))
        .filter((value) => Number.isFinite(value)),
      llm_base_url: form.elements.namedItem("llm_base_url").value.trim(),
      llm_model: form.elements.namedItem("llm_model").value.trim(),
      llm_api_key: form.elements.namedItem("llm_api_key").value.trim(),
    };
    try {
      await api("settings", { method: "PUT", body: payload });
      toast("Settings saved — plan recomputed", "success");
      hide(backdrop);
      onSaved?.();
    } catch (error) {
      status.textContent = error.message;
    }
  };
  return { backdrop, form, status };
}
