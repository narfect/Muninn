"""Muninn HTTP server bootstrap (CONCRETE — wiring + transport, not business logic).

Wires the layered app together and serves it with the stdlib threaded HTTP server:

    settings -> init_db -> Repository
             -> build_memory_store(settings)      (Hindsight or offline Local)
             -> build_reasoner(settings)           (Groq or offline Local)
             -> TriageAgent(reasoner, memory, repo)
             -> IncidentService / TriageService / MetricsService
             -> Routes(ctx).table() -> Router
             -> ThreadingHTTPServer  (JSON API under /api/*, static SPA otherwise)

The API handlers themselves live in ``backend/api/routes.py`` and are stubs until
Claude Code implements them (they return HTTP 501 via the router). This bootstrap is
intentionally complete so ``python -m backend.server`` starts a running skeleton.
"""
from __future__ import annotations

import logging
import mimetypes
import os
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .api import Routes
from .config import Settings, settings as default_settings
from .db import Repository, init_db
from .llm import build_reasoner
from .llm.agent import TriageAgent
from .memory import build_memory_store
from .router import Request, Response, Router
from .services.incidents import IncidentService
from .services.metrics import MetricsService
from .services.triage import TriageService

log = logging.getLogger("muninn.server")

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


@dataclass
class AppContext:
    """Container for the wired application components (handed to Routes)."""

    settings: Settings
    repo: Repository
    memory: Any
    reasoner: Any
    agent: TriageAgent
    incidents: IncidentService
    triage: TriageService
    metrics: MetricsService


def build_context(settings: Settings = default_settings) -> AppContext:
    """Construct and wire every layer. Safe to call at import time (no I/O beyond db init)."""
    init_db(settings.db_path)
    repo = Repository(settings.db_path)
    memory = build_memory_store(settings)
    reasoner = build_reasoner(settings)
    agent = TriageAgent(reasoner=reasoner, memory=memory, repo=repo)
    incidents = IncidentService(repo=repo, memory=memory)
    triage = TriageService(memory=memory, agent=agent, repo=repo, top_k=settings.recall_top_k)
    metrics = MetricsService(repo=repo, memory=memory)
    return AppContext(settings=settings, repo=repo, memory=memory, reasoner=reasoner,
                      agent=agent, incidents=incidents, triage=triage, metrics=metrics)


def build_router(ctx: AppContext) -> Router:
    return Router(Routes(ctx).table())


def _safe_static_path(url_path: str) -> str | None:
    """Resolve a URL path to a file inside STATIC_DIR, or None if it escapes/doesn't exist."""
    rel = "index.html" if url_path in ("", "/") else url_path.lstrip("/")
    target = os.path.normpath(os.path.join(STATIC_DIR, rel))
    if not target.startswith(os.path.abspath(STATIC_DIR) + os.sep) and target != os.path.abspath(STATIC_DIR):
        return None  # path traversal attempt
    return target if os.path.isfile(target) else None


def make_handler(router: Router):
    class MuninnHandler(BaseHTTPRequestHandler):
        server_version = "Muninn/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # route through logging
            log.info("%s - %s", self.address_string(), fmt % args)

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", 0) or 0)
            return self.rfile.read(length) if length else b""

        def _write(self, resp: Response) -> None:
            if resp.stream is not None:
                self._write_stream(resp)
                return
            self.send_response(resp.status)
            for k, v in resp.headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(resp.body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(resp.body)

        def _write_stream(self, resp: Response) -> None:
            self.send_response(resp.status)
            for k, v in resp.headers.items():
                self.send_header(k, v)
            self.end_headers()
            try:
                for chunk in resp.stream():  # type: ignore[misc]
                    self.wfile.write(chunk.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                log.info("client disconnected from stream")

        def _serve_static(self) -> None:
            target = _safe_static_path(self.path.split("?", 1)[0])
            if target is None:
                # SPA fallback: unknown non-API GET -> index.html if present
                index = os.path.join(STATIC_DIR, "index.html")
                target = index if os.path.isfile(index) else None
            if target is None:
                self._write(Response.error("not found", status=404, code="not_found"))
                return
            ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
            with open(target, "rb") as fh:
                data = fh.read()
            self._write(Response(status=200, body=data, headers={"Content-Type": ctype}))

        def _handle(self, method: str) -> None:
            path = self.path.split("?", 1)[0]
            if method == "GET" and not path.startswith("/api/"):
                self._serve_static()
                return
            req = Request.build(method, self.path, dict(self.headers), self._read_body())
            self._write(router.dispatch(req))

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def do_HEAD(self) -> None:
            self._handle("GET")

    return MuninnHandler


def run(settings: Settings = default_settings) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ctx = build_context(settings)
    router = build_router(ctx)
    httpd = ThreadingHTTPServer((settings.host, settings.port), make_handler(router))
    log.info("Muninn listening on http://%s:%d  (memory=%s, llm=%s)",
             settings.host, settings.port,
             settings.resolved_memory_backend(), settings.resolved_llm_backend())
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        httpd.server_close()


def main() -> None:
    run()


if __name__ == "__main__":
    main()
