"""Router transport tests (concrete layer — green)."""
import json
import unittest

from backend.router import Request, Response, Router


def _ok(_req):
    return Response.json({"ok": True})


def _boom(_req):
    raise NotImplementedError("IMPLEMENT: something")


def _bad(_req):
    raise ValueError("nope")


class TestRouter(unittest.TestCase):
    def _router(self):
        return Router([
            ("GET", "/api/incidents", _ok),
            ("GET", "/api/incidents/{id}", lambda r: Response.json({"id": r.path_params["id"]})),
            ("POST", "/api/incidents/{id}/resolve", _ok),
            ("GET", "/api/boom", _boom),
            ("POST", "/api/bad", _bad),
        ])

    def test_request_build_parses_query_and_path(self):
        req = Request.build("GET", "/api/incidents?status=open", {}, b"")
        self.assertEqual(req.path, "/api/incidents")
        self.assertEqual(req.query.get("status"), "open")

    def test_request_json_empty_body(self):
        self.assertEqual(Request.build("POST", "/x", {}, b"").json(), {})

    def test_path_param_match(self):
        r = self._router()
        resp = r.dispatch(Request.build("GET", "/api/incidents/42", {}, b""))
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(resp.body)["id"], "42")

    def test_unknown_route_404(self):
        resp = self._router().dispatch(Request.build("GET", "/api/nope", {}, b""))
        self.assertEqual(resp.status, 404)

    def test_wrong_method_404(self):
        resp = self._router().dispatch(Request.build("DELETE", "/api/incidents", {}, b""))
        self.assertEqual(resp.status, 404)

    def test_not_implemented_maps_to_501(self):
        resp = self._router().dispatch(Request.build("GET", "/api/boom", {}, b""))
        self.assertEqual(resp.status, 501)
        self.assertEqual(json.loads(resp.body)["error"], "not_implemented")

    def test_value_error_maps_to_400(self):
        resp = self._router().dispatch(Request.build("POST", "/api/bad", {}, b""))
        self.assertEqual(resp.status, 400)

    def test_response_helpers(self):
        self.assertEqual(Response.json({"a": 1}).status, 200)
        self.assertEqual(Response.error("x", status=422).status, 422)
        self.assertIn("event-stream", Response.sse(lambda: iter(())).headers["Content-Type"])


if __name__ == "__main__":
    unittest.main()
