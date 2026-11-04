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
  const headers = { "Content-Type": "application/json", ...options.headers };
  const init = { ...options, headers };
  if (init.body && typeof init.body === "object") {
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
