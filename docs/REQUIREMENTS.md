# Requirements Traceability

Status values are used strictly:

| Status | Means |
|---|---|
| **IMPLEMENTED** | Code exists, runs, and is covered by a test or a generated artifact. |
| **PARTIAL** | Works for the stated cases; the gap is named in the limitation column. |
| **DESIGN ONLY** | Documented and architecturally provided for. **No working code.** |
| **NOT IMPLEMENTED** | Neither. Listed so its absence is explicit rather than implied. |

**Nothing planned is marked implemented.** Where a benchmark has not been executed, the
evidence column says `NOT RUN` rather than quoting a number.

---

## Core simulation and modelling

| Requirement | Status | Implementation | Validation | Evidence | Known limitation |
|---|---|---|---|---|---|
| Mixed-model production | IMPLEMENTED | `offline/simulator/line_model.py` `_draw_variant`; per-variant cycle multipliers in `configs/*.yaml` | `tests/test_simulator.py`, `tests/test_configs.py` | 3 variants/plant; EV-heavy-day mix shift | Variant mix is a weekday schedule, not a demand model |
| Multiple plants | CONFIG-READY | `configs/plant_a.yaml` (30 stations); a new plant is a new YAML | `tests/test_configs.py` | One plant ships (plant B removed, ADR-068) | No second plant measured; plant ACL only for isolation |
| Discrete-event line simulation | IMPLEMENTED | `offline/simulator/line_model.py` — heapq scheduler, buffers, blocking, starvation, breakdowns | `tests/test_simulator.py`, `tests/test_bottleneck_benchmark.py` | 40,903 events/day/plant A | Serial topology only (see below) |
| Causal synthetic defect generation | IMPLEMENTED | `offline/simulator/causal_engine.py` — 3 competing-risk modes + unobservable lot/noise hazards | `tests/test_no_leakage.py` | Realised rate 2.97–3.3% vs configured 3% | Synthetic throughout; no real defect data |
| Soft sensors | IMPLEMENTED | `offline/ml/soft_sensor.py`; masked-station validation | artifact `soft_sensor_validation` | Ambient R² 0.965–0.996; tool-wear R² −0.55 to −0.07 | **Tool-wear signals are not reconstructable.** Published as a negative result |
| Tree-based defect-risk model | IMPLEMENTED | `offline/ml/train.py` → `artifacts/model_<plant>.npz`; `serve/scorer.py` walks it in pure numpy | `tests/test_scorer_parity.py` (max \|diff\| < 1e-6 vs sklearn) | PR-AUC 0.256 (plant A) | 53% of a measured oracle ceiling of 0.481 |
| Probability calibration | IMPLEMENTED | Platt, fitted offline, applied in `Scorer.predict` | `tests/test_scorer_parity.py` | Brier 0.0206 | Single global calibrator; not per-segment |
| Temporal train/val/test splits | IMPLEMENTED | `offline/ml/featurize.py` `_split_for_day` — whole-week boundaries | `tests/test_no_leakage.py` | — | — |
| Oracle ceiling analysis | IMPLEMENTED | Simulator's latent propensity scored as a predictor | artifact `oracle_pr_auc` | 0.481; capture 53% | Ceiling is simulator-specific |
| Containment workflow + human decisions | IMPLEMENTED | `serve/routers/intelligence.py`; `containment_decisions` table | `tests/test_intelligence_api.py` | 4 validation outcomes recorded | Feedback aggregation is PARTIAL — see below |

## Bottleneck detection

| Requirement | Status | Implementation | Validation | Evidence | Known limitation |
|---|---|---|---|---|---|
| Bottleneck detection | IMPLEMENTED | `offline/ml/bottleneck.py` — Roser active-share argmax | **Independent throughput-sensitivity ground truth** | `artifacts/benchmarks/bottleneck_detector_results.csv`; top-1 **0.93** on plant A baseline | Previous 85.9%/90.2% figure was self-consistency, not accuracy |
| Independent ground truth | IMPLEMENTED | `offline/benchmarks/bottleneck_ground_truth.py` — CRN perturbation, 30 seeds, bootstrap CI | `tests/test_bottleneck_benchmark.py` (25 tests) | `bottleneck_ground_truth.csv` | +10% probe only; local sensitivity |
| Multi-detector benchmark | PARTIAL | `offline/benchmarks/bottleneck_detectors.py` — 7 detectors | Same | plant A baseline measured | **6 of 7 scenarios NOT RUN** |
| Bottleneck forecast | IMPLEMENTED | `offline/ml/bottleneck.py` `forecast_skill` — ridge on lags vs persistence | artifact `forecast_skill_vs_persistence` | +0.136 / +0.205 | 15-min horizon; not validated against sensitivity ground truth |

## Live simulation

| Requirement | Status | Implementation | Validation | Evidence | Known limitation |
|---|---|---|---|---|---|
| Recorded replay | IMPLEMENTED | `serve/main.py` stream endpoint over committed JSONL | `tests/test_api.py` | — | `stream_index.byte_offset` drifted on Windows-built artifacts |
| Scenario injection | IMPLEMENTED | `serve/live_simulation.py` | `tests/test_live_simulation.py` | 8 scenarios | **Writes risk directly** (`_raise_risk`). Retained as a scripted demo, renamed honestly |
| True live simulation | IMPLEMENTED | `offline/live_twin/` — stepped DES → incremental physics → shared feature contract → real scorer | `tests/test_live_simulation_engine.py` (16 tests) | Defect rate 2.974%; 52.1 UPH; alerts emerge on EV-heavy days unprompted | Single process per run; ~25s calibration at start |
| No direct risk manipulation in live mode | IMPLEMENTED | Risk only from `Scorer.predict` | `test_no_module_writes_a_risk_value` — AST guard, verified to fail on an injected `risk += …` | — | — |
| Incident changes physics first | IMPLEMENTED | `offline/live_twin/incidents.py` — 7 physical handles | `test_torque_degradation_moves_the_physics_then_the_model`, `test_high_humidity_thins_the_film`, `test_station_breakdown_propagates_blocking_and_starvation` | Torque 43.6 → 34.1 under tool decay | — |
| Shared state across clients | PARTIAL | Engine is a single owning process; snapshot is serialisable | — | — | **`SimulationManager` and the HTTP routes for the live mode are NOT IMPLEMENTED.** The engine is driven programmatically and by `scripts/live_validate.py`, not yet by the API |
| Deterministic seeds | IMPLEMENTED | `LiveTwinEngine(seed=…)`; six spawned streams | `test_same_seed_produces_identical_events`, `test_different_seeds_produce_a_different_realisation` | — | — |
| Speed independent of results | IMPLEMENTED | Absolute target clock in `advance()` | `test_speed_does_not_change_results` | Bug found and fixed: `advance` was non-additive (989 vs 961 events) | — |

## ML robustness and monitoring

| Requirement | Status | Implementation | Validation | Evidence | Known limitation |
|---|---|---|---|---|---|
| Alert-score diversity measurement | IMPLEMENTED | `offline/benchmarks/score_diversity.py` — unique ratio, entropy, IQR, precision@k, recall@k, within-cohort rank-AUC | Collapse warnings fire | `artifacts/benchmarks/score_diversity.json` | Measurement only |
| Within-alert discrimination | **NOT IMPLEMENTED** (measured, not fixed) | — | — | **Plant A: 113 alerts, 1 unique score, entropy 0.000, rank-AUC 0.500.** | The model identifies the at-risk set and cannot order it. Feature work to fix this was **not** undertaken |
| Explainability | PARTIAL | `serve/scorer.py` `explain()` — exact decision-path (Saabas) attribution, additive to `raw − expected_value` | `tests/test_scorer_parity.py` | API reports `explanation_mode: "decision_path"` | **Deliberately not called SHAP.** Explanation *diversity* metrics (§6 of the brief) NOT IMPLEMENTED |
| Progressive sensor-loss benchmark | **NOT IMPLEMENTED** | — | — | `NOT RUN` | Registered in `tools/verify_artifacts.py` as an expected artifact so its absence is visible |
| Alert-budget rolling recalibration | **NOT IMPLEMENTED** | Static `budget_threshold` from training | — | Observed 8.5 alerts/shift vs a budget of 6 — the overshoot the brief predicts | — |
| Closed-loop feedback metrics | PARTIAL | `containment_decisions` records `correct_intervention` / `unnecessary_intervention` / `missed_defect` / `correct_monitor` | `tests/test_intelligence_api.py` | — | Operator-usefulness vs confirmed-outcome are **not** separated; no 7/30-day or per-station/shift/variant aggregation |
| Segmented drift monitoring | PARTIAL | `mlops/drift_detector.py` — PSI, calibration, coverage, global | `tests/test_drift_detection.py` | — | **Single global score.** No per-plant/variant/shift/zone/humidity-regime segmentation, no minimum-sample gating |
| Model fallback | IMPLEMENTED | `mlops/fallback.py` + `serve/fallback_scorer.py` — SPC Nelson rules | `tests/test_graceful_degradation.py` | Shadow precision/recall 3.0% / 100%, published as measured (ADR-035) | Poor, and said to be |
| Champion/challenger retraining | IMPLEMENTED | `mlops/retrainer.py`, `mlops/model_registry.py` | `tests/test_retraining.py`, `tests/test_model_registry_runtime.py` | — | — |
| External public-dataset benchmark | IMPLEMENTED | `benchmark/` — AI4I 2020 adapter, separate dependency group | `tests/test_benchmark_adapters.py` | PR-AUC 0.773 [0.657–0.873] | **Validates the pipeline, not the automotive task.** See `docs/CLAIMS.md` |

## Platform and production readiness

| Requirement | Status | Implementation | Validation | Evidence | Known limitation |
|---|---|---|---|---|---|
| Authentication (OIDC/JWT) | IMPLEMENTED | `serve/middleware/jwt_auth.py` — signature, issuer, audience, expiry, JWKS with TTL cache, configurable role claim + mapping | `tests/test_jwt_auth.py` | Fail-closed at boot if `jwt` mode cannot verify | No live IdP has been exercised; HS256 and JWKS paths tested against synthetic tokens |
| Role header disabled in production | IMPLEMENTED | `PULSETWIN_AUTH_MODE=jwt` → bare `X-PulseTwin-Role` is 401 | `tests/test_jwt_auth.py` | — | Dev mode remains the default |
| CORS restriction | IMPLEMENTED | `PULSETWIN_CORS_ORIGINS`; wildcard refused in `jwt` mode | `tests/test_jwt_auth.py` | — | — |
| RBAC | IMPLEMENTED | `serve/middleware/auth.py` — 4-role hierarchy; `frontend/lib/routeAccess.ts` exact-match route table | `tests/test_auth.py`, `tests/test_auth_session.py` | — | — |
| Audit logging | IMPLEMENTED | `serve/middleware/audit.py` — every mutating action | `tests/test_audit.py` | — | — |
| PostgreSQL persistence | IMPLEMENTED | `migrations/001–004`, `serve/db_postgres.py`, `offline/load_to_postgres.py` | `tests/test_api.py` (SQLite path) | — | **`docker compose -f docker-compose.prod.yml up` has never been run end to end** |
| MQTT ingestion | IMPLEMENTED | `ingestion/` — separate deployable, dedup, reordering, quality scoring | `tests/test_ingestion.py`, `tests/test_mqtt_live_projection.py` | — | — |
| Redis | IMPLEMENTED | `ingestion/feature_buffer.py` | `tests/test_live_feature_parity.py` (fake) | — | Not exercised against a live Redis in CI |
| Verified read-only AI Copilot | IMPLEMENTED | `genai/` — closed tool registry as data-egress allowlist; `genai/verifier.py` deterministic gate | `tests/test_genai_*.py` (8 files) | 4/5 claims verified, 5th correctly redacted, on a live provider run | Provider-dependent; offline path is the keyless default |
| Next.js frontend | IMPLEMENTED | `frontend/` — 7 routes, Tailwind v4 + Radix + Motion | `npm run typecheck` clean; static export | — | **Live-simulation mode selector NOT IMPLEMENTED** (brief §25) |
| Reproducible dependencies | IMPLEMENTED | `pyproject.toml` + `uv.lock` (57 packages), 5 groups | `tests/test_dependency_manifest.py` | Docker `requirements*.txt` retained as the import-wall enforcer | — |
| CI | IMPLEMENTED | `.github/workflows/ci.yml` (4 jobs, Windows matrix) + `nightly.yml` (4 expensive jobs) | — | **Never executed** — no remote exists and this repo must not be pushed | Workflow YAML is parse-validated only |
| Artifact provenance | IMPLEMENTED | `tools/verify_artifacts.py` — content hashes, 15 artifacts, 14 source groups | Verified to detect an injected source change and to ignore a CRLF flip | `artifacts/MANIFEST.json` | Proves consistency, not correctness |
| README metric consistency | IMPLEMENTED | `tools/metrics_summary.py --check` | Verified to fail on a 0.256→0.241 edit | `artifacts/metrics_summary.json`; 14 numbers gated | Only marked numbers are checked |
| Topology abstraction | **NOT IMPLEMENTED** | — | — | — | `station i+1` assumptions remain throughout `line_model.py`. Parallel/merge/split/rework unsupported |
| Real PLC integration | **NOT IMPLEMENTED** | — | — | — | No OPC-UA / Modbus / S7 client exists |
| MES integration | **NOT IMPLEMENTED** | — | — | — | Order release, genealogy export and work-order closure are all absent |
| Real factory validation | **NOT IMPLEMENTED** | — | — | — | **PulseTwin has never seen production data.** Every metric is synthetic |

---

## Summary

| Status | Count |
|---|---|
| IMPLEMENTED | 38 |
| PARTIAL | 6 |
| DESIGN ONLY | 0 |
| NOT IMPLEMENTED | 8 |

The eight `NOT IMPLEMENTED` rows are the honest edge of this project: within-alert
discrimination, the sensor-degradation benchmark, rolling alert-budget recalibration,
topology abstraction, the live-simulation HTTP surface, the frontend mode selector, PLC and
MES integration, and real-factory validation.
