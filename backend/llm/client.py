"""Reasoner interface + backends (FR-11/12).

Groq is OpenAI-compatible:
  POST {GROQ_BASE_URL}/chat/completions
  headers: Authorization: Bearer <GROQ_API_KEY>
  body: {"model": ..., "messages": [...], "tools": [...], "tool_choice": "auto",
         "temperature": 0.2, "stream": false}
  -> choices[0].message may contain .content and/or .tool_calls[]

Both backends normalize to {"content": str|None, "tool_calls": [{"id","name","arguments":dict}]}
and NEVER raise on network / parse / malformed-argument errors — a transient failure comes
back as safe empty output so the agent degrades gracefully rather than crashing.

``LocalReasoner`` is a deterministic, offline reasoner. It emulates tool-calling by
inspecting the running message list: on the first turn (when memory tools are offered and
no tool result is present yet) it emits a ``recall_memory`` tool call; once the recall
result is fed back it synthesizes a structured JSON brief grounded in those memories. With
no tools / no memories it emits the "cold start" baseline brief for the before/after demo.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Optional

from ..config import Settings

log = logging.getLogger("muninn.llm")


class Reasoner(ABC):
    backend_name: str = "abstract"

    @abstractmethod
    def chat(self, messages: list[dict[str, Any]], *,
             tools: Optional[list[dict]] = None,
             temperature: float = 0.2) -> dict[str, Any]:
        """Return a normalized assistant message:
        {"content": str|None, "tool_calls": [{"id","name","arguments":dict}]}.
        MUST NOT raise on network/parse errors — return {"content": <safe>, "tool_calls": []}."""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """{"backend": name, "ok": bool, "detail": str} — never raises."""


class GroqClient(Reasoner):
    backend_name = "groq"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.groq_base_url.rstrip("/")
        self.api_key = settings.groq_api_key
        self.model = settings.groq_model
        self.timeout = settings.request_timeout

    def chat(self, messages, *, tools=None, temperature=0.2):
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8") or "{}"
            parsed = json.loads(raw)
            return self._normalize(parsed)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            log.warning("groq unreachable: %s", exc)
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            log.warning("groq bad response: %s", exc)
        except Exception as exc:  # noqa: BLE001 - chat must never raise
            log.warning("groq unexpected error: %s", exc)
        return {"content": "", "tool_calls": []}

    @staticmethod
    def _normalize(parsed: dict[str, Any]) -> dict[str, Any]:
        choices = parsed.get("choices") or []
        if not choices:
            return {"content": "", "tool_calls": []}
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content")
        tool_calls: list[dict[str, Any]] = []
        for tc in message.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function", {}) if isinstance(tc.get("function"), dict) else {}
            name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            # Groq returns arguments as a JSON *string*; tolerate malformed JSON (FR-12).
            if isinstance(raw_args, dict):
                args = raw_args
            else:
                try:
                    args = json.loads(raw_args) if raw_args else {}
                    if not isinstance(args, dict):
                        args = {}
                except (json.JSONDecodeError, TypeError):
                    args = {}
            tool_calls.append({"id": str(tc.get("id", "")), "name": name, "arguments": args})
        return {"content": content, "tool_calls": tool_calls}

    def health(self):
        ok = bool(self.api_key)
        return {"backend": self.backend_name, "ok": ok,
                "detail": "configured" if ok else "no GROQ_API_KEY"}


class LocalReasoner(Reasoner):
    """Deterministic offline reasoner: turns recalled memories into a structured brief."""

    backend_name = "local"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def chat(self, messages, *, tools=None, temperature=0.2):
        try:
            return self._chat(messages, tools)
        except Exception as exc:  # noqa: BLE001 - never raise
            log.warning("local reasoner error: %s", exc)
            return {"content": json.dumps(_fallback_brief()), "tool_calls": []}

    def _chat(self, messages: list[dict[str, Any]], tools) -> dict[str, Any]:
        tool_results = [m for m in messages if m.get("role") == "tool"]
        has_tools = bool(tools)
        already_called = any(
            m.get("role") == "assistant" and m.get("tool_calls") for m in messages
        )
        # Phase 1: offer recall on the first turn when memory tools are available.
        if has_tools and not tool_results and not already_called:
            query = _first_user_text(messages)
            return {
                "content": None,
                "tool_calls": [{
                    "id": "call_local_recall",
                    "name": "recall_memory",
                    "arguments": {"query": query, "top_k": self.settings.recall_top_k},
                }],
            }
        # Phase 2: synthesize the final brief from whatever memories came back.
        memories = _memories_from_tool_results(tool_results)
        brief = _synthesize_brief(_first_user_text(messages), memories)
        return {"content": json.dumps(brief), "tool_calls": []}

    def health(self):
        return {"backend": self.backend_name, "ok": True,
                "detail": "deterministic offline reasoner"}


# --- deterministic synthesis helpers ---------------------------------------

def _first_user_text(messages: list[dict[str, Any]]) -> str:
    for m in messages:
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"]
    return ""


def _memories_from_tool_results(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tr in tool_results:
        content = tr.get("content")
        if not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("memories"), list):
            out.extend(m for m in payload["memories"] if isinstance(m, dict))
    return out


def _extract_line(content: str, prefix: str) -> str:
    for line in content.splitlines():
        s = line.strip()
        if s.lower().startswith(prefix.lower()):
            _, _, rest = s.partition(":")
            return rest.strip()
    return ""


def _synthesize_brief(query: str, memories: list[dict[str, Any]]) -> dict[str, Any]:
    if not memories:
        return _fallback_brief()
    top = memories[0]
    content = top.get("content", "")
    source = top.get("source", "")
    score = float(top.get("score", 0.0) or 0.0)
    root = _extract_line(content, "Root cause") or "insufficient signal — investigate top recalled incident"
    fix = _extract_line(content, "Fix")
    runbook = _extract_line(content, "Runbook")
    steps = [s.strip() for s in fix.split(";") if s.strip()] if fix else [
        "Review the cited past incident and apply the documented mitigation.",
    ]
    citations = [
        {
            "id": m.get("source", ""),
            "score": round(float(m.get("score", 0.0) or 0.0), 4),
            "excerpt": (m.get("content", "").split("\n", 1)[0])[:160],
        }
        for m in memories[:3] if m.get("source")
    ]
    return {
        "summary": (f"Matches {len(memories)} prior incident(s); closest is "
                    f"{source or 'a past incident'} ({score:.0%}). Likely a known pattern."),
        "root_cause_hypothesis": root,
        "confidence": round(min(0.95, max(0.2, score)), 2),
        "remediation_steps": steps,
        "runbook_ref": runbook,
        "citations": citations,
    }


def _fallback_brief() -> dict[str, Any]:
    return {
        "summary": ("No similar past incidents in memory — cold start. "
                    "Proceeding on generic SRE guidance only."),
        "root_cause_hypothesis": "Unknown — no institutional memory to ground a hypothesis.",
        "confidence": 0.15,
        "remediation_steps": [
            "Check recent deploys/config changes for this service and consider a rollback.",
            "Inspect error rate, saturation, and dependency health dashboards.",
            "Page the service owner if customer impact is confirmed.",
        ],
        "runbook_ref": "",
        "citations": [],
    }


def build_reasoner(settings: Settings) -> Reasoner:
    """Factory: Groq when a key is configured (or forced), else local."""
    if settings.resolved_llm_backend() == "groq":
        return GroqClient(settings)
    return LocalReasoner(settings)
