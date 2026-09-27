"""Tiny dependency-free HTTP router (CONCRETE — transport plumbing, not business logic).

Provides ``Request``/``Response`` abstractions, ``{param}`` path matching, JSON and
SSE helpers, and defensive dispatch. Business logic lives in ``backend/api/routes.py``
(handlers) — this module only moves bytes and shapes errors. Handlers that are still
stubs raise ``NotImplementedError`` and are surfaced to the client as HTTP 501, so the
skeleton runs end-to-end before Claude Code implements the endpoints.
"""
from __future__ import annotations

import json
import logging
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("muninn.router")

# A streaming body is a generator yielding already-encoded SSE text chunks.
StreamFn = Callable[[], Iterator[str]]


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes = b""
    path_params: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        """Parse the JSON body; returns {} for an empty body, raises ValueError on bad JSON."""
        if not self.body:
            return {}
        return json.loads(self.body.decode("utf-8"))

    @classmethod
    def build(cls, method: str, raw_path: str, headers: dict[str, str], body: bytes) -> "Request":
        parsed = urlparse(raw_path)
        flat = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        return cls(method=method.upper(), path=parsed.path, query=flat, headers=headers, body=body)


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    stream: Optional[StreamFn] = None  # when set, server emits an SSE response

    @classmethod
    def json(cls, data: Any, status: int = 200) -> "Response":
        payload = json.dumps(data, default=str).encode("utf-8")
        return cls(status=status, body=payload,
                   headers={"Content-Type": "application/json; charset=utf-8"})

    @classmethod
    def text(cls, text: str, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> "Response":
        return cls(status=status, body=text.encode("utf-8"), headers={"Content-Type": content_type})

    @classmethod
    def error(cls, message: str, status: int = 400, code: str = "") -> "Response":
        return cls.json({"error": code or message, "message": message}, status=status)

    @classmethod
    def sse(cls, stream: StreamFn) -> "Response":
        return cls(status=200, stream=stream,
                   headers={"Content-Type": "text/event-stream",
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                            "X-Accel-Buffering": "no"})


class Router:
    """Matches (method, path) against a route table of (method, pattern, handler)."""

    def __init__(self, routes: list[tuple[str, str, Callable[[Request], Response]]]) -> None:
        self._routes = [(m.upper(), self._split(p), p, h) for (m, p, h) in routes]

    @staticmethod
    def _split(pattern: str) -> list[str]:
        return [seg for seg in pattern.strip("/").split("/") if seg != ""]

    def _match(self, method: str, path: str) -> Optional[tuple[Callable[[Request], Response], dict[str, str]]]:
        parts = [seg for seg in path.strip("/").split("/") if seg != ""]
        for r_method, r_segs, _pat, handler in self._routes:
            if r_method != method or len(r_segs) != len(parts):
                continue
            params: dict[str, str] = {}
            ok = True
            for r_seg, seg in zip(r_segs, parts):
                if r_seg.startswith("{") and r_seg.endswith("}"):
                    params[r_seg[1:-1]] = seg
                elif r_seg != seg:
                    ok = False
                    break
            if ok:
                return handler, params
        return None

    def dispatch(self, req: Request) -> Response:
        """Route the request; convert stubs to 501 and unexpected errors to 500 JSON."""
        matched = self._match(req.method, req.path)
        if matched is None:
            return Response.error("no such route", status=404, code="not_found")
        handler, params = matched
        req.path_params = params
        try:
            return handler(req)
        except NotImplementedError as exc:
            log.warning("handler not implemented: %s %s (%s)", req.method, req.path, exc)
            return Response.error(f"endpoint not implemented yet: {exc}", status=501,
                                  code="not_implemented")
        except ValueError as exc:
            return Response.error(f"bad request: {exc}", status=400, code="bad_request")
        except Exception:  # never leak a traceback to the client
            log.error("unhandled error on %s %s\n%s", req.method, req.path, traceback.format_exc())
            return Response.error("internal server error", status=500, code="internal_error")
