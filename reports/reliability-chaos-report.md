# Reliability / Chaos / Recovery Report

Production-readiness pass, 2026-09-21. Tests below were executed against the real Docker
production stack (`docker-compose.prod.yml`) where noted, and against the local dev
frontend/backend where noted. Time did not allow chaos-testing every dependency
individually (Postgres, Redis, and MQTT restarts were not attempted this pass, beyond MQTT
already being exercised as part of the live data-path test in e2e-test-report.md) — this is
listed honestly under Blocked/Unverified rather than assumed.

## Backend container: kill and restart (Docker, real Postgres)

This was the vehicle for verifying ADR-041 (see production-readiness-report.md P0-1), and
doubles as a genuine chaos test:

1. Promoted a test challenger model via the real `POST /api/admin/promote-model` API.
2. `docker kill pulsetwinaccenture-backend-1` — hard kill, not a graceful stop.
3. **Observed: no restart policy is configured** on any service in
   `docker-compose.prod.yml` (confirmed by `grep restart: docker-compose.prod.yml` —
   no matches). The container stayed in `exited` state indefinitely; Docker's default
   policy (`no`) applied. **This is a real gap for a production deployment** — a crashed
   backend or ingestion container in this Compose stack does not self-heal. The
   Kubernetes manifests are a separate matter (K8s has its own restart semantics via
   liveness probes) and were not evaluated live (no cluster stood up).
4. `docker start pulsetwinaccenture-backend-1` (manual, as an operator would).
5. **Recovery: clean.** Logs showed no errors, `Application startup complete` within
   seconds, `/api/health` answering 200 immediately after.
6. **Data consistency after restart, verified**: the promoted model was still champion in
   `model_registry`, AND `/api/predict/defect` correctly served from it — proving ADR-041's
   fix holds under a real, hard container kill, not just a clean pytest-driven shutdown.

**Recommendation**: add `restart: unless-stopped` (or `on-failure`) to at least `backend`,
`ingestion`, and `mqtt` in `docker-compose.prod.yml`. Small, safe, immediately actionable —
not implemented this pass because it is a deployment-policy choice the operator should
confirm (e.g., whether silent auto-restart masking a crash-loop is preferred over visible
failure), consistent with this audit's policy of not patching ambiguous choices without
flagging them first.

## Frontend graceful degradation (previously verified, spot-checked this pass)

`docs/STATE.md` already records a full backend-kill-with-browser-open test (2026-09-16):
`TWIN OFFLINE` banner within 1s, line map and station tiles retained (not cleared), banner
clears within 1s of backend restart, `SSE LIVE` restored. This pass did not repeat that
exact test (time-bounded), but confirmed via code review that the mechanism it depends on
(`OfflineBanner`, `retryNonce`-driven reconnect, `lastKnownAt` staleness tracking) is still
present and wired into all four persona pages' JSX, matching the prior verified state.

## MQTT path (see e2e-test-report.md for full detail)

- **Broker/subscriber connectivity**: verified live — a publisher connected, the
  subscriber (running in its own container) received and processed every event, wrote to
  real Postgres.
- **Forced sensor dropout via `control/dropout` topic**: attempted live; inconclusive
  within the test window (see e2e-test-report.md for the exact reasoning — this is a
  timing/replay-position question in the test methodology, not a confirmed defect).
- **Broker restart mid-stream, duplicate/out-of-order/corrupted payload injection,
  subscriber restart, sustained burst traffic**: **not executed this pass.** The fault
  types themselves are already built and unit-tested (`ingestion/mqtt_publisher.py`'s
  `FaultInjector`, `tests/test_ingestion.py`), but exercising them against the LIVE broker
  under this pass's time budget was not completed beyond the one live burst test recorded
  above.

## What was NOT chaos-tested this pass (honest ledger)

- PostgreSQL container kill/restart while the backend holds open connections.
- Redis container kill/restart (ingestion's feature buffer, the documented degraded-read
  cache).
- MQTT broker (mosquitto) kill/restart mid-stream.
- Ingestion container kill/restart while mid-processing a burst (would test for lost vs.
  duplicated events across the restart boundary — the dedup logic is unit-tested but not
  chaos-tested against a real restart this pass).
- Simulating a corrupt/missing model artifact against the LIVE Docker deployment (this
  exists as a designed fallback path — `serve/fallback_scorer.py`, labelled `FALLBACK
  MODE` — and is unit-tested, but was not forced live this pass).
- Approximate recovery time was only measured for the one scenario actually run (backend
  kill/restart: sub-5-second observed recovery, health endpoint responsive immediately
  after container start logs `Application startup complete`).

**Recommendation**: the single highest-value next chaos test, given what this pass found,
is killing `postgres` while `backend` holds an active connection pool — this is the
scenario most likely to interact badly with `serve/db.py`'s connection handling and was
explicitly named as a "test if practical" item in the audit brief but not reached this
pass due to time.
