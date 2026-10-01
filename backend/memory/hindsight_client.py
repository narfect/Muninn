"""Faithful HTTP client for the Hindsight REST API (standard-library ``urllib``).

Hindsight (by Vectorize) is an agent-memory system exposing retain / recall / reflect
over a memory bank. This client wraps those endpoints with no third-party dependency so
the core runs on the standard library alone.

Base URL (cloud): https://api.hindsight.vectorize.io   (OSS local: http://localhost:8888)
Auth:             Authorization: Bearer <HINDSIGHT_API_KEY>
Path namespace:   /v1/<namespace>/...            (namespace default = "default")

Endpoints (reconciled against the public Hindsight API docs + OpenAPI, Oct 2026)
--------------------------------------------------------------------------------
create_bank : PUT /v1/<ns>/banks/<bank_id>        body {"reflect_mission": str,
                                                        "disposition_*": int}
    The documented "create or update memory bank" call — a single idempotent PUT that
    creates the bank (if absent) or updates it in place, auto-filling omitted fields with
    defaults. Mission/disposition ride in this one body (``reflect_mission`` shapes the
    reflect voice; ``disposition_skepticism``/``_literalism``/``_empathy`` are 1-5, default
    3). Banks also auto-create on first write, so a non-confirming PUT is non-fatal — the
    first retain still materialises the bank.
retain      : POST /v1/<ns>/banks/<bank_id>/memories
    body {"items": [{"content": str, "document_id": str?, ...}]}  (optional ?async=true).
    ``document_id`` is the upsert key — retaining the same id REPLACES the prior version,
    so re-seeding does not duplicate. ``metadata``/``context`` are not in the public
    reference, so they are sent best-effort and dropped on a 422 (see :meth:`retain`).
recall      : POST /v1/<ns>/banks/<bank_id>/memories/recall   body {"query": str}
    -> {"results": [{"text": str, "id": str, ...}], "chunks": {...}}  (TEMPR fusion).
    The response exposes ``text`` + ``id`` per hit; a numeric per-item score is NOT part
    of the documented response shape (``item_score`` handles it defensively if present).
reflect     : POST /v1/<ns>/banks/<bank_id>/reflect            body {"query": str}
    -> {"text": str, "based_on": {...}, "usage": {...}}  (answer lives in ``text``).
health      : GET /health   (readiness; also /health/live, /health/ready)

Every method returns a plain dict and NEVER raises on network/5xx/parse errors —
transient failures come back as ``{"ok": False, "error": "..."}`` so the store layer
can degrade gracefully (NFR-2). Response parsing is defensive because field names vary
across API versions.

NOTE: these paths/shapes are reconciled from the official docs/OpenAPI; they were not
exercised against the live endpoint from the build sandbox (egress to the Hindsight host
was blocked). The offline suite covers behaviour; a guarded live smoke test
(tests/test_hindsight_live_smoke.py) exercises the real service when a key is present and
the host is reachable.
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

    def create_bank(self, bank_id: str, name: str = "", mission: str = "",
                    disposition: Optional[dict[str, int]] = None) -> dict[str, Any]:
        """Create or update the bank in a single documented call: ``PUT /v1/<ns>/banks/
        <bank_id>`` with ``reflect_mission`` + ``disposition_*`` in one body. Hindsight
        creates the bank if absent, updates it in place if present, and auto-fills any
        omitted field with its default — so this is idempotent and safe to call on every
        boot. (Banks also auto-create on first write, so even a non-confirming PUT is
        non-fatal; see ``HindsightStore.ensure_bank``.)

        The store treats 200/201 — and a 409-style "already exists" — as success. Returns a
        plain dict and NEVER raises.

        ``name`` is accepted for call-site compatibility but is NOT a documented field of
        the create/update body (``bank_id`` lives in the path), so it is not sent on the
        wire — sending an unknown field would risk the 422 this change exists to remove.
        """
        body: dict[str, Any] = {}
        if mission:
            body["reflect_mission"] = mission
        for trait in ("skepticism", "literalism", "empathy"):
            val = (disposition or {}).get(trait)
            if isinstance(val, int):
                body[f"disposition_{trait}"] = val
        return self._request("PUT", self._url(f"banks/{bank_id}"), body)

    def retain(self, bank_id: str, content: str, *, mem_type: str = "experience",
               metadata: Optional[dict] = None, is_async: bool = False,
               document_id: str = "") -> dict[str, Any]:
        """Store one memory. ``document_id`` is the upsert key (same id replaces the prior
        version — this is how re-seeding avoids duplicates).

        ``metadata``/``context`` are NOT part of the public API reference, so they are sent
        best-effort and, if the server rejects them with HTTP 422, the retain is retried
        with only the documented fields (``content`` + ``document_id``) so an unknown-field
        rejection never silently drops the memory. ``mem_type`` is carried in metadata
        (``muninn_type``) rather than as a top-level field (Hindsight infers fact type)."""
        base_item: dict[str, Any] = {"content": content}
        if document_id:
            base_item["document_id"] = str(document_id)

        rich_item = dict(base_item)
        # Hindsight stores string key/value metadata; coerce every value so a float/int
        # (e.g. mttr_minutes, created_at) can't trip a type-validation error.
        meta = {str(k): str(v) for k, v in (metadata or {}).items() if v not in (None, "")}
        meta.setdefault("muninn_type", mem_type)
        rich_item["metadata"] = meta
        src = (metadata or {}).get("source") or document_id
        if src:
            rich_item["context"] = str(src)

        url = self._url(f"banks/{bank_id}/memories")
        if is_async:
            url += "?async=true"
        resp = self._request("POST", url, {"items": [rich_item]})
        if resp.get("_status") == 422:
            log.info("retain 422 with optional metadata/context — retrying with documented "
                     "fields only (content%s)", "+document_id" if document_id else "")
            resp = self._request("POST", url, {"items": [base_item]})
        return resp

    def recall(self, bank_id: str, query: str, top_k: int = 5) -> dict[str, Any]:
        # top_k is applied client-side (store slices the ranked list): recall volume on the
        # wire is governed by the bank's own retrieval budget, so we send only the query.
        return self._request("POST", self._url(f"banks/{bank_id}/memories/recall"),
                             {"query": query})

    def reflect(self, bank_id: str, query: str) -> dict[str, Any]:
        return self._request("POST", self._url(f"banks/{bank_id}/reflect"),
                             {"query": query})

    def health(self) -> dict[str, Any]:
        # Readiness probe at the service root, outside the /v1/<ns> namespace.
        return self._request("GET", f"{self.base_url}/health")

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
        for key in ("text", "content", "document", "body"):
            v = item.get(key)
            if isinstance(v, str) and v:
                return v
        return ""

    @staticmethod
    def item_source(item: dict[str, Any]) -> str:
        """Best-effort provenance (incident id) for a recalled memory. The documented
        recall item carries ``id``/``text``; provenance may ride along as ``document_id``,
        ``context``, or a ``metadata.source`` — try them in order of specificity."""
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        for cand in (meta.get("source"), item.get("document_id"),
                     item.get("source"), item.get("context")):
            if isinstance(cand, str) and cand:
                return cand
        return ""

    @staticmethod
    def item_score(item: dict[str, Any]) -> Optional[float]:
        """Return the backend relevance score for a recall hit, or ``None`` when the
        response carries no numeric score (the public recall response documents ``text``
        + ``id`` but not a score field, so this probes the known/likely locations and lets
        the caller decide how to represent an unscored-but-recalled hit)."""
        scores = item.get("scores")
        if isinstance(scores, dict):
            for key in ("final", "score", "rerank", "relevance", "similarity", "final_score"):
                v = scores.get(key)
                if isinstance(v, (int, float)):
                    return float(v)
        for key in ("final_score", "score", "relevance", "similarity",
                    "rerank_score", "relevance_score"):
            v = item.get(key)
            if isinstance(v, (int, float)):
                return float(v)
        # a distance is the inverse of similarity — map into a [0,1] score
        dist = item.get("distance")
        if isinstance(dist, (int, float)):
            return float(max(0.0, min(1.0, 1.0 - dist)))
        return None
