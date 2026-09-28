"""Phase 2 regression tests — transport & security hardening (S9, S10, SEC2, SEC3).

The transport-layer tests drive the real ``BaseHTTPRequestHandler`` over an in-process
``socket.socketpair()`` (no TCP port is bound, so this stays offline and sandbox-safe).
The SEC2 error-text tests exercise the router directly. Each test fails against the
pre-Phase-2 code and passes after the fix.
"""
import socket
import unittest

from backend.router import Request, Response, Router
from backend.server import make_handler


class _DummyServer:  # BaseHTTPRequestHandler only needs a truthy .server attribute
    pass


def _roundtrip(router: Router, raw: bytes, *, max_body_bytes: int = 1_048_576) -> bytes:
    """Feed one raw HTTP request to the handler and return the raw response bytes."""
    srv, cli = socket.socketpair()
    cli.settimeout(2.0)
    try:
        cli.sendall(raw)
        cli.shutdown(socket.SHUT_WR)  # signal EOF so keep-alive doesn't block on read 2
        make_handler(router, max_body_bytes=max_body_bytes)(srv, ("127.0.0.1", 0),
                                                            _DummyServer())
        srv.close()
        data = b""
        try:
            while True:
                chunk = cli.recv(65536)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
        return data
    finally:
        try:
            srv.close()
        except OSError:
            pass
        cli.close()


def _parse(raw: bytes) -> tuple[int, dict[str, str], bytes]:
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin1").split("\r\n")
    status = int(lines[0].split(" ", 2)[1])
    headers = {}
    for line in lines[1:]:
        k, _, v = line.partition(":")
        headers[k.strip().lower()] = v.strip()
    return status, headers, body


class TestS9BodyCap(unittest.TestCase):
    def _router(self):
        return Router([("POST", "/api/echo", lambda r: Response.json({"n": len(r.body)}))])

    def test_oversized_content_length_is_413(self):
        raw = (b"POST /api/echo HTTP/1.1\r\nHost: x\r\n"
               b"Content-Length: 5000000\r\n\r\nhello")
        status, _, body = _parse(_roundtrip(self._router(), raw, max_body_bytes=1000))
        self.assertEqual(status, 413)
        self.assertIn(b"too large", body)

    def test_body_under_cap_passes(self):
        raw = b"POST /api/echo HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\n\r\nhello"
        status, _, body = _parse(_roundtrip(self._router(), raw, max_body_bytes=1000))
        self.assertEqual(status, 200)
        self.assertIn(b'"n": 5', body)


class TestS10SSEHeadGuard(unittest.TestCase):
    def _router_and_flag(self):
        ran: list[bool] = []

        def gen():
            ran.append(True)
            yield "event: token\ndata: {\"t\": \"hi\"}\n\n"

        return Router([("GET", "/api/stream", lambda r: Response.sse(gen))]), ran

    def test_head_does_not_spawn_worker_or_stream_body(self):
        router, ran = self._router_and_flag()
        raw = b"HEAD /api/stream HTTP/1.1\r\nHost: x\r\n\r\n"
        status, headers, body = _parse(_roundtrip(router, raw))
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("content-length"), "0")
        self.assertEqual(body, b"")            # no SSE frames on a HEAD
        self.assertEqual(ran, [])              # worker never started

    def test_get_stream_runs_worker_and_closes_connection(self):
        router, ran = self._router_and_flag()
        raw = b"GET /api/stream HTTP/1.1\r\nHost: x\r\n\r\n"
        status, headers, body = _parse(_roundtrip(router, raw))
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("connection"), "close")
        self.assertIn(b"event: token", body)
        self.assertEqual(ran, [True])

    def test_sse_response_advertises_connection_close(self):
        self.assertEqual(Response.sse(lambda: iter(())).headers.get("Connection"), "close")


class TestSEC2ErrorTextNotLeaked(unittest.TestCase):
    def _router(self):
        def not_impl(_r):
            raise NotImplementedError("IMPLEMENT: secret_internal_hint 0xDEADBEEF")

        def domain_bad(_r):
            raise ValueError("remediation_steps must be a list")

        return Router([
            ("GET", "/api/notimpl", not_impl),
            ("POST", "/api/parse", lambda r: Response.json(r.json())),
            ("POST", "/api/domain", domain_bad),
        ])

    def test_not_implemented_message_is_generic(self):
        resp = self._router().dispatch(Request.build("GET", "/api/notimpl", {}, b""))
        self.assertEqual(resp.status, 501)
        self.assertNotIn(b"secret_internal_hint", resp.body)
        self.assertNotIn(b"0xDEADBEEF", resp.body)

    def test_bad_json_message_is_generic(self):
        resp = self._router().dispatch(
            Request.build("POST", "/api/parse", {}, b"{not valid json"))
        self.assertEqual(resp.status, 400)
        self.assertIn(b"invalid JSON body", resp.body)
        # the parser's own detail (line/column/"Expecting") must not leak
        for leak in (b"Expecting", b"line 1", b"column"):
            self.assertNotIn(leak, resp.body)

    def test_domain_validation_message_is_preserved(self):
        resp = self._router().dispatch(Request.build("POST", "/api/domain", {}, b""))
        self.assertEqual(resp.status, 400)
        self.assertIn(b"remediation_steps must be a list", resp.body)


class TestSEC3SecurityHeaders(unittest.TestCase):
    _EXPECT_CSP = ("default-src 'self'; object-src 'none'; base-uri 'self'; "
                   "frame-ancestors 'none'")

    def test_responses_carry_hardening_headers(self):
        router = Router([("GET", "/api/ok", lambda r: Response.json({"ok": True}))])
        _, headers, _ = _parse(_roundtrip(
            router, b"GET /api/ok HTTP/1.1\r\nHost: x\r\n\r\n"))
        self.assertEqual(headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(headers.get("x-frame-options"), "DENY")
        self.assertEqual(headers.get("referrer-policy"), "no-referrer")
        # CSP present on an API JSON response, and never opens script-src to inline.
        csp = headers.get("content-security-policy", "")
        self.assertEqual(csp, self._EXPECT_CSP)
        self.assertNotIn("unsafe-inline", csp)

    def test_static_file_response_carries_csp(self):
        # A static (non-API) GET goes through the server's static handler, not the router,
        # so assert the same hardening (CSP included) lands there too.
        router = Router([("GET", "/api/ok", lambda r: Response.json({"ok": True}))])
        status, headers, _ = _parse(_roundtrip(
            router, b"GET /index.html HTTP/1.1\r\nHost: x\r\n\r\n"))
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("content-security-policy"), self._EXPECT_CSP)
        self.assertEqual(headers.get("x-frame-options"), "DENY")


if __name__ == "__main__":
    unittest.main()
