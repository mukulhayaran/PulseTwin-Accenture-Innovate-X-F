# End-to-End Test Report

Production-readiness pass, 2026-09-21.

## Production Docker stack — the headline result of this pass

`docs/STATE.md` had carried, unchanged across multiple sessions, a note that
`docker compose -f docker-compose.prod.yml up --build` had never been run end to end
because no Docker daemon was available in prior sessions' sandboxes. This session's
environment has a live Docker Desktop daemon (confirmed via `docker version` — Server:
Docker Desktop 4.83.0, Engine 29.6.2). This is the first time this exact command has been
run against this repository.

### `docker compose config`

**PASS.** Validates cleanly (exit 0). Services: `backend`, `ingestion`, `migrate`, `mqtt`,
`postgres`, `redis`. Dependency graph is sound: `postgres`/`redis` healthy → `migrate`
completes → `backend`/`ingestion` start; `mqtt` only needs to be started, not healthy
(mosquitto has no built-in healthcheck in this image).

### `docker compose build`

**FAILED on first attempt.** `serve/Dockerfile`'s `pip install -r serve/requirements.txt`
hit `ResolutionImpossible`:

```
fastapi 0.115.0 depends on starlette<0.39.0 and >=0.37.2
prometheus-fastapi-instrumentator 8.1.0 depends on starlette<2.0.0 and >=1.0.0
```

These ranges are disjoint — no version of starlette satisfies both, so the pinned
`serve/requirements.txt` has never been installable from a clean environment. This has been
invisible for as long as it's existed because (a) no CI pipeline exists to catch it, and
(b) local development never does a clean install — every `pytest` run and `make
dev-backend` invocation used whatever fastapi/pydantic/uvicorn happened to already be
installed in the ambient interpreter (0.139.0/2.13.4/0.51.0 — see baseline-verification.md),
which silently diverged from the file's pins at some point. **Root cause proven, fix
contained**: repinned `fastapi`/`uvicorn`/`pydantic` in `serve/requirements.txt` to the
versions this project has actually been tested against throughout its history.

**PASSED on second attempt** after the fix. All three images
(`pulsetwinaccenture-backend`, `pulsetwinaccenture-migrate`, `pulsetwinaccenture-ingestion`)
built successfully.

### `docker compose up`

**PASS**, with one environment-specific accommodation: host port 7860 was already held by
an unrelated process on this machine (a separate, known issue — see
`reports/reliability-chaos-report.md`), so the backend's published port was remapped to
7862 via a local, uncommitted compose override for testing purposes only; the container's
internal port (7860) and the compose file itself were not changed.

Startup sequence observed directly, in order: `mqtt`/`redis`/`postgres` created → `redis`
and `postgres` reach `healthy` → `migrate` runs and exits 0 → `backend` and `ingestion`
start. `backend` reached Docker's own `healthy` state (its healthcheck curls
`/api/health` from inside the container).

**Migration behavior, verified against a REAL pre-existing volume** (this machine's
Postgres volume predates this session by 5 days, with migrations 001-004 already applied):

```
[migrate] skip  001_initial_schema.sql (already applied)
[migrate] skip  002_audit_log.sql (already applied)
[migrate] skip  003_model_registry.sql (already applied)
[migrate] skip  004_drift_metrics.sql (already applied)
[migrate] apply 005_genai_triage.sql
[migrate] apply 006_genai_copilot.sql
[migrate] applied 2 migration(s): 005_genai_triage.sql, 006_genai_copilot.sql
```

Idempotent tracking works correctly: already-applied migrations are skipped, only the two
new ones (from the copilot feature, added after this volume was first created) apply.

**Backend startup log, against real Postgres:**

```
[pulsetwin] backend=postgresql 2 plants, 2 scorers, RSS=79.253504 (proc)
```

Both plants' scorers loaded successfully against Postgres for the first time ever tested.

`GET /api/health` through the container: `{"status":"ok",...,"database":"postgresql"}` —
200 OK. `GET /api/system/health` (manager role):
`"database":{"type":"postgresql","connected":true,"latency_ms":95.4}` — a real,
measured round-trip against the containerized Postgres, not a mock.

### Full data path: simulated event → MQTT → ingestion → PostgreSQL → backend API

**PASS — proven live, the first time in this repository's history**
(`docs/STATE.md` NEXT ACTION item #3 named this exact path as never run against a real
broker).

1. Ran `python -m ingestion.mqtt_publisher --plant-id plant_a --broker localhost --port
   1883 --speed 200 --no-loop` from the host (port 1883 is published to the host by the
   compose file).
2. `ingestion/mqtt_subscriber.py`, running inside the `ingestion` container and connected
   to the same broker over the Docker network, received and processed every event,
   printing one log line per sensor status transition (`[subscriber] sensor_health
   plant_a/S18/film_thickness_um -> healthy`, etc.) and writing to Postgres.
3. Verified directly against the database: `SELECT count(*) FROM sensor_health` went from
   0 to 55 (one row per distinct `(station_id, sensor_type)` first observed) after the
   publish burst.
4. Verified the LAST leg of the path — backend reading what ingestion wrote — via
   `GET /api/system/sensor-dropouts?plant_id=plant_a` returning `sensor_dropouts_active: 0`
   consistent with all 55 sensors having reported `healthy`, sourced from the same live
   Postgres table.

**Partially inconclusive: forced sensor-dropout via the `control/dropout` MQTT topic.**
Published `{"station_id": 8, "signal": "vibration_mm_s", "duration_s": 45}` to
`pulsetwin/plant_a/control/dropout` while a long-running (`--loop`) publisher was
connected and confirmed subscribed (an `ESTABLISHED` TCP connection to the broker was
independently verified via `netstat`). No status transition to `dropout` was observed in
`sensor_health` within the ~60-second test window. This is reported as **inconclusive, not
as a confirmed defect**: `ingestion/mqtt_subscriber.py::_transition_status` only writes a
row on a status CHANGE (confirmed by reading the code), and the forced dropout only takes
effect the next time the publisher's replay reaches that specific
`(station, signal)` in its playback position — which depends on where in the artifact's
stream file the publisher currently is, a timing detail this test did not control for.
**Recommend**: a follow-up test that either starts the publisher at a timestamp close to a
known S8-vibration event, or that patches `mqtt_publisher.py` to apply a forced dropout
immediately on receipt rather than waiting for the next natural occurrence, to make this
path deterministically testable.

### What was NOT exercised in the Docker stack

- Broker/Postgres/Redis restart mid-flight (only the `backend` container was
  killed/restarted — see reliability-chaos-report.md). Time did not allow repeating this
  for every service.
- Sustained/burst throughput measurement of the live MQTT path (events/sec, p50/p95/p99
  processing latency) — the one live-path test run was a short, illustrative burst, not a
  sustained load test. See performance-benchmark.md for what WAS load-tested (the HTTP API,
  not the MQTT path).
- Kubernetes/Terraform deployment — `infrastructure/terraform/main.tf` has never been
  `terraform apply`'d (stated in its own header) and this pass did not change that; no
  cloud credentials were available or sought, consistent with "no operation requiring
  credentials or external cost."

## Browser E2E (Playwright, real Chromium)

All 6 routes loaded (`domcontentloaded`) against the local dev server
(`localhost:3000`, backend `localhost:7861`), console and network captured for ~2.5s of
settle time per route:

| Route | Console errors | Failed/4xx/5xx requests |
|---|---|---|
| `/` | 0 | 0 |
| `/supervisor` | 0 | 0 |
| `/manager` | 0 | 0 |
| `/leadership` | 0 | 0 |
| `/ops` | 0 | 0 |
| `/copilot` | 1 (browser-logged) | 1 — `GET /api/model/health` → 403 |

The one 403 is expected, handled RBAC behavior, not an unhandled error: `/copilot` sets its
role to `manager` (`frontend/app/copilot/page.tsx`), and `/api/model/health` requires
`leadership` per `docs/SECURITY.md`'s table. `AiTrustPanel.tsx` catches this
(`.catch(() => setGated(true))`) and renders a gated state — confirmed by reading the
component. The console entry is the browser's own automatic network-panel log for any
non-2xx response; it appears regardless of whether application code handles the rejection,
and is not evidence of a bug.

**Not covered this pass** (time-bounded scope): per-route interactive workflows (clicking
through containment/what-if/promote-rollback/copilot-ask flows), empty/loading/error-state
injection, responsive-layout breakpoints, and keyboard-navigation audits. `docs/STATE.md`
already records a prior, more exhaustive Playwright pass (2026-09-16 and 2026-09-19) that
covered these; this pass's browser testing was a targeted regression check (console/network
cleanliness after this session's fixes), not a re-run of that full suite.
