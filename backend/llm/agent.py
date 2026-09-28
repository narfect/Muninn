"""``TriageAgent`` — the tool-calling loop (FR-11/12).

Flow:
  1. Seed messages with TRIAGE_SYSTEM_PROMPT + a user message describing the incident.
  2. Loop (max ~4 iterations):
       resp = reasoner.chat(messages, tools=TOOL_SPECS)
       if resp.tool_calls:  dispatch each -> append assistant + tool result messages
       else: break with resp.content (final)
  3. Parse the final content into a backend.models.Brief. If parsing fails (or the loop
     hits its cap without a final answer), build a Brief from the recalled memories
     directly — graceful degradation, never a crash.

Robustness (FR-12): malformed tool calls (bad JSON args, unknown tool, missing fields) go
through ``tools.dispatch`` which returns ``{"error": ...}``; the error is fed back to the
model and the loop continues. Iterations are capped so a misbehaving model cannot loop.

Memory OFF: no tools are offered, so the reasoner produces the "cold start" baseline brief
used for the before/after comparison (FR-8).
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from ..config import settings as _DEFAULT_SETTINGS
from ..models import Brief, Incident, RecallResult
from .prompts import BRIEF_INSTRUCTIONS, TRIAGE_SYSTEM_PROMPT
from .tools import TOOL_SPECS, dispatch

_MAX_ITERS = 4

_SCHEMA_HINT = (
    "\n\n" + BRIEF_INSTRUCTIONS + " Respond with ONLY a JSON object of the form:\n"
    '{"summary": str, "root_cause_hypothesis": str, "confidence": number(0..1), '
    '"remediation_steps": [str], "runbook_ref": str, '
    '"citations": [{"id": str, "score": number, "excerpt": str}]}'
)


class TriageAgent:
    def __init__(self, reasoner: Any, memory: Any, repo: Any) -> None:
        self.reasoner = reasoner
        self.memory = memory
        self.repo = repo

    def triage(self, incident: Incident, *, use_memory: bool = True,
               recalled: Optional[RecallResult] = None,
               on_token: Optional[Callable[[str], None]] = None) -> Brief:
        """Produce a triage Brief for the incident. ``on_token`` optionally streams text."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": TRIAGE_SYSTEM_PROMPT + _SCHEMA_HINT},
            {"role": "user", "content": _incident_user_msg(incident)},
        ]
        tools = TOOL_SPECS if use_memory else None
        seen_memories: list[dict[str, Any]] = list(_recalled_to_dicts(recalled)) if use_memory else []

        final_content: Optional[str] = None
        for _ in range(_MAX_ITERS):
            resp = self.reasoner.chat(messages, tools=tools)
            calls = resp.get("tool_calls") or []
            if calls:
                messages.append(_assistant_msg(resp.get("content"), calls))
                for call in calls:
                    name = call.get("name", "")
                    args = call.get("arguments", {})
                    result = dispatch(name, args, memory=self.memory, repo=self.repo)
                    if name == "recall_memory" and isinstance(result.get("memories"), list):
                        seen_memories.extend(result["memories"])
                    messages.append({
                        "role": "tool",
                        "tool_call_id": str(call.get("id", "")),
                        "name": name,
                        "content": json.dumps(result),
                    })
                continue
            final_content = resp.get("content")
            break

        parsed = _parse_brief(final_content)
        if parsed is not None:
            # The configured reasoner produced a usable brief — stamp it honestly.
            brief = parsed
            brief.llm_backend = getattr(self.reasoner, "backend_name", "local")
            brief.degraded = False
        else:
            # The configured reasoner produced NOTHING usable (empty/None content or
            # unparseable output — e.g. Groq unreachable, so chat() returned safe empties).
            # Fall back to a deterministic offline LocalReasoner so the brief stays grounded,
            # and stamp provenance for what ACTUALLY produced it: "local", not the configured
            # backend. ``degraded`` records that a non-local backend was configured but a local
            # fallback answered — the honesty the security pass required.
            brief = self._local_fallback(incident, seen_memories, use_memory)
            brief.llm_backend = "local"
            configured = getattr(self.reasoner, "backend_name", "local")
            brief.degraded = configured != "local"

        brief.memory_used = use_memory
        brief.memory_backend = getattr(self.memory, "backend_name", "local")
        if not use_memory:
            brief.citations = []
        if on_token:
            _stream_brief(brief, on_token)
        return brief

    def _local_fallback(self, incident: Incident, seen_memories: list[dict[str, Any]],
                        use_memory: bool) -> Brief:
        """Produce a grounded brief with a real, deterministic offline ``LocalReasoner`` when
        the configured reasoner returned nothing usable. Drives the same synthesis path a
        locally-configured Muninn would use (memories fed in as a tool result), then keeps the
        "(degraded)" summary prefix so the UI and logs stay honest about the fallback."""
        from .client import LocalReasoner  # local import: avoids any llm-package import cycle
        settings = getattr(self.reasoner, "settings", None) or _DEFAULT_SETTINGS
        local = LocalReasoner(settings)
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": _incident_user_msg(incident)},
        ]
        if use_memory and seen_memories:
            messages.append({
                "role": "tool", "name": "recall_memory",
                "content": json.dumps({"memories": seen_memories}),
            })
        resp = local.chat(messages, tools=None)
        brief = _parse_brief(resp.get("content")) or _degrade(seen_memories)
        if not brief.summary.startswith("(degraded)"):
            brief.summary = "(degraded) " + brief.summary
        return brief


# --- message construction ---------------------------------------------------

def _incident_user_msg(incident: Incident) -> str:
    parts = [
        "Live incident to triage:",
        incident.signature_text(),
        f"Severity: {incident.severity}. Status: {incident.status}.",
    ]
    if incident.log_excerpt:
        parts.append(f"Log excerpt:\n{incident.log_excerpt}")
    return "\n".join(parts)


def _assistant_msg(content: Optional[str], calls: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": str(c.get("id", "")),
                "type": "function",
                "function": {
                    "name": c.get("name", ""),
                    "arguments": json.dumps(c.get("arguments", {})),
                },
            }
            for c in calls
        ],
    }


# --- brief parsing + degradation --------------------------------------------

def _parse_brief(content: Optional[str]) -> Optional[Brief]:
    if not content or not isinstance(content, str):
        return None
    obj = _extract_json_object(content)
    if obj is None:
        return None
    try:
        steps = obj.get("remediation_steps", [])
        if not isinstance(steps, list):
            steps = [str(steps)]
        citations = obj.get("citations", [])
        if not isinstance(citations, list):
            citations = []
        conf = obj.get("confidence", 0.0)
        conf = float(conf) if isinstance(conf, (int, float, str)) and _is_number(conf) else 0.0
        return Brief(
            summary=str(obj.get("summary", "")),
            root_cause_hypothesis=str(obj.get("root_cause_hypothesis", "")),
            confidence=max(0.0, min(1.0, conf)),
            remediation_steps=[str(s) for s in steps],
            runbook_ref=str(obj.get("runbook_ref", "")),
            citations=[c for c in citations if isinstance(c, dict)],
        )
    except (TypeError, ValueError):
        return None


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    text = text.strip()
    # strip ```json ... ``` fences if present
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _is_number(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _recalled_to_dicts(recalled: Optional[RecallResult]):
    if not recalled:
        return []
    return [
        {"content": m.content, "score": m.score, "source": m.source, "type": m.type}
        for m in recalled.memories
    ]


def _degrade(memories: list[dict[str, Any]]) -> Brief:
    """Build a defensible Brief straight from recalled memories when the LLM output is
    unusable — the system stays useful even if the model misbehaves."""
    if not memories:
        return Brief(
            summary="No usable model output and no similar past incidents — cold start.",
            root_cause_hypothesis="Unknown — insufficient signal.",
            confidence=0.15,
            remediation_steps=[
                "Check recent deploys/config changes and consider a rollback.",
                "Inspect error rate, saturation, and dependency dashboards.",
            ],
        )
    top = memories[0]
    content = top.get("content", "")
    root = _line(content, "Root cause") or "See closest recalled incident."
    fix = _line(content, "Fix")
    runbook = _line(content, "Runbook")
    steps = [s.strip() for s in fix.split(";") if s.strip()] if fix else [
        "Apply the mitigation documented in the cited past incident.",
    ]
    score = float(top.get("score", 0.0) or 0.0)
    return Brief(
        summary=(f"(degraded) Grounded in {len(memories)} recalled incident(s); "
                 f"closest {top.get('source') or 'prior incident'} ({score:.0%})."),
        root_cause_hypothesis=root,
        confidence=round(min(0.9, max(0.2, score)), 2),
        remediation_steps=steps,
        runbook_ref=runbook,
        citations=[
            {"id": m.get("source", ""), "score": round(float(m.get("score", 0.0) or 0.0), 4)}
            for m in memories[:3] if m.get("source")
        ],
    )


def _line(content: str, prefix: str) -> str:
    for line in content.splitlines():
        s = line.strip()
        if s.lower().startswith(prefix.lower()):
            _, _, rest = s.partition(":")
            return rest.strip()
    return ""


def _stream_brief(brief: Brief, on_token: Callable[[str], None]) -> None:
    text = brief.summary
    if brief.root_cause_hypothesis:
        text += f"\n\nRoot cause: {brief.root_cause_hypothesis}"
    if brief.remediation_steps:
        text += "\n\nRemediation:\n" + "\n".join(
            f"{i}. {s}" for i, s in enumerate(brief.remediation_steps, 1)
        )
    for token in re.findall(r"\S+\s*", text):
        try:
            on_token(token)
        except Exception:  # noqa: BLE001 - a broken sink must not fail triage
            break
