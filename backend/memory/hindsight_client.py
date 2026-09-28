"""Faithful HTTP client for the Hindsight REST API (standard-library ``urllib``).

Hindsight (by Vectorize) is an agent-memory system exposing retain / recall / reflect
over a memory bank. This client wraps those endpoints with no third-party dependency so
the core runs on the standard library alone.

Base URL (cloud): https://api.hindsight.vectorize.io
Auth:             Authorization: Bearer <HINDSIGHT_API_KEY>
Path namespace:   /v1/<namespace>/...            (namespace default = "default")

Endpoints
---------
create_bank : POST /v1/<ns>/banks
    body {"bank_id": id, "name": name, "mission": mission?}
retain      : POST /v1/<ns>/banks/<bank_id>/memories
    body {"items": [{"content": str, "type": world|experience|observation,
                     "metadata": {...}}]}   (optional ?async=true -> {"operation_id": ...})
recall      : POST /v1/<ns>/banks/<bank_id>/recall
    body {"query": str, "top_k": int?}   -> ranked memories (TEMPR fusion)
reflect     : POST /v1/<ns>/banks/<bank_id>/reflect
    body {"query": str}                  -> synthesized answer (shaped by disposition)
health      : GET /healthz  (alias /health) -> 200 ok / 503

Every method returns a plain dict and NEVER raises on network/5xx/parse errors —
transient failures come back as ``{"ok": False, "error": "..."}`` so the store layer
can degrade gracefully (NFR-2). Response parsing is defensive because field names vary
across API versions.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Optional

log = logging.getLogger("muninn.hindsight")


class HindsightClient:
    def __init__(self, base_url: str, api_key: str = "", namespace: str = "default",
                 timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.namespace = namespace
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return f"{self.base_url}/v1/{self.namespace}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _request(self, method: str, url: str, body: Optional[dict] = None) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8") or "{}"
                parsed = json.loads(raw) if raw.strip() else {}
                if not isinstance(parsed, dict):
                    parsed = {"data": parsed}
                parsed.setdefault("ok", True)
                parsed["_status"] = resp.status
                return parsed
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")
            except Exception:  # noqa: BLE001 - best-effort body read
                pass
            log.warning("hindsight %s %s -> HTTP %s", method, url, exc.code)
            return {"ok": False, "error": f"http {exc.code}", "detail": detail,
                    "_status": exc.code}
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            log.warning("hindsight %s %s unreachable: %s", method, url, exc)
            return {"ok": False, "error": "unreachable", "detail": str(exc)}
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("hindsight %s %s bad response: %s", method, url, exc)
            return {"ok": False, "error": "bad_response", "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001 - the store contract is "never raise" (S6)
            # Any unexpected error (e.g. socket.timeout on older Pythons, an encoding
            # error) must still degrade to a safe dict so recall/reflect never hard-fail.
            log.warning("hindsight %s %s unexpected error: %s", method, url, exc)
            return {"ok": False, "error": "unexpected", "detail": str(exc)}

    def create_bank(self, bank_id: str, name: str, mission: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {"bank_id": bank_id, "name": name}
        if mission:
            body["mission"] = mission
        return self._request("POST", self._url("banks"), body)

    def retain(self, bank_id: str, content: str, *, mem_type: str = "experience",
               metadata: Optional[dict] = None, is_async: bool = False) -> dict[str, Any]:
        item: dict[str, Any] = {"content": content, "type": mem_type}
        if metadata:
            item["metadata"] = metadata
        url = self._url(f"banks/{bank_id}/memories")
        if is_async:
            url += "?async=true"
        return self._request("POST", url, {"items": [item]})

    def recall(self, bank_id: str, query: str, top_k: int = 5) -> dict[str, Any]:
        return self._request("POST", self._url(f"banks/{bank_id}/recall"),
                             {"query": query, "top_k": top_k})

    def reflect(self, bank_id: str, query: str) -> dict[str, Any]:
        return self._request("POST", self._url(f"banks/{bank_id}/reflect"),
                             {"query": query})

    def health(self) -> dict[str, Any]:
        # /healthz lives at the service root, outside the /v1/<ns> namespace.
        return self._request("GET", f"{self.base_url}/healthz")

    # --- defensive parsing helpers (field names vary by API version) --------
    @staticmethod
    def extract_memories(resp: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("memories", "results", "items", "data"):
            val = resp.get(key)
            if isinstance(val, list):
                return val
        return []

    @staticmethod
    def item_content(item: dict[str, Any]) -> str:
        for key in ("content", "text", "document", "body"):
            v = item.get(key)
            if isinstance(v, str) and v:
                return v
        return ""

    @staticmethod
    def item_score(item: dict[str, Any]) -> float:
        for key in ("score", "relevance", "similarity"):
            v = item.get(key)
            if isinstance(v, (int, float)):
                return float(v)
        # a distance is the inverse of similarity — map into a [0,1] score
        dist = item.get("distance")
        if isinstance(dist, (int, float)):
            return float(max(0.0, min(1.0, 1.0 - dist)))
        return 0.0
