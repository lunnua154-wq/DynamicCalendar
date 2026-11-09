"""Pluggable LLM client (any OpenAI-compatible endpoint) with offline fallbacks.

The four skills (tag, cluster, chat, tune_settings) each have a deterministic
fallback used when no LLM endpoint is configured (offline mode, AGENTS.md #7).
LLM output is validated before persisting; rejected output falls back.

The local llama.cpp model wraps JSON in `````json```` fences, so every LLM
skill uses :func:`extract_json` to strip fences and pull out the first JSON
object. On any parse or validation error the skill falls back (no exception).
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from openai import OpenAI

from .config import (
    EFFORT_HOURS_MAX,
    EFFORT_HOURS_MIN,
    Settings,
    validate_capacity_hours,
    validate_slot_minutes,
)
try:
    from .tools import dispatch, tool_specs
    _TOOLS_OK = True
except Exception:  # pragma: no cover - tools module always present
    _TOOLS_OK = False
    dispatch = None  # type: ignore[assignment]
    tool_specs = None  # type: ignore[assignment]


_JSON_FENCE = re.compile(r"```(?:json)?\s*", re.IGNORECASE)

#: Deterministic effort baseline: ~200 wpm reading with review/practice passes.
_EFFORT_WORDS_PER_HOUR = 600
_EFFORT_MIN_HOURS = 0.5


def _effort_baseline_hours(markdown: str) -> float | None:
    """Size-based effort estimate in hours, clamped to [EFFORT_HOURS_MIN, EFFORT_HOURS_MAX].

    Returns None for empty markdown so callers can fall back to settings.
    """
    words = len(markdown.split())
    if words == 0:
        return None
    hours = words / _EFFORT_WORDS_PER_HOUR
    return round(min(EFFORT_HOURS_MAX, max(_EFFORT_MIN_HOURS, hours)), 2)


#: Last LLM failure seen by this process. Surfaced to the client so a configured
#: but broken endpoint is distinguishable from "no endpoint configured".
_LAST_ERROR: dict[str, Any] | None = None


def _record_error(skill: str, exc: BaseException) -> dict[str, Any]:
    """Remember the most recent LLM failure (never raised to the caller)."""
    global _LAST_ERROR
    message = str(exc).strip() or exc.__class__.__name__
    info = {"skill": skill, "message": message[:300]}
    _LAST_ERROR = {**info, "at": _now_utc()}
    return info


def last_error() -> dict[str, Any] | None:
    return dict(_LAST_ERROR) if _LAST_ERROR else None


def clear_last_error() -> None:
    global _LAST_ERROR
    _LAST_ERROR = None


def _error_kind(exc: BaseException) -> str:
    """Classify a client failure for the UI (auth / connection / timeout / ...)."""
    name = exc.__class__.__name__
    known = {
        "AuthenticationError": "auth",
        "PermissionDeniedError": "auth",
        "NotFoundError": "not_found",
        "APIConnectionError": "connection",
        "APITimeoutError": "timeout",
        "BadRequestError": "bad_request",
        "RateLimitError": "rate_limit",
        "APIStatusError": "http_error",
    }
    return known.get(name, "unknown")

# Chat keywords that indicate the user is asking about the calendar schedule.
_SCHEDULE_KEYWORDS = (
    "due", "schedule", "week", "plan", "upcoming", "next",
    "what's", "what is", "today", "tomorrow",
)


def extract_json(text: str) -> dict[str, Any]:
    """Strip markdown fences and return the first JSON object in ``text``.

    Uses the standard-library decoder (string-aware), so braces inside quoted
    values don't truncate the parse. Raises ``ValueError`` if no parseable
    object is found. Callers catch this and fall back (never propagate).
    """
    cleaned = _JSON_FENCE.sub("", text or "").strip()
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("no JSON object in LLM output")
    try:
        obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    except json.JSONDecodeError as exc:
        raise ValueError("no parseable JSON object in LLM output") from exc
    if not isinstance(obj, dict):
        raise ValueError("LLM output is not a JSON object")
    return obj


def _fmt_slot(iso: str, offset_minutes: int = 0) -> str:
    """Format a stored UTC slot in the user's local clock: ``Mon 09 Nov 14:00``."""
    s = iso.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(timezone.utc) + timedelta(minutes=offset_minutes)
    return local.strftime("%a %d %b %H:%M")


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strip_fences(text: str) -> str:
    """Remove markdown fences (``` / ```json) wrapping a JSON payload."""
    text = _JSON_FENCE.sub("", text or "").strip()
    if text.endswith("```"):
        text = text[:-3].strip()
    return text


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any = None  # created lazily on first LLM-backed call

    @property
    def client(self) -> Any:
        if self._client is None and self.settings.llm_enabled:
            try:
                self._client = OpenAI(
                    base_url=self.settings.llm_base_url or None,
                    # The OpenAI SDK requires a non-empty key even for local
                    # servers (llama.cpp/Ollama) that ignore authentication.
                    api_key=self.settings.llm_api_key or "not-needed",
                )
            except Exception:  # pragma: no cover - malformed endpoint config
                # Never let a bad endpoint configuration escape the skills'
                # offline fallback path.
                return None
        return self._client

    # ---------------------------------------------------------- diagnostics

    def check_connection(self, *, timeout: float = 15.0) -> dict[str, Any]:
        """Round-trip the configured endpoint with a tiny completion.

        Read-only, bounded, and never raises: the settings modal uses it to tell
        "configured and working" apart from "configured but unreachable".
        """
        if not self.settings.llm_enabled:
            return {
                "ok": False,
                "kind": "not_configured",
                "error": "no LLM endpoint configured (base URL + model are required)",
            }
        client = self.client
        if client is None:
            return {"ok": False, "kind": "bad_config", "error": "could not build the LLM client"}
        started = time.monotonic()
        try:
            # No retries for a diagnostic: a dead endpoint should answer in
            # well under a second instead of backing off three times.
            # with_options lives on the client in the openai>=3 SDK (not on
            # chat.completions).
            completions = client.with_options(
                max_retries=0, timeout=timeout
            ).chat.completions
            resp = completions.create(
                model=self.settings.llm_model,
                messages=[{"role": "user", "content": "Reply with the single word: pong"}],
                max_tokens=8,
                temperature=0,
            )
            text = (resp.choices[0].message.content or "").strip()
            clear_last_error()
            return {
                "ok": True,
                "model": self.settings.llm_model,
                "reply": text[:120],
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        except Exception as exc:  # any failure is a diagnostic result, not a crash
            info = _record_error("test", exc)
            return {
                "ok": False,
                "kind": _error_kind(exc),
                **info,
                "latency_ms": int((time.monotonic() - started) * 1000),
            }

    # ---------------------------------------------------------------- tag

    def tag(self, *, markdown: str, filename: str) -> dict[str, Any]:
        baseline = _effort_baseline_hours(markdown)
        if self.client is not None:
            try:
                return self._tag_llm(markdown=markdown, filename=filename, baseline=baseline)
            except Exception as exc:  # network/LLM failure -> offline path
                _record_error("tag", exc)
        return self._tag_fallback(filename=filename, baseline=baseline)

    def _tag_llm(
        self, *, markdown: str, filename: str, baseline: float | None = None
    ) -> dict[str, Any]:
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        words = len(markdown.split())
        base = (
            baseline
            if baseline is not None
            else float(self.settings.default_effort_hours or 1.0)
        )
        lo = round(max(EFFORT_HOURS_MIN, base * 0.5), 2)
        hi = round(min(EFFORT_HOURS_MAX, base * 1.5), 2)
        prompt = (
            "You tag study material. Reply with one JSON object and nothing else, "
            "using plain values (no angle brackets or placeholders). Example:\n"
            '{"summary": "One sentence describing the material.", '
            '"tags": ["photosynthesis", "biology"], "effort_hours": 1.5}\n'
            f"The material is ~{words} words. A deterministic size-based estimate "
            f"is {base:.2f} hours (reading + review). Estimate the effort to LEARN "
            f"it; effort_hours must be a number between {lo} and {hi}.\n"
            f"Material filename: {filename}\n\n{markdown[:4000]}"
        )
        resp = self.client.chat.completions.create(  # type: ignore[union-attr]
            model=self.settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        data = extract_json(resp.choices[0].message.content or "{}")
        raw = data.get("effort_hours")
        effort: float | None = None
        try:
            effort = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            effort = None
        if effort is None:
            effort = base
        effort = min(max(effort, lo), hi)
        effort = round(min(max(effort, EFFORT_HOURS_MIN), EFFORT_HOURS_MAX), 2)
        return {
            "summary": str(data.get("summary", stem))[:500],
            "tags": [str(t) for t in (data.get("tags") or [stem])][:5],
            "effort_hours": effort,
        }

    def _tag_fallback(
        self, *, filename: str, baseline: float | None = None
    ) -> dict[str, Any]:
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        return {
            "summary": f"Material imported from {filename}.",
            "tags": [stem],
            "effort_hours": (
                baseline if baseline is not None else self.settings.default_effort_hours
            ),
        }

    # ------------------------------------------------------------- cluster

    def cluster(
        self,
        *,
        content_hash: str,
        filename: str,
        existing_topics: list[dict[str, Any]],
        existing_material_hashes: list[str],
        markdown: str = "",
        topic_name: str | None = None,
        effort_hours: float | None = None,
    ) -> dict[str, Any]:
        if content_hash in existing_material_hashes:
            return {
                "topic_id": None,
                "relation": "duplicate",
                "reason": f"identical content already ingested ({filename})",
            }
        if self.client is not None:
            try:
                return self._cluster_llm(
                    filename=filename,
                    existing_topics=existing_topics,
                    markdown=markdown,
                    topic_name=topic_name,
                    effort_hours=effort_hours,
                )
            except Exception as exc:  # network/LLM failure -> offline path
                _record_error("cluster", exc)
        return self._cluster_fallback(content_hash=content_hash, filename=filename)

    def _cluster_llm(
        self,
        *,
        filename: str,
        existing_topics: list[dict[str, Any]],
        markdown: str = "",
        topic_name: str | None = None,
        effort_hours: float | None = None,
    ) -> dict[str, Any]:
        topics_json = json.dumps(
            [{"id": t["id"], "name": t["name"]} for t in existing_topics]
        )
        excerpt = (markdown or "")[:1500]
        hint = ""
        if topic_name:
            hint = f"\nSuggested topic name: {topic_name}"
        if effort_hours is not None:
            hint += f"\nEstimated effort hours: {effort_hours}"
        prompt = (
            "Classify a new study file. Return JSON only: "
            '{"topic_id": "<existing topic id or null>", "relation": "new|duplicate|version", '
            '"reason": "<text>"}. Existing topics: '
            f"{topics_json}\nNew file: {filename}{hint}\nExcerpt: {excerpt}"
        )
        resp = self.client.chat.completions.create(  # type: ignore[union-attr]
            model=self.settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        data = extract_json(resp.choices[0].message.content or "{}")
        relation = str(data.get("relation", "new"))
        if relation not in ("new", "duplicate", "version"):
            relation = "new"
        topic_id = data.get("topic_id")
        topic_ids = {t["id"] for t in existing_topics}
        if topic_id is not None and topic_id not in topic_ids:
            topic_id = None
        return {
            "topic_id": topic_id,
            "relation": relation,
            "reason": str(data.get("reason", ""))[:200],
        }

    def _cluster_fallback(self, *, content_hash: str, filename: str) -> dict[str, Any]:
        # Offline: de-dup by content_hash only; each file its own topic.
        return {
            "topic_id": None,
            "relation": "new",
            "reason": f"new topic from {filename} (offline mode)",
        }

    # ---------------------------------------------------------------- chat

    def chat(
        self,
        *,
        message: str,
        history: list[dict[str, str]],
        context: dict[str, Any] | None = None,
        conn: Any = None,
        now_iso: str | None = None,
    ) -> dict[str, Any]:
        error: dict[str, Any] | None = None
        if self.client is not None:
            # 1) full tool loop when a DB connection is available
            if conn is not None and _TOOLS_OK:
                try:
                    return self._chat_llm_tools(
                        message=message,
                        history=history,
                        context=context,
                        conn=conn,
                        now_iso=now_iso or _now_utc(),
                    )
                except Exception as exc:  # e.g. endpoint without tool support
                    error = _record_error("chat", exc)
            # 2) plain chat: works with endpoints that do not support tools
            try:
                out = self._chat_llm(message=message, history=history, context=context)
                clear_last_error()
                return out
            except Exception as exc:  # network/LLM failure -> offline reply
                error = _record_error("chat", exc)
        out = self._chat_fallback(message=message, context=context)
        if error is not None:
            # The reply is still useful, but the client must be able to tell
            # that the configured endpoint failed (docs/API.md).
            out["llm_error"] = error
        return out

    def _chat_llm(
        self, *, message: str, history: list[dict[str, str]], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        system = "You are a study calendar assistant."
        if context:
            system += "\nCalendar context: " + json.dumps(context)[:2000]
        resp = self.client.chat.completions.create(  # type: ignore[union-attr]
            model=self.settings.llm_model,
            messages=[{"role": "system", "content": system}]
            + history
            + [{"role": "user", "content": message}],
            temperature=0.3,
        )
        return {"reply": resp.choices[0].message.content or "", "llm": "on"}

    def _chat_llm_tools(
        self,
        *,
        message: str,
        history: list[dict[str, str]],
        context: dict[str, Any] | None,
        conn: Any,
        now_iso: str,
    ) -> dict[str, Any]:
        """Bounded agent loop (max 5 rounds) over the narrow tool registry.

        Tool results are appended as tool messages; every outcome is recorded
        in the returned ``actions`` receipts. Any exception degrades to the
        offline fallback (never propagates).
        """
        system = (
            "You are a study calendar assistant. Use the narrow tools to read "
            "calendar data or apply bounded settings/effort changes; never "
            "invent job moves or external-event changes."
        )
        if context:
            system += "\nCalendar context: " + json.dumps(context)[:2000]
        tools = tool_specs()
        messages: list[dict[str, Any]] = (
            [{"role": "system", "content": system}]
            + [dict(h) for h in history]
            + [{"role": "user", "content": message}]
        )
        actions: list[dict[str, Any]] = []
        for _ in range(5):
            resp = self.client.chat.completions.create(  # type: ignore[union-attr]
                model=self.settings.llm_model,
                messages=messages,
                tools=tools,
                temperature=0,
            )
            resp_msg = resp.choices[0].message
            tool_calls = getattr(resp_msg, "tool_calls", None) or []
            if not tool_calls:
                out: dict[str, Any] = {
                    "reply": getattr(resp_msg, "content", None) or "",
                    "llm": "on",
                }
                if actions:
                    out["actions"] = actions
                return out
            for tc in tool_calls:
                fn = getattr(tc, "function", None)
                name = getattr(fn, "name", "") or ""
                raw_args = getattr(fn, "arguments", "{}") or "{}"
                try:
                    args = json.loads(_strip_fences(raw_args))
                except Exception as exc:
                    args = raw_args
                    result = {"error": f"invalid tool arguments: {exc}"}
                else:
                    result = dispatch(conn, name, args, now_iso)
                actions.append({"tool": name, "args": args, "result": result})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": getattr(tc, "id", ""),
                        "content": json.dumps(result, default=str),
                    }
                )
        # Cap reached without a final answer: degrade gracefully, but keep
        # the receipts — earlier rounds may already have changed settings.
        out = self._chat_fallback(message=message, context=context)
        if actions:
            out["actions"] = actions
        return out

    def _chat_fallback(self, *, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Genuinely useful offline reply: calendar-grounded, never just
        'LLM not configured'."""
        msg = message.lower()
        context = context or {}
        offset = int(context.get("utc_offset_minutes") or 0)
        jobs = context.get("jobs", []) or []
        events = context.get("events", []) or []
        materials = context.get("materials", []) or []
        goals = context.get("goals", []) or []

        if any(k in msg for k in _SCHEDULE_KEYWORDS):
            items: list[tuple[str, str, str, str]] = []
            for j in jobs:
                if j.get("status") != "pending":
                    continue
                title = j.get("material_title") or j.get("event_title") or "job"
                items.append((j["slot_start"], j["slot_end"], title, j.get("kind", "job")))
            for e in events:
                items.append((e["dtstart"], e["dtend"], e["title"], "external"))
            items.sort(key=lambda x: x[0])
            items = items[:8]
            if items:
                lines = [
                    f"{_fmt_slot(s, offset)}–{_fmt_slot(e, offset)[-5:]} — {title} ({kind})"
                    for s, e, title, kind in items
                ]
                return {"reply": "Here's what's coming up:\n" + "\n".join(lines), "llm": "off"}
            return {"reply": "No upcoming jobs or events in the next 14 days.", "llm": "off"}

        # "summarize <material>" -> use the stored summary.
        m = re.search(r"summar(?:ise|ize)\s+(.+)", msg)
        if m:
            name = m.group(1).strip().lower()
            for mat in materials:
                if name in (mat.get("filename") or "").lower():
                    summary = mat.get("summary") or "No summary stored for that material."
                    return {"reply": summary, "llm": "off"}
            return {"reply": "I couldn't find a material matching that name.", "llm": "off"}

        if goals:
            g = goals[0]
            reply = f"You have {len(goals)} open goal(s), e.g. “{g.get('title')}” "
            reply += f"due {g.get('deadline', '')[:10]}. "
            reply += "Ask me what's due this week, or ask me to summarize a material."
            return {"reply": reply, "llm": "off"}

        return {
            "reply": (
                "I can help with your study calendar. Try asking what's due this "
                "week, or ask me to summarize a material."
            ),
            "llm": "off",
        }

    # ------------------------------------------------------------- tune_settings

    def tune_settings(self, *, context: str) -> dict[str, Any]:
        if self.client is not None:
            try:
                return self._tune_llm(context=context)
            except Exception as exc:  # network/LLM failure -> offline no-op
                _record_error("tune_settings", exc)
        return {}  # offline: no-op, settings keep current values

    def _tune_llm(self, *, context: str) -> dict[str, Any]:
        resp = self.client.chat.completions.create(  # type: ignore[union-attr]
            model=self.settings.llm_model,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Suggest study settings. Return JSON only with optional keys "
                        '"daily_capacity_hours" (0.5-12) and "slot_minutes" (15-120). '
                        f"Context: {context}"
                    ),
                }
            ],
            temperature=0,
        )
        data = extract_json(resp.choices[0].message.content or "{}")
        out: dict[str, Any] = {}
        if "daily_capacity_hours" in data:
            out["daily_capacity_hours"] = validate_capacity_hours(
                float(data["daily_capacity_hours"])
            )
        if "slot_minutes" in data:
            out["slot_minutes"] = validate_slot_minutes(int(data["slot_minutes"]))
        return out
