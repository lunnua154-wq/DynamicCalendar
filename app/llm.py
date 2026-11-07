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
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI

from .config import Settings, validate_capacity_hours, validate_effort_hours, validate_slot_minutes


_JSON_FENCE = re.compile(r"```(?:json)?\s*", re.IGNORECASE)

# Chat keywords that indicate the user is asking about the calendar schedule.
_SCHEDULE_KEYWORDS = (
    "due", "schedule", "week", "plan", "upcoming", "next",
    "what's", "what is", "today", "tomorrow",
)


def extract_json(text: str) -> dict[str, Any]:
    """Strip markdown fences and return the first JSON object in ``text``.

    Raises ``ValueError`` if no parseable object is found. Callers catch this
    and fall back (never propagate).
    """
    cleaned = _JSON_FENCE.sub("", text or "").strip()
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("no JSON object in LLM output")
    depth = 0
    end = -1
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        raise ValueError("no JSON object in LLM output")
    return json.loads(cleaned[start:end + 1])


def _fmt_slot(iso: str) -> str:
    """Format an ISO-8601 UTC slot as ``Mon 09 Nov 14:00``."""
    s = iso.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%a %d %b %H:%M")


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any = None  # created lazily on first LLM-backed call

    @property
    def client(self) -> Any:
        if self._client is None and self.settings.llm_enabled:
            self._client = OpenAI(
                base_url=self.settings.llm_base_url or None,
                api_key=self.settings.llm_api_key or None,
            )
        return self._client

    # ---------------------------------------------------------------- tag

    def tag(self, *, markdown: str, filename: str) -> dict[str, Any]:
        if self.client is not None:
            try:
                return self._tag_llm(markdown=markdown, filename=filename)
            except Exception:  # pragma: no cover - network/LLM failure
                pass
        return self._tag_fallback(filename=filename)

    def _tag_llm(self, *, markdown: str, filename: str) -> dict[str, Any]:
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        prompt = (
            "You tag study material. Return JSON only: "
            '{"summary": "<one sentence>", "tags": ["tag1", "tag2"], '
            '"effort_hours": <number 0.1-20>}. '
            f"Material filename: {filename}\n\n{markdown[:4000]}"
        )
        resp = self.client.chat.completions.create(  # type: ignore[union-attr]
            model=self.settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        data = extract_json(resp.choices[0].message.content or "{}")
        return {
            "summary": str(data.get("summary", stem))[:500],
            "tags": [str(t) for t in (data.get("tags") or [stem])][:5],
            "effort_hours": validate_effort_hours(float(data.get("effort_hours") or 1.0)),
        }

    def _tag_fallback(self, *, filename: str) -> dict[str, Any]:
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        return {
            "summary": f"Material imported from {filename}.",
            "tags": [stem],
            "effort_hours": self.settings.default_effort_hours,
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
            except Exception:  # pragma: no cover - network/LLM failure
                pass
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
        self, *, message: str, history: list[dict[str, str]], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self.client is not None:
            try:
                return self._chat_llm(message=message, history=history, context=context)
            except Exception:  # pragma: no cover - network/LLM failure
                pass
        return self._chat_fallback(message=message, context=context)

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

    def _chat_fallback(self, *, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Genuinely useful offline reply: calendar-grounded, never just
        'LLM not configured'."""
        msg = message.lower()
        context = context or {}
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
                    f"{_fmt_slot(s)}–{_fmt_slot(e)} — {title} ({kind})"
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
            except Exception:  # pragma: no cover - network/LLM failure
                pass
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
