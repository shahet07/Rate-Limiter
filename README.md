# Rate Limiter Middleware POC

This is a dependency-free Python proof of concept for a rate-limiting middleware layer. It sits in front of a tiny WSGI API and rejects requests before route handlers run.

The implementation is intentionally small: in-memory counters, JSON config, fixed-window limits, and a few sample routes. The README is part of the submission because the important engineering work here is knowing what this solves and what it does not.

## What It Does

- Enforces per-client/org limits using the `X-Org-Id` request header.
- Enforces separate endpoint limits for read methods and write methods.
- Supports endpoint-specific overrides, for example a stricter `POST /api/sync` limit.
- Returns `429 Too Many Requests` with a JSON body naming the limit that was hit.
- Sets the `Retry-After` response header.
- Loads limits from `config/rate_limits.json`, with hot reload on file modification.
- Runs as middleware; individual route handlers do not know about rate limiting.

## Project Layout

```text
rate-limiter-poc/
  app.py                         sample WSGI API and integration
  config/rate_limits.json        editable limits
  rate_limiter/config.py         config loader and validation
  rate_limiter/store.py          in-memory fixed-window counters
  rate_limiter/middleware.py     request interception and 429 response
  tests/test_rate_limiter.py     behavior tests
```

## Run It

Python 3.9+ is enough. No package install is required.

```bash
cd rate-limiter-poc
python3 -m unittest discover -s tests
python3 app.py
```

In another terminal:

```bash
curl -i -H 'X-Org-Id: noisy-client' -X POST http://localhost:8000/api/sync
```

Run that command six times inside a minute. The first five requests pass, and the sixth returns `429`.

Example rejected response:

```http
HTTP/1.0 429 Too Many Requests
Content-Type: application/json
Retry-After: 54
```

```json
{
  "error": "rate_limit_exceeded",
  "message": "Request rejected before application handler execution.",
  "limit_hit": "endpoint:POST /api/sync",
  "limit": {
    "requests": 5,
    "window_seconds": 60
  },
  "retry_after_seconds": 54
}
```

## Configuration

Limits live in `config/rate_limits.json`.

```json
{
  "default_tier": "standard",
  "tiers": {
    "standard": {
      "org": { "requests": 100, "window_seconds": 60 },
      "read": { "requests": 120, "window_seconds": 60 },
      "write": { "requests": 20, "window_seconds": 60 }
    },
    "premium": {
      "org": { "requests": 500, "window_seconds": 60 },
      "read": { "requests": 600, "window_seconds": 60 },
      "write": { "requests": 100, "window_seconds": 60 }
    }
  }
}
```

Org-specific overrides are supported:

```json
"noisy-client": {
  "tier": "standard",
  "overrides": {
    "org": { "requests": 10, "window_seconds": 60 },
    "write": { "requests": 3, "window_seconds": 60 }
  }
}
```

Endpoint-specific overrides are also supported:

```json
{
  "method": "POST",
  "path": "/api/sync",
  "limit": { "requests": 5, "window_seconds": 60 }
}
```

The middleware checks the config file modification time on each request. That is acceptable for this POC and means operators can change limits without editing code or restarting the process. In production I would cache reload checks, validate config through CI, and roll it out through the normal config system.

## Design Choices

This uses a fixed-window counter because it is simple, visible, and good enough for a 30-minute POC. Each request checks two limits:

1. The org-wide limit, such as `100 req/min` for a standard org.
2. The endpoint category or override limit, such as stricter write limits or `POST /api/sync`.

Both counters are checked under a lock. Counters are incremented only when the request is allowed. A rejected endpoint request does not also consume org quota.

The client identity is `X-Org-Id` for demo purposes. In a real service, that should come from trusted auth context after API key or token verification, not from a caller-controlled header.

## Known Failure Modes

In-memory rate limiting is useful for a local proof of concept, but it has sharp limits:

- Process restarts erase all counters. A client can get a fresh quota after deploys or crashes.
- Horizontal scaling breaks global enforcement. With four API instances, a `100 req/min` limit can effectively become up to `400 req/min` if traffic spreads evenly.
- Load balancer behavior changes enforcement. Sticky sessions make limits more stable per instance, but failover gives clients fresh counters elsewhere.
- Memory grows with active org and endpoint windows. This POC prunes expired counters on each request, but very high-cardinality org IDs or paths could still create pressure.
- Fixed windows allow boundary bursts. A client can send 100 requests at the end of one minute and 100 at the start of the next.
- Header-based org identity is not trustworthy. This must be wired to authenticated org identity before production use.
- Multi-threading is protected with a process-local lock, but multi-process servers still have independent counters.

## What Changes With Infrastructure

With Redis or another shared low-latency store, I would move counters out of process and enforce limits atomically with Redis Lua scripts or server-side transactions. That gives every API instance the same view of usage and preserves counters across app restarts.

I would also likely switch from fixed windows to token bucket, leaky bucket, or sliding-window counters depending on product needs. Token bucket is usually a good fit for APIs because it allows small bursts while preserving an average rate.

Config should move to the platform's config management system or feature flag service. Limit changes should be validated before rollout, observed with metrics, and applied consistently across instances.
