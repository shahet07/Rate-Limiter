import json
import os
import tempfile
import unittest
from wsgiref.util import setup_testing_defaults

from app import api_app
from rate_limiter.config import RateLimitConfig
from rate_limiter.middleware import RateLimitMiddleware
from rate_limiter.store import FixedWindowStore


class FakeClock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RateLimiterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.temp_dir.name, "rate_limits.json")
        self.write_config(
            {
                "default_tier": "standard",
                "tiers": {
                    "standard": {
                        "org": {"requests": 3, "window_seconds": 60},
                        "read": {"requests": 10, "window_seconds": 60},
                        "write": {"requests": 2, "window_seconds": 60},
                    },
                    "premium": {
                        "org": {"requests": 99, "window_seconds": 60},
                        "read": {"requests": 99, "window_seconds": 60},
                        "write": {"requests": 99, "window_seconds": 60},
                    },
                },
                "orgs": {
                    "standard-org": {"tier": "standard"},
                    "premium-org": {"tier": "premium"},
                },
                "endpoint_overrides": [
                    {"method": "POST", "path": "/api/sync", "limit": {"requests": 1, "window_seconds": 60}}
                ],
            }
        )
        self.clock = FakeClock()
        self.app = RateLimitMiddleware(
            api_app,
            RateLimitConfig(self.config_path),
            store=FixedWindowStore(clock=self.clock),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_write_endpoint_limit_returns_429_and_retry_after(self):
        first = self.request("POST", "/api/widgets", "standard-org")
        second = self.request("POST", "/api/widgets", "standard-org")
        third = self.request("POST", "/api/widgets", "standard-org")

        self.assertEqual(first["status"], "200 OK")
        self.assertEqual(second["status"], "200 OK")
        self.assertEqual(third["status"], "429 Too Many Requests")
        self.assertEqual(third["headers"]["Retry-After"], "60")
        self.assertEqual(third["body"]["limit_hit"], "endpoint:write")

    def test_org_limit_is_enforced_across_endpoints(self):
        self.assertEqual(self.request("GET", "/api/widgets", "standard-org")["status"], "200 OK")
        self.assertEqual(self.request("POST", "/api/widgets", "standard-org")["status"], "200 OK")
        self.assertEqual(self.request("GET", "/health", "standard-org")["status"], "200 OK")

        rejected = self.request("GET", "/api/widgets", "standard-org")
        self.assertEqual(rejected["status"], "429 Too Many Requests")
        self.assertEqual(rejected["body"]["limit_hit"], "org:standard-org")

    def test_endpoint_override_beats_generic_write_limit(self):
        self.assertEqual(self.request("POST", "/api/sync", "premium-org")["status"], "200 OK")
        rejected = self.request("POST", "/api/sync", "premium-org")

        self.assertEqual(rejected["status"], "429 Too Many Requests")
        self.assertEqual(rejected["body"]["limit_hit"], "endpoint:POST /api/sync")

    def test_window_expiry_allows_requests_again(self):
        self.assertEqual(self.request("POST", "/api/sync", "premium-org")["status"], "200 OK")
        self.assertEqual(self.request("POST", "/api/sync", "premium-org")["status"], "429 Too Many Requests")

        self.clock.advance(60)

        self.assertEqual(self.request("POST", "/api/sync", "premium-org")["status"], "200 OK")

    def test_config_reload_changes_limits_without_code_change(self):
        self.assertEqual(self.request("POST", "/api/widgets", "standard-org")["status"], "200 OK")
        self.assertEqual(self.request("POST", "/api/widgets", "standard-org")["status"], "200 OK")

        self.write_config(
            {
                "default_tier": "standard",
                "tiers": {
                    "standard": {
                        "org": {"requests": 10, "window_seconds": 60},
                        "read": {"requests": 10, "window_seconds": 60},
                        "write": {"requests": 3, "window_seconds": 60},
                    }
                },
                "orgs": {"standard-org": {"tier": "standard"}},
                "endpoint_overrides": [],
            }
        )

        allowed_after_reload = self.request("POST", "/api/widgets", "standard-org")
        self.assertEqual(allowed_after_reload["status"], "200 OK")

    def write_config(self, payload):
        with open(self.config_path, "w", encoding="utf-8") as config_file:
            json.dump(payload, config_file)

    def request(self, method, path, org_id):
        environ = {}
        setup_testing_defaults(environ)
        environ["REQUEST_METHOD"] = method
        environ["PATH_INFO"] = path
        environ["HTTP_X_ORG_ID"] = org_id

        captured = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = dict(headers)

        body = b"".join(self.app(environ, start_response))
        captured["body"] = json.loads(body.decode("utf-8"))
        return captured


if __name__ == "__main__":
    unittest.main()
