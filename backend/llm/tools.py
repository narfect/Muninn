"""Agent tool schema + dispatch (FR-11/12).

Tools exposed to the LLM (OpenAI function-calling schema). Each maps to a Python
handler that reads from the memory layer / repository. Keep tools small + typed.

  recall_memory(query: str, top_k: int = 5)
      -> {"memories": [{content, score, source, type}]}   # Hindsight recall
  reflect_memory(question: str)
      -> {"answer": str}                                   # Hindsight reflect
  get_runbook(service: str)
      -> {"runbooks": [{id, title, steps}]}                # from repository
  get_service(name: str)
      -> {"name","tier","dependencies","oncall"}           # from repository

``dispatch`` validates arguments and NEVER raises: an unknown tool, non-dict args, a
missing/blank required field, or an exception thrown by a handler all come back as
``{"error": "..."}``. The agent surfaces that error back to the model and keeps going,
which is exactly the robustness FR-12 demands.
"""
from __future__ import annotations

from typing import Any

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "recall_memory",
            "description": (
                "Recall the most similar past incidents from institutional memory "
                "(Hindsight). Use the incident signature/symptom as the query. Returns "
                "scored, cited memories you MUST ground your brief in."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language incident signature to search for.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "How many memories to return (default 5).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reflect_memory",
            "description": (
                "Ask memory (Hindsight reflect) to synthesize a cross-incident pattern "
                "or likely root cause for a question. Returns a short synthesized answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to reflect on, e.g. the incident signature.",
                    },
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_runbook",
            "description": "Fetch operational runbooks registered for a given service.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {
                        "type": "string",
                        "description": "Service name, e.g. 'checkout-api'.",
                    },
                },
                "required": ["service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_service",
            "description": "Fetch metadata (tier, dependencies, on-call) for a service.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Service name, e.g. 'checkout-api'.",
                    },
                },
                "required": ["name"],
            },
        },
    },
]

TOOL_NAMES = frozenset(spec["function"]["name"] for spec in TOOL_SPECS)


def _require_str(arguments: dict[str, Any], key: str) -> str:
    val = arguments.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"missing or empty required string argument '{key}'")
    return val.strip()


def dispatch(name: str, arguments: dict[str, Any], *, memory: Any, repo: Any) -> dict[str, Any]:
    """Execute a tool by name. Returns a JSON-serializable dict; never raises."""
    if not isinstance(name, str) or name not in TOOL_NAMES:
        return {"error": f"unknown tool '{name}'"}
    if not isinstance(arguments, dict):
        return {"error": f"tool '{name}' expects a JSON object of arguments"}
    try:
        if name == "recall_memory":
            query = _require_str(arguments, "query")
            top_k = arguments.get("top_k", 5)
            top_k = int(top_k) if isinstance(top_k, (int, float, str)) and str(top_k).strip().lstrip("-").isdigit() else 5
            top_k = max(1, min(top_k, 20))
            rc = memory.recall(query, top_k=top_k)
            return {
                "backend": rc.backend,
                "memories": [
                    {
                        "content": m.content,
                        "score": round(m.score, 4),
                        "source": m.source,
                        "type": m.type,
                    }
                    for m in rc.memories
                ],
            }
        if name == "reflect_memory":
            question = _require_str(arguments, "question")
            return {"answer": memory.reflect(question)}
        if name == "get_runbook":
            service = _require_str(arguments, "service")
            runbooks = repo.find_runbooks_for_service(service)
            return {
                "runbooks": [
                    {"id": rb.id, "title": rb.title, "steps": rb.steps}
                    for rb in runbooks
                ]
            }
        if name == "get_service":
            svc_name = _require_str(arguments, "name")
            svc = repo.get_service(svc_name)
            if svc is None:
                return {"error": f"service '{svc_name}' not found"}
            return {
                "name": svc.name,
                "tier": svc.tier,
                "dependencies": svc.dependencies,
                "oncall": svc.oncall,
            }
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - a tool must never crash the agent (FR-12)
        return {"error": f"tool '{name}' failed: {exc}"}
    return {"error": f"unknown tool '{name}'"}
