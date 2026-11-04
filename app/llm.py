"""Pluggable LLM client (any OpenAI-compatible endpoint) with offline fallbacks.

The four skills (tag, cluster, chat, tune_settings) each have a deterministic
fallback used when no LLM endpoint is configured (offline mode, AGENTS.md #7).
LLM output is validated before persisting; rejected output falls back.
"""

from __future__ import annotations

from typing import Any

from openai import OpenAI

from .config import Settings, validate_capacity_hours, validate_effort_hours, validate_slot_minutes


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
        import json

        data = json.loads(resp.choices[0].message.content or "{}")
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
                )
            except Exception:  # pragma: no cover
                pass
        return self._cluster_fallback(content_hash=content_hash, filename=filename)

    def _cluster_llm(self, *, filename: str, existing_topics: list[dict[str, Any]]) -> dict[str, Any]:
        import json

        topics_json = json.dumps(
            [{"id": t["id"], "name": t["name"]} for t in existing_topics]
        )
        prompt = (
            "Classify a new study file. Return JSON only: "
            '{"topic_id": "<existing topic id or null>", "relation": "new|duplicate|version", '
            '"reason": "<text>"}. Existing topics: '
            f"{topics_json}\nNew file: {filename}"
        )
        resp = self.client.chat.completions.create(  # type: ignore[union-attr]
            model=self.settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        relation = str(data.get("relation", "new"))
        if relation not in ("new", "duplicate", "version"):
            relation = "new"
        return {
            "topic_id": data.get("topic_id"),
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

    def chat(self, *, message: str, history: list[dict[str, str]]) -> dict[str, Any]:
        if self.client is not None:
            try:
                return self._chat_llm(message=message, history=history)
            except Exception:  # pragma: no cover
                pass
        return self._chat_fallback(message=message)

    def _chat_llm(self, *, message: str, history: list[dict[str, str]]) -> dict[str, Any]:
        resp = self.client.chat.completions.create(  # type: ignore[union-attr]
            model=self.settings.llm_model,
            messages=[{"role": "system", "content": "You are a study calendar assistant."}]
            + history
            + [{"role": "user", "content": message}],
            temperature=0.3,
        )
        return {"reply": resp.choices[0].message.content or "", "llm": "on"}

    def _chat_fallback(self, *, message: str) -> dict[str, Any]:
        return {
            "reply": (
                "LLM not configured — use the calendar pages. "
                f"I received: '{message[:120]}'"
            ),
            "llm": "off",
        }

    # ------------------------------------------------------------- tune_settings

    def tune_settings(self, *, context: str) -> dict[str, Any]:
        if self.client is not None:
            try:
                return self._tune_llm(context=context)
            except Exception:  # pragma: no cover
                pass
        return {}  # offline: no-op, settings keep current values

    def _tune_llm(self, *, context: str) -> dict[str, Any]:
        import json

        prompt = (
            "Suggest study settings. Return JSON only with optional keys "
            '"daily_capacity_hours" (0.5-12) and "slot_minutes" (15-120). '
            f"Context: {context}"
        )
        resp = self.client.chat.completions.create(  # type: ignore[union-attr]
            model=self.settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        out: dict[str, Any] = {}
        if "daily_capacity_hours" in data:
            out["daily_capacity_hours"] = validate_capacity_hours(
                float(data["daily_capacity_hours"])
            )
        if "slot_minutes" in data:
            out["slot_minutes"] = validate_slot_minutes(int(data["slot_minutes"]))
        return out
