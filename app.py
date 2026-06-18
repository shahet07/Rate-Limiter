import json
import os
from wsgiref.simple_server import make_server

from rate_limiter import RateLimitConfig, RateLimitMiddleware


def api_app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/")

    routes = {
        ("GET", "/api/widgets"): list_widgets,
        ("POST", "/api/widgets"): create_widget,
        ("POST", "/api/sync"): start_sync,
        ("GET", "/health"): health,
    }
    handler = routes.get((method, path))

    if handler is None:
        return json_response(start_response, "404 Not Found", {"error": "not_found"})

    return json_response(start_response, "200 OK", handler(environ))


def list_widgets(_environ):
    return {"widgets": ["alpha", "beta"], "handler": "list_widgets"}


def create_widget(_environ):
    return {"created": True, "handler": "create_widget"}


def start_sync(_environ):
    return {"accepted": True, "handler": "start_sync"}


def health(_environ):
    return {"ok": True}


def json_response(start_response, status, payload):
    body = json.dumps(payload).encode("utf-8")
    start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
    return [body]


def build_app(config_path=None):
    config_path = config_path or os.path.join(os.path.dirname(__file__), "config", "rate_limits.json")
    return RateLimitMiddleware(api_app, RateLimitConfig(config_path))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app = build_app()
    with make_server("", port, app) as server:
        print(f"Serving rate-limiter POC on http://localhost:{port}")
        print("Try: curl -i -H 'X-Org-Id: noisy-client' -X POST http://localhost:8000/api/sync")
        server.serve_forever()
