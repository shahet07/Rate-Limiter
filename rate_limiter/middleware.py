import json
from typing import Callable, Iterable

from .config import RateLimitConfig
from .store import Check, Decision, FixedWindowStore


StartResponse = Callable[[str, list], None]
WsgiApp = Callable[[dict, StartResponse], Iterable[bytes]]


class RateLimitMiddleware:
    """WSGI middleware that enforces per-org and per-endpoint rate limits."""

    def __init__(self, app: WsgiApp, config: RateLimitConfig, store: FixedWindowStore = None):
        self.app = app
        self.config = config
        self.store = store or FixedWindowStore()

    def __call__(self, environ: dict, start_response: StartResponse) -> Iterable[bytes]:
        org_id = environ.get("HTTP_X_ORG_ID", "anonymous")
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")

        resolved = self.config.resolve(org_id, method, path)
        decision = self.store.check_and_increment(
            [
                Check(
                    key=("org", resolved.org_id, resolved.tier),
                    limit=resolved.org,
                    label=f"org:{resolved.org_id}",
                ),
                Check(
                    key=("endpoint", resolved.org_id, f"{method}:{resolved.endpoint_name}"),
                    limit=resolved.endpoint,
                    label=f"endpoint:{resolved.endpoint_name}",
                ),
            ]
        )

        if not decision.allowed:
            return self._rate_limited(start_response, decision)

        return self.app(environ, start_response)

    def _rate_limited(self, start_response: StartResponse, decision: Decision) -> Iterable[bytes]:
        payload = {
            "error": "rate_limit_exceeded",
            "message": "Request rejected before application handler execution.",
            "limit_hit": decision.limit_label,
            "limit": {
                "requests": decision.limit_requests,
                "window_seconds": decision.window_seconds,
            },
            "retry_after_seconds": decision.retry_after_seconds,
        }
        body = json.dumps(payload, indent=2).encode("utf-8")
        start_response(
            "429 Too Many Requests",
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
                ("Retry-After", str(decision.retry_after_seconds)),
            ],
        )
        return [body]
