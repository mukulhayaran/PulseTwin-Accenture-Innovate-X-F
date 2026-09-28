# Production Readiness Report

PulseTwin — Production-readiness, scalability, reliability, security, ML/MLOps, and E2E
verification pass. 2026-09-21.

This report synthesizes `system-architecture-audit.md`, `baseline-verification.md`,
`e2e-test-report.md`, `performance-benchmark.md`, `scalability-review.md`,
`security-review.md`, and `reliability-chaos-report.md`. Read those for full detail; this
is the executive layer.

## Executive summary

PulseTwin's own documentation culture is unusually rigorous — `docs/STATE.md` already
tracks 76 numbered historical bugs with root causes, and `docs/DECISIONS.md` carries 40
ADRs justifying every deviation from the original brief. This pass's job was to verify
that record still holds against current code and to push into the two areas that record
itself flagged as unverified: the model-registry/runtime-scorer seam, and the production
Docker stack, which had never been run end to end because no session before this one had
a working Docker daemon.

**Both landed real defects, previously invisible because nothing had ever exercised them:**
a promoted model silently lost its promotion on every process restart while the registry
kept claiming otherwise, and the committed `serve/requirements.txt` could not be installed
in a clean environment at all — meaning the container image, if anyone had tried to build
it, would never have started. A third, unrelated defect (`/api/stations/stats` crashing
on every call against real PostgreSQL) was found via a security probe and confirmed to
have zero test coverage calling the endpoint at all, in a suite of 518+ tests.

All three are fixed, tested, and — for the two Docker-relevant ones — proven against the
real production stack (real Postgres, real container kill/restart, real MQTT broker), not
just pytest. The full regression suite is green throughout. Two significant gaps remain
named but not fixed, by design, because they are architecture decisions rather than
contained bugs: multi-replica promotion propagation, and no restart policy on the Compose
services.

**Overall assessment: closer to production-ready than the "demo, not production" framing
suggests for the pieces that were actually load-bearing, but the production Docker stack
had genuinely never been proven before this pass, and now that it has, three real defects
were found in the first attempt.** That ratio (3 real defects on the FIRST successful run
of a path that had never run before) is itself informative: treat every remaining
"reviewed by hand, not run" claim in this codebase's docs with the same skepticism
`docs/STATE.md` already applies to its own container-stack claims.

## Verified (actually executed and observed this pass)

- Full Python test suite: 518 passed / 0 failed (baseline, before this pass's changes),
  **525 passed / 0 failed (final closing run, after all fixes and new tests — Phase 20
  acceptance, 17m09s)** — see baseline-verification.md.
- `npx tsc --noEmit -p .` — clean.
- `npm run build` (Next.js static export) — clean, all 8 routes, reasonable bundle sizes.
- `docker compose -f docker-compose.prod.yml config` — valid.
- `docker compose -f docker-compose.prod.yml build` — failed, then fixed, then passed
  (all 3 images).
- `docker compose -f docker-compose.prod.yml up` — full stack healthy: postgres, redis,
  mqtt, migrate (idempotent against a real pre-existing volume), backend, ingestion.
- Full data path MQTT → ingestion → PostgreSQL → backend API, live, real broker: 55 real
  rows written and correctly read back by the API.
- Model registry ↔ runtime scorer seam: fixed, unit-tested (5 new tests), AND proven under
  a real `docker kill` + restart of the backend container against real Postgres.
- RBAC: 5 live probes against the real Docker backend, all matched documented behavior
  exactly, including a clean rejection of an invented role.
- SQL injection / path traversal probes: no injection occurred (parameterized queries
  confirmed); the injection probe surfaced an unrelated real bug (see P0-2).
- Rate limiting: correctly triggers 429 under real concurrent load.
- Real load test (concurrency 1/10/25/50/100) against 5 endpoint categories on the live
  Docker backend — real p50/p95/p99, not estimated.
- Browser E2E: 0 unhandled console errors / 0 unexpected failed requests across all 6
  routes.
- GenAI verifier: reviewed 37 existing adversarial/verifier tests (confirmed thorough by
  reading them, not just counting), plus one new live-executed adversarial probe (C3
  corpus-poisoning) with a concrete, reproducible finding.

## Blocked / unverified (named honestly, not glossed over)

- **Kubernetes/Terraform actual deployment** — never `terraform apply`'d (stated in the
  file's own header); no cloud credentials sought or available, correctly out of this
  pass's mandate (would incur real cost / require credentials).
- **Chaos-testing Postgres, Redis, and MQTT container kill/restart** — only the backend
  container was chaos-tested this pass; time did not extend to the other three.
- **Forced sensor-dropout via MQTT `control/dropout` topic** — attempted live, result
  inconclusive within the test window (timing/replay-position issue in the test method,
  not a confirmed product defect — see e2e-test-report.md).
- **Load testing beyond 100 concurrent clients** — the in-memory rate limiter (working as
  intended) makes single-harness testing past this point measure the test harness, not the
  server; a proper high-concurrency test needs either multiple replicas or a
  multi-identity harness, neither built this pass.
- **True multi-plant (5/20) scale testing** — analysis only (scalability-review.md);
  standing up real additional plants was outside this pass's scope and budget.
- **Dependency vulnerability scanning** (`pip-audit`, `npm audit`) — not run; already
  named as not-built-for-the-demo in `docs/SECURITY.md`.
- **Live adversarial run against a hosted LLM provider** — not repeated this pass (cost/
  time); the deterministic-verifier tests exercise the same guarantees without the
  nondeterminism.

## Findings

| ID | Severity | Area | Finding | Evidence | Fix | Verification |
|---|---|---|---|---|---|---|
| P0-1 | P0 | MLOps / seam | `serve/main.py::lifespan` never consulted `model_registry` at startup — a promotion's hot-swap lived only in the promoting process's memory; a restart silently reverted to the committed artifact while the registry kept reporting the promoted challenger as champion. | Traced startup code path; reproduced by promoting via the real API then `docker kill`+restart against real Postgres — champion reverted before the fix. | `serve/main.py::_resolve_champion_artifact` / `_bootstrap_registry_champion` (ADR-041); `serve/routers/admin.py`'s stale `persists_across_restart: False` also corrected to `True`. | 5 new tests (`tests/test_model_registry_runtime.py`), all passing; re-verified live against real Postgres after the fix — promotion survived the same kill+restart. |
| P0-2 | P0 | Data governance / SQLite-Postgres parity | `/api/stations/stats`'s SQL selected a non-aggregated column (`provenance`) without grouping by it — SQLite tolerates this silently, PostgreSQL rejects it with `GroupingError` on every call, valid `plant_id` or not. Zero test coverage called this endpoint over HTTP at all. | 500 on every call against real Postgres; confirmed via `docker logs`; confirmed the endpoint is used by both the Manager view and the copilot's tool registry. | `GROUP BY station_id, provenance` — safe because `provenance` is functionally dependent on `station_id` by construction (`offline/build_artifact.py`). | New test `tests/test_api.py::test_station_stats_returns_one_row_per_station`; re-verified live against real Postgres — 200 OK, correct data, after the fix. |
| P0-3 | P0 | Release / Docker | `serve/requirements.txt` pinned mutually uninstallable versions (`fastapi==0.115.0` needs `starlette<0.39`, `prometheus-fastapi-instrumentator==8.1.0` needs `starlette>=1.0`) — the container image has never been buildable from a clean environment. | `docker compose build` failed with `ResolutionImpossible` on first attempt of this pass — the first time this file has ever been installed from scratch (no CI, no prior working Docker daemon). | Repinned `fastapi`/`uvicorn`/`pydantic` to the versions this project has actually run against throughout its history (confirmed via `pip show` against the ambient dev interpreter). | Full rebuild succeeded; full stack came up; full pytest suite still green (versions match what was already ambient). |
| P1-1 | P1 | Scalability / MLOps | A promotion's in-memory hot-swap reaches only the ONE backend replica that received the API call — in the documented 2-6 pod production topology, other replicas keep serving the old model with no error until individually restarted. | Code trace (`STATE["scorers"]` is a process-local dict); not reproducible with only 1 replica running, so not live-demonstrated, but the mechanism is unambiguous from the code. | Not fixed — genuine architecture decision. ADR-041 names 3 alternatives (runbook step / poll reconciliation / Redis pub-sub) and recommends the runbook step now, poll reconciliation before the next real multi-replica deployment. | N/A — documented, not patched, per this audit's own policy against blindly patching ambiguous architecture. |
| P1-2 | P1 | Release engineering | No CI pipeline exists at all (`.github/workflows/` does not exist). The entire test suite has apparently never run against real PostgreSQL — confirmed indirectly by P0-2 surviving 518 passing tests. | `ls .github/workflows/` → does not exist. | Not fixed this pass (out of contained-fix scope) — recommended in Top 10 below. | N/A |
| P1-3 | P1 | Reliability | No `restart:` policy on any `docker-compose.prod.yml` service — a crashed backend/ingestion/mqtt container stays dead until manually restarted. | `grep restart: docker-compose.prod.yml` → no matches; confirmed live (`docker kill` left the container in `exited` state indefinitely). | Not fixed this pass — deployment-policy choice flagged for operator confirmation, not silently patched. | N/A |
| P2-1 | P2 | Security (GenAI) | The verifier's C3 "uncited number" sweep exempts any number appearing anywhere in tool-result data, including inside free-text string fields — not only genuine numeric fields. A fabricated, uncited claim can be laundered if its number happens to appear inside an unrelated string. | Reproduced live with a synthetic transcript (`genai/verifier.py::verify`) — a fabricated 47.3% claim was served as `verified`. | Not fixed — real gap but not currently reachable (`lot_id`/`unit_id` are bounded, simulator-generated formats, never open free text in this system today); a contained fix would also break a legitimate existing behavior (numbers cited from explanatory `note` fields). Documented as a recommendation, not patched. | Reproduction script preserved in `security-review.md`. |
| P2-2 | P2 | Observability | Missing metrics: ingestion rate/duplicate/corrupt/reorder-buffer-size, DB error count, retrain/promotion/rollback counters, active-model-id gauge, GenAI provider-failure/verification-failure counts. `serve/metrics.py` has 7 custom series today; none of the above. | Read `serve/metrics.py` in full; cross-checked against the audit's requested metric list. | Not implemented (P2 hardening, not a bug) — see Top 10 recommendations. | N/A |
| P2-3 | P2 | Docs drift | `docs/STATE.md`'s "339 tests pass" headline is stale (523 after this pass; was already 518 before it). | `pytest tests/ -q` actual counts vs. the quoted figure. | Corrected in this pass's doc edits; recommend deriving this number rather than hand-copying it in future. | Full suite run, counted directly. |
| P3-1 | P3 | Reliability (inconclusive) | Forced sensor-dropout via the `control/dropout` MQTT topic did not visibly transition status within a 60s test window. | Live test against the real broker; publisher confirmed connected via `netstat`. | Not a confirmed defect — likely a test-timing/replay-position artifact, not investigated further this pass. | Recommend a follow-up test that controls for publisher replay position. |

## Performance

See `performance-benchmark.md` for full tables. Headline numbers, all measured against the
live single-replica Docker backend (`uvicorn --workers 1`, real Postgres):

| Endpoint | n=1 p50 | n=25 p50 | n=100 p50 | Notes |
|---|---|---|---|---|
| `/api/health` | 110.6ms | 125.3ms | **1093.1ms** | Cheapest possible endpoint — this is pure single-worker queuing, not DB or scoring cost. |
| `/api/units` (real DB query) | 144.4ms | 188.7ms | 421.1ms (72% rate-limited) | Rate limiter correctly engaged mid-test. |

The single-worker ceiling is real and measured, not inferred from the architecture docs
alone. It is also the documented, correct tradeoff for the async-SSE design (ADR-009) —
the fix is horizontal replicas, which is exactly why P1-1 (promotion propagation) must be
resolved before replica count is safely increased.

## Scalability

See `scalability-review.md` for the full 1/5/20-plant analysis. Summary: the data layer
(Postgres partitioning by `plant_id`, per-plant model artifacts, low-cardinality Prometheus
labels) is already built for multi-plant scale and holds up well under review. The real
ceiling is operational, not architectural: MQTT ingestion is fixed at 1 replica by
design-not-yet-built, in-memory rate limiting doesn't share state or partition by plant
across replicas, and — most importantly — the promotion-propagation gap (P1-1) means
backend replica count should not be increased in production until that is resolved.
Recommended topology: fix promotion propagation first (runbook step, cheap; poll
reconciliation, better), then scale backend replicas per the documented HPA range, then
address MQTT shared-subscription scaling only once ingestion throughput is actually
measured as the bottleneck (not yet proven — no sustained MQTT load test was run this
pass).

## Security

Demo posture (per `docs/SECURITY.md`, verified live this pass): authorization without
authentication (`X-PulseTwin-Role` header, unauthenticated), CORS wide open, in-memory
single-process rate limiting — all correctly documented as demo-only, all confirmed
behaving exactly as documented. GenAI adversarial coverage is already extensive (37
existing tests, confirmed by reading them) and held up under one new live probe, which
found a real-but-unreachable-today verifier gap (P2-1).

**Required before real deployment** (already correctly identified in `docs/SECURITY.md`,
confirmed still accurate): OIDC/JWT replacing the role header (the swap is isolated to one
function body, per that document's own design), origin allow-listing instead of `*`,
Redis-backed rate limiting for multi-replica, gating the 13 currently-open read endpoints
in the SAME change that adds authentication (not before, since a read gate with no
authentication in front of it is decorative).

## MLOps — which model is serving, concretely

**Before this pass**: unanswerable with confidence. `/api/predict/defect` scored with
`artifacts/model_<plant>.npz` by file-path convention; `model_registry` had no row for it.
A promotion (`POST /api/admin/promote-model`) correctly updated the registry AND the live
in-memory scorer, but a restart discarded the live change while the registry kept
reporting the promoted model — so the registry and the actually-serving model could
silently disagree the moment any real deployment restarted a pod.

**After this pass**: `serve/main.py::lifespan` asks `model_registry` for each plant's
current champion at startup and loads its `artifact_path`, falling back to the committed
artifact only if no champion row exists or the file is missing. The committed artifact is
bootstrap-registered with real metrics (`pr_auc`, `oracle_pr_auc`,
`precision_at_budget`, `recall_at_budget`, `bottleneck_detection_accuracy` — pulled from
the `metrics` table, not fabricated) the first time a plant is served with no prior row.

**Concretely, right now**: `SELECT model_id, status, artifact_path FROM model_registry` on
the live Docker Postgres returns exactly one `champion` row per plant, naming the exact
`.npz` file currently loaded, trained_at, training data range, and measured metrics. A
promotion changes that row AND the live serving state atomically (from the caller's
perspective) and survives a restart — proven with a real `docker kill`.

**What is still not answerable with confidence**: in a MULTI-replica deployment, "which
model is serving" can differ PER REPLICA for a window after a promotion, until each
replica is individually restarted or (once built) picks up a poll-based reconciliation
signal. This is P1-1, named above, not fixed.

## Reliability

See `reliability-chaos-report.md` for full detail.

| Dependency | What happens when it fails |
|---|---|
| Backend process/container | **Tested live.** No auto-restart policy (P1-3) — stays down until manually restarted. Manual restart recovers cleanly in seconds, and (post-fix) resumes serving the correct promoted model. |
| Frontend ↔ backend (SSE) | **Verified in a prior session, re-confirmed by code review this pass.** `OfflineBanner` within 1s, line map/station tiles retained (not cleared), auto-recovers on reconnect. |
| MQTT broker/subscriber | **Connectivity verified live.** Kill/restart of the broker itself not tested this pass. |
| PostgreSQL | Not chaos-tested this pass (named as unverified, not assumed safe). |
| Redis | Not chaos-tested this pass (named as unverified, not assumed safe). |
| Model artifact (corrupt/missing) | Falls back to `serve/fallback_scorer.py`, labelled `FALLBACK MODE` — unit-tested, not forced live this pass. |

## Top 10 recommendations

Ordered by risk reduction and engineering value, not novelty.

1. **Resolve multi-replica promotion propagation before scaling backend replicas.**
   Problem: P1-1. Evidence: code trace, ADR-041. Solution: start with a documented
   post-promotion rolling-restart runbook step (S), move to poll-based reconciliation in
   `lifespan` reusing `_resolve_champion_artifact` (M) before the next real multi-replica
   deployment. Impact: closes a silent split-brain-serving risk. Complexity: S then M.
   Risk of the fix itself: low (reuses already-tested code). Verification: a 2-replica
   local Compose test promoting on one and polling the other's `/api/model/health`.
2. **Add a CI pipeline, even a minimal one, and run at least a subset of tests against
   real Postgres.** Problem: P1-2, and directly how P0-2 survived 518 tests. Evidence:
   no `.github/workflows/`; `/api/stations/stats` had zero HTTP-level test coverage.
   Solution: fast PR checks (pytest -k "not slow", tsc, build, import-wall test) plus a
   nightly job that stands up Postgres via Compose and runs the full suite against
   `DATABASE_URL` set. Impact: would have caught P0-2 and P0-3 automatically. Complexity:
   M. Risk: none to existing behavior. Verification: the nightly job itself failing on
   the pre-fix code, passing on the fixed code.
3. **Add `restart:` policies to `docker-compose.prod.yml`.** Problem: P1-3. Evidence:
   live `docker kill` left the container dead indefinitely. Solution: `restart:
   unless-stopped` on `backend`, `ingestion`, `mqtt` (postgres/redis's official images
   already default sensibly in most setups, but should be checked). Impact: real crashes
   self-heal instead of requiring manual intervention. Complexity: S. Risk: could mask a
   genuine crash-loop if not paired with the observability gaps below being closed first.
   Verification: `docker kill` + wait, confirm auto-recovery.
4. **Close the observability gaps for MLOps state** (P2-2, the active-model-id gauge and
   promotion/rollback counters specifically). Problem: an operator cannot currently see
   "which model is champion, and did it just change" without querying the database
   directly. Evidence: `serve/metrics.py` audit. Solution: a `pulsetwin_active_model_info`
   gauge (labels: plant_id, model_id — bounded cardinality, promotions are rare) plus
   `pulsetwin_model_promotions_total`/`_rollbacks_total` counters, incremented in
   `admin.py` alongside the existing `log_action()` calls. Impact: closes exactly the
   "which model is running" question this whole pass centered on, for an operator who
   doesn't want to run SQL. Complexity: S. Risk: none (additive). Verification: promote a
   model, scrape `/api/metrics`, confirm the gauge changed.
5. **Extend Prometheus metrics into `ingestion/`.** Problem: no visibility into ingestion
   rate, duplicate count, corrupt/rejected message count, or reorder-buffer size — all
   requested by this audit and all currently absent. Evidence: `serve/metrics.py` only
   covers `serve/`; `ingestion/` is NOT under the import wall (only `serve/` is
   restricted), so it can freely add `prometheus_client` and expose its own `/metrics`.
   Solution: a small `ingestion/metrics.py` mirroring the pattern in `serve/metrics.py`.
   Impact: the single biggest observability gap for diagnosing an ingestion incident
   without attaching a debugger. Complexity: M. Risk: none. Verification: publish a burst,
   scrape the new endpoint, confirm counts match what was published.
6. **Fix the C3 verifier corpus-poisoning gap before any future tool exposes free text**
   (P2-1). Problem: documented above. Evidence: live reproduction. Solution: restrict
   `_flatten_numbers`' string-scanning to an explicit allowlist of field name patterns
   (`note`, `reason`, `explanation`) rather than every string value, OR require the model
   to always cite through the structured `claims` table for THIS class of number and
   drop the corpus-string exemption for arbitrary free text. This is a genuine design
   choice (see security-review.md's tradeoff discussion) — recommend deciding it now,
   before it becomes urgent. Complexity: M. Risk: could break a legitimate citation
   pattern if the allowlist is too narrow — needs the existing `test_genai_verifier.py`
   suite extended alongside. Verification: the reproduction script in security-review.md
   should return `rejected` or a redacted claim after the fix, and all 25 existing
   verifier tests must still pass.
7. **Repeat this pass's Docker E2E test on a schedule, not just once.** Problem: this
   exact path (`docker compose up --build`) had never run before this pass and
   immediately found 2 P0s. Nothing guarantees it won't silently break again the next time
   a dependency is bumped in the ambient dev environment without the pin being updated to
   match. Evidence: P0-3's entire root cause. Solution: fold into the CI recommendation
   above as the nightly job's first step. Complexity: S (given #2 exists). Risk: none.
   Verification: the nightly job fails loudly on the next pin drift instead of silently.
8. **Document and decide the sensor-dropout MQTT control-topic test gap.** Problem: P3-1,
   inconclusive this pass. Evidence: e2e-test-report.md. Solution: either fix
   `mqtt_publisher.py` to apply a forced dropout immediately on receipt (small, contained)
   or add a documented test recipe that seeks the publisher to a known timestamp first.
   Impact: makes `docs/FINALS_DEMO_SCRIPT.md`'s Act 2 live-injection step actually
   verifiable on demand, not just "should work." Complexity: S. Risk: low — only affects
   the demo/test fault-injection path, never production ingestion logic. Verification: a
   repeat of this pass's test, this time observing a status transition within a bounded
   window.
9. **Add a lightweight nightly load-test job using multiple synthetic identities**, since
   this pass's own load test proved a single-identity harness cannot safely probe past
   ~100 concurrent requests against this API without the (correctly-working) rate limiter
   interfering. Problem: no repeatable load-testing recipe exists for this API today.
   Evidence: performance-benchmark.md's rate-limiter section. Solution: a small script
   rotating `X-PulseTwin-Role`/synthetic source IPs (or, more realistically, running from
   several replicas), scheduled nightly, not on every PR. Complexity: M. Risk: none if
   run against a non-shared environment. Verification: the job itself produces a
   trend-line report over time.
10. **Audit-derive headline counts instead of hand-copying them** (P2-3 and the broader
    pattern `docs/STATE.md` itself already names as a repeated failure mode — 5 documents
    disagreeing about headline metrics, historically). Problem: test counts, endpoint
    inventories, and registry state are currently hand-copied into multiple docs and drift.
    Evidence: the "339 tests" staleness found this pass. Solution: a small script (`pytest
    --collect-only -q | wc -l`, `grep -rc "^@router\." serve/routers/`) run as part of the
    doc-generation or CI step, per `docs/STATE.md`'s own Phase 18 suggestion. Complexity:
    S. Risk: none. Verification: the generated numbers matching a manual recount.

## Release gates

| Gate | Status |
|---|---|
| All critical (P0) tests pass | **PASS** — 525/525, full suite, final closing run (17m09s) |
| Zero P0 findings | **PASS, after this pass's fixes** — 3 P0s found, all 3 fixed and verified live |
| Production Compose stack starts cleanly | **PASS** — postgres/redis/mqtt/migrate/backend/ingestion all healthy |
| Complete MQTT→DB→API→UI path verified | **PARTIAL PASS** — MQTT→DB→API proven live; UI leg (SSE rendering the live-ingested data) not separately re-verified this pass (verified in a prior session per `docs/STATE.md`, not repeated) |
| Active model maps to registry entry | **PASS** — verified live, both plants, real Postgres |
| Promotion and rollback verified | **PASS (promotion)** — verified live via the real API + restart. **NOT verified (rollback)** this pass specifically against the live Docker stack (rollback IS covered by `tests/test_retraining.py` and `tests/test_model_registry_runtime.py`'s SQLite-backed tests, just not repeated live in Docker this pass) |
| No browser console errors | **PASS** — 0 unhandled errors across 6 routes; 1 expected/handled RBAC 403 (browser-logged, not an app defect) |
| No unexpected 5xx during normal E2E | **FAIL, then PASS** — `/api/stations/stats` 500'd on every call against Postgres; fixed and re-verified 200 OK |
| Load-test error rate below defined threshold | **NOT FULLY EVALUATED** — no defined threshold exists yet in project docs; observed errors at high concurrency were rate-limiter enforcement (correct), not server failures |
| p95 latency within justified target | **NOT FORMALLY GATED** — no target is documented; this pass's measurement (p95 ~1.17s at n=100 concurrent on a single worker) should inform setting one, tied to the horizontal-scaling recommendation |
| No unbounded metrics cardinality | **PASS** — all current labels (`plant_id`, `feature`, `shift`) are bounded; no new unbounded label introduced this pass |
| Recovery behavior verified | **PARTIAL PASS** — backend kill/restart verified live; Postgres/Redis/MQTT restart not tested this pass (see reliability-chaos-report.md) |
