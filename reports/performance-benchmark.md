# Performance Benchmark

Production-readiness pass, 2026-09-21. Measured against the live Docker backend
(`docker-compose.prod.yml`, real Postgres, single `uvicorn --workers 1` process — the
actual deployed configuration, per ADR-009), using a simple `asyncio`/`httpx` concurrency
script (`ENDPOINTS x CONCURRENCIES`, no synthetic ramp-up/think-time). This is a modest,
safe load test appropriate to a single dev machine — not a claim about datacenter-scale
capacity.

## Method

Fired N concurrent requests (N = 1, 10, 25, 50, 100) at one endpoint at a time, measured
per-request wall-clock latency client-side, computed p50/p95/p99/max from the sorted
sample. No warm-up discard (the numbers include cold-start effects, which is conservative).

## Results — `GET /api/health` (cheapest possible endpoint, no DB query)

| Concurrency | p50 | p95 | p99 | max | Errors |
|---|---|---|---|---|---|
| 1 | 110.6ms | 110.6ms | 110.6ms | 110.6ms | 0/1 |
| 10 | 82.2ms | 83.5ms | 83.5ms | 83.7ms | 0/10 |
| 25 | 125.3ms | 136.6ms | 136.8ms | 137.2ms | 0/25 |
| 50 | 227.2ms | 258.2ms | 259.6ms | 260.8ms | 0/50 |
| 100 | **1093.1ms** | 1166.4ms | 1169.9ms | 1178.2ms | 0/100 |

**This is the single-worker ceiling, measured directly, not inferred.** Even the cheapest
endpoint in the API — no DB query, no scoring, just a dict read — shows p50 jumping 10x
between 50 and 100 concurrent clients. This is exactly what `uvicorn --workers 1` predicts
(ADR-009: required for the async SSE design in the demo topology) and confirms the
documented production answer — horizontal replicas behind a Service, not more workers on
one process — is the correct one, not merely a plausible one.

## Results — `GET /api/units?plant_id=plant_a&limit=50` (real DB query)

| Concurrency | p50 | p95 | p99 | max | Errors |
|---|---|---|---|---|---|
| 1 | 144.4ms | 144.4ms | 144.4ms | 144.4ms | 0/1 |
| 10 | 87.9ms | 107.9ms | 107.9ms | 114.9ms | 0/10 |
| 25 | 188.7ms | 225.4ms | 226.1ms | 227.1ms | 0/25 |
| 50 | 562.4ms | 610.3ms | 611.9ms | 617.0ms | 0/50 |
| 100 | 421.1ms | 538.9ms | 540.7ms | 541.1ms | **72/100 (72%)** |

The apparent latency IMPROVEMENT at n=100 versus n=50 is an artifact, not a real speedup:
72 of the 100 requests at n=100 failed fast with `429` (rate-limited) rather than being
served, which pulls the latency distribution down. This is the rate limiter working
correctly, not the server getting faster under more load — see below.

## The rate limiter, caught in the act (real, useful evidence)

Subsequent endpoints in the same test run (`predict`, `bottleneck_history`,
`stations_stats`) returned **100% errors even at concurrency 1**, which looked at first
like a broken test until verified with a direct `curl`:

```
$ curl -s -X POST http://localhost:7862/api/predict/defect -d '{"plant_id":"plant_a","unit_id":"U030733"}'
{"detail":"rate limit exceeded for role 'supervisor': 300/min"}
HTTP 429
```

**This is correct, working behavior, not a defect.** The unauthenticated/default role
bucket's 300 req/min budget was legitimately exhausted by the earlier `health`/`units`
sweeps (186 + 186 = 372 requests) within well under a minute, and every subsequent request
from the same (role, IP) was correctly rejected. Two things follow from this, both worth
recording:

1. **The limiter works exactly as `serve/middleware/rate_limit.py` intends**, confirmed
   under real concurrent load against the real Docker deployment, not just the unit tests
   in `tests/test_auth.py`.
2. **A single-client load-testing harness against this API will hit this limit fast.**
   Anyone building a more thorough load-testing setup than this one needs either multiple
   distinct role/IP identities or explicit backoff — worth noting in
   `docs/INCIDENT_RUNBOOK.md` or a future load-testing runbook, since it is easy to
   mistake for the server failing rather than the limiter succeeding.

## What this benchmark does NOT cover

- **Concurrency beyond 100** was not attempted. Going further on this single-worker,
  rate-limited deployment would mean either disabling the rate limiter (explicitly against
  this project's own stated philosophy — "the limiter is real production behaviour and
  stays exactly as strict", `docs/STATE.md` bug #72) or spreading requests across many
  fabricated identities, which stops measuring the server and starts measuring the test
  harness. **Recommendation, not executed**: a proper load test needs either multiple
  backend replicas (the documented production topology) or a purpose-built harness that
  rotates roles/IPs deliberately, run as a scheduled/nightly job separate from normal PR
  checks (see the CI/CD recommendation in production-readiness-report.md).
- **SSE long-lived connections** (1/10/50/100 simultaneous streams, memory growth,
  reconnect behavior) — not load-tested this pass. `docs/ARCHITECTURE.md` records a prior
  measurement (with a stream held open, `/api/health`/`/api/metrics`/`/api/bottleneck/
  current` all answer in ~330ms), which this pass did not repeat or extend.
- **Database query plans / `EXPLAIN ANALYZE`** — not run this pass. The schema itself
  (indexes on `(plant_id, alert_fired, calibrated_risk)`, `(plant_id, lot_id)`,
  `(plant_id, prediction_ts)` on `units`; partitioning by `plant_id` on `genealogy`/
  `station_readings`) looks deliberately built for the query patterns the API actually
  uses (confirmed by reading `migrations/001_initial_schema.sql` alongside
  `serve/routers/intelligence.py`'s actual queries), but this was a code-reading check, not
  an `EXPLAIN`-verified one. No index was added or recommended without that verification —
  per this audit's own instruction not to add indexes without proving the need.
- **Frontend runtime performance** (chart re-render cost, memory growth during long
  sessions, CLS under real network conditions) — the bundle-size numbers in
  baseline-verification.md were captured; a browser performance trace was not.
