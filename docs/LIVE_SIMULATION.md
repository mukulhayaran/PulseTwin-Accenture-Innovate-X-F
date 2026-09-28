# Live Simulation

> **Everything on this page is SYNTHETIC. No real factory data is involved at any point.**
> PulseTwin has never been connected to a production line. See `docs/CLAIMS.md` for the
> wording these results do and do not support.

PulseTwin has three event sources. They are easy to confuse and they make very different
claims, so they now have separate names, separate code paths, and separate honesty
labels.

| Mode | What it is | Where risk comes from | Use it for |
|---|---|---|---|
| **Recorded Replay** | The committed `artifacts/stream_<plant>.jsonl` played back at a chosen speed | Replayed — it was computed offline by the real pipeline and baked into the artifact | A fast, exactly reproducible demo of the shipped numbers |
| **Scenario Injection** | The same replay, with an incident script that edits the replayed events | **Written by the script.** `serve/live_simulation.py::_raise_risk` does `calibrated_risk += 0.5 * severity` | A scripted narrative where the operator must see a specific thing happen at a specific moment |
| **Live Simulation** | A genuine discrete-event simulation of the line, advancing in server-owned time | **Computed by the trained model** from features derived from simulated physics | Anything that claims to show the system *working* |

**Scenario Injection is kept, not deleted.** A scripted demo is a legitimate thing to want
and it was the only mode that existed before. What changed is that it is no longer called
"live", because it isn't.

---

## 1. Why the old "Live Synthetic" mode was not a simulation

`serve/live_simulation.py` reads the committed JSONL stream line by line, mutates a few
fields, and on an incident calls:

```python
def _raise_risk(self, event, severity):
    event["calibrated_risk"] = min(0.99, event.get("calibrated_risk", 0) + 0.5 * severity)
    if event["calibrated_risk"] >= threshold:
        event["alert"] = 1
```

The risk rises because the code raised it. The model is not consulted. That guarantees a
demo always "works", which is exactly why it proves nothing: the same output would appear
if the model were deleted.

It also has a subtler problem. `torque_degradation` both edits `torque_nm` **and** calls
`_raise_risk`. A viewer sees a torque drop followed by a risk rise and reasonably infers
causation. There is none — the two lines are independent edits to the same dict.

---

## 2. What Live Simulation actually does

```
                      offline/live_twin/
  ┌──────────────────────────────────────────────────────────────────────┐
  │  LineSimulator.step()           one DES event at a time              │
  │      station state · buffers · blocking · starvation · breakdowns    │
  │                          │                                            │
  │                          ▼                                            │
  │  LiveSignalGenerator     incremental AR(1)/IIR sensor physics         │
  │      torque · vibration · temperature · weld current · humidity ·     │
  │      film thickness           (formulas imported from physics.py)     │
  │                          │                                            │
  │                          ▼                                            │
  │  feature_contract.signal_features    THE SAME function ingestion/     │
  │      + flow features                 and the offline pipeline use     │
  │                          │                                            │
  │                          ▼                                            │
  │  serve.scorer.Scorer.predict()       the real committed ensemble      │
  │      raw score → Platt calibration → operating threshold              │
  │                          │                                            │
  │                          ▼                                            │
  │  alert → containment → inspection outcome → validation                │
  └──────────────────────────────────────────────────────────────────────┘
```

`LineSimulator` is the **same** simulator the offline pipeline uses. It was refactored
from `run()` into `start()` / `step()` / `finish()`, where `run()` is now
`start(); while step(): pass; return finish()`. No second simulator exists.
`tests/test_bottleneck_benchmark.py` and a fingerprint over `state_seconds`, `visits` and
`completed_units` confirm the batch path is byte-identical after the refactor.

### Feature parity

A live unit arrives at the scorer with **253 of 253** features populated — the complete
set the offline pipeline builds. Per-signal features (`_dev_median`, `_z`, `_roc`,
`_roll_std`) come from `feature_contract.signal_features`, the same standard-library
function `ingestion/feature_buffer.py` calls, so the live twin cannot drift from the MQTT
path or from training.

### Warm-up, and why there are two phases

Three things need history before an honest alert: trailing medians (50 units), z-scores
(200 units), and the causal hazard calibration.

The hazard calibration **cannot** come from the live warm-up, and the first implementation
that tried produced a degenerate result every time — `fitted_k` pinned at its bisection
ceiling of 40.0 with zero mode exposure. Two structural reasons:

1. **Mode A is gated on humidity**, and the humidity step only occurs on the configured
   high-humidity weekdays (Tue/Wed). A warm-up long enough to define a z-score is about
   five simulated hours, all of it Monday, so the dominant defect mode is *structurally
   absent* from the calibration cohort.
2. **Mode C is gated on a vibration percentile**, and during warm-up the reference
   distribution being built is the very thing the percentile needs. It is circular: Mode C
   is identically zero until the calibration it feeds exists.

So calibration runs the **genuine batch pipeline** (`generate_signals` →
`build_unit_signals` → `compute_defects`) over a short multi-day run that spans the weekday
cycle, and freezes `k`, the per-mode references and the vibration reference distribution.
The live phase then applies those constants per unit. `tests/test_live_causal_parity.py`
pins the two forms of the hazard formula against each other.

---

## 3. Incident propagation

Every incident changes a **physical condition**. None of them touches a score.

| Scenario | Physical handle | Path to the model |
|---|---|---|
| `torque_degradation` | Tool health at the station decays over ~30 simulated minutes | Achieved torque falls and vibration rises at that station (shared cause) → `s<N>_torque_nm`, `_dev_median`, `_z`, `_roc` |
| `high_humidity` | Ambient relative humidity rises | `humidity_pct` rises **and** `film_thickness_um` thins through the spray-transfer term; both are Mode A co-triggers |
| `station_breakdown` | The station is forced down **through the simulator's own `_on_failure`** | Downstream starves, upstream blocks, dwell times rise, the constraint moves |
| `buffer_congestion` | Output buffer capacity physically reduced | Blocking propagates upstream; `paint_buffer_dwell_s` rises |
| `sensor_dropout` | Named sensors stop reporting | The value is **absent**, not zero and not imputed; the unit routes down the model's native missing-value branch and provenance becomes `inferred` |
| `rushed_checklist` | The manual station's checklist *standard* is shortened | The operator model produces genuinely faster checklists → Mode B's driver |
| `bad_material_lot` | The unobservable lot-quality latent is degraded for one lot | Torque down and vibration up, with **no directly observable cause**. The honest hard case. |

### This is enforced, not just documented

`tests/test_live_simulation_engine.py::test_no_module_writes_a_risk_value` parses the AST
of every file in `offline/live_twin/` and fails on any assignment to `risk`,
`calibrated_risk`, `risk_raw`, `score` or `probability` whose right-hand side does not
call `scorer.predict` — and fails on **any** augmented assignment to those names, whatever
the right-hand side. Injecting `record.risk += 0.5 * 0.7` into the engine makes it fail
with `engine.py:501 augmented assignment to risk`.

A companion test asserts `IncidentState` exposes no method whose name contains `risk`,
`score`, `probability`, `alert` or `threshold`. Incidents offer physical quantities only.

---

## 4. Measured behaviour

Plant A, seed 42, 12-day horizon, threshold 0.4061 (read from the committed artifact).
Reproduce with the command in §6.

### The Tuesday Problem reproduces on its own

Alerts are not scheduled. They appear when the physics reaches the regime that produces
them — the EV-heavy, high-humidity days when S19 binds and blocks back through the tight
paint buffers.

| Sim hour | Scored | Alerts | risk p50 | risk max | Unique risks |
|---|---|---|---|---|---|
| 10.8 | 338 | **0** | 0.0118 | 0.0468 | 322 |
| 16.8 | 337 | **0** | 0.0117 | 0.0437 | 322 |
| 22.8 | 330 | **0** | 0.0130 | 0.0570 | 322 |
| 28.8 | 305 | **5** | 0.0120 | 0.4061 | 293 |
| 34.8 | 303 | **27** | 0.0276 | 0.4061 | 273 |
| 40.8 | 303 | 15 | 0.0222 | 0.4061 | 281 |
| 46.8 | 302 | 9 | 0.0270 | 0.4061 | 292 |
| 52.8 | 306 | 31 | 0.0246 | 0.4061 | 273 |

Monday is quiet. The first alert appears at hour 28.8 and the peak at 34.8. Nothing in the
engine knows what day it is; the humidity schedule and the variant mix are config.

### Run totals vs the offline pipeline

| | Live twin | Offline artifact | Note |
|---|---|---|---|
| Defect rate | **2.974%** | ~3.0% configured | the frozen calibration transfers |
| Throughput | **52.1 UPH** | 53.7 UPH | live run includes the warm-up transient |
| `paint_buffer_dwell_s` p95 | **646s** | 617s | the paint cascade regime reproduces |
| Units scored | 3,753 | — | |
| Alerts | 138 (3.7%) | — | |
| Unique risk values | 3,185 / 3,753 (84.9%) | — | |

### A negative result, preserved

`risk max` is **exactly 0.4061 in every block after the first alert**. That is plant A's
documented score collapse reproducing in live mode: the alerting cohort shares one score,
so the model identifies the at-risk set and cannot rank within it. The live twin does not
paper over it — see `docs/SENSOR_ROBUSTNESS.md`'s sibling analysis and
`artifacts/benchmarks/score_diversity.json`, which measures it as
`unique_ratio 0.9%, entropy 0.000, ranking_lift_auc 0.500`.

Whether the collapse is specific to plant A or inherent to the approach is not established: the project ships one plant (ADR-068).

---

## 5. Architecture and its limitations

### Why the engine runs out of process

`serve/` may not import it. `offline/config_schema.py` imports PyYAML, PyYAML is not on
the import-wall allowlist (CLAUDE.md §2), and `offline` is explicitly in
`tests/test_serve_imports.py`'s `BANNED` set. So the live twin is a **separate deployable**
launched with `subprocess.Popen`, exactly as `serve/routers/admin.py` launches retraining
and `serve/routers/triage.py` launches the agent (ADR-036). The API and the engine share
state only through the ops database.

That is also what makes two browsers watching the same run see the same thing: there is one
engine process per run, and every observer reads its database rows.

### Known limitations

- **Single process per run.** The engine is deliberately not thread-safe; one loop owns it.
  Horizontal scaling would need run affinity or a shared event log, neither of which is
  built.
- **Start-up cost.** Calibration is a real multi-day batch run — roughly 20–30 seconds
  before the first live event. It is not instant and is not meant to be.
- **The vibration percentile is against a frozen reference**, not the live cohort. That is
  what a deployed system with a stored reference distribution would do, and it is not
  identical to the batch path's within-cohort rank.
- **Wall-clock pacing is the caller's job.** The engine advances *simulated* seconds;
  `speed` divides the sleep between advances. This is tested
  (`test_speed_does_not_change_results`): advancing 1800s in one call and in thirty 60s
  calls produces identical events.
- **No real PLC or MES integration.** See `docs/REQUIREMENTS.md`, where both are
  `NOT IMPLEMENTED`.

---

## 6. Commands

```bash
# The measured run in §4 (writes to reports/benchmark_logs/live_validate.log)
OMP_NUM_THREADS=1 python scripts/live_validate.py

# Tests: determinism, CRN, incident propagation, and the no-risk-write guard
OMP_NUM_THREADS=1 python -m pytest tests/test_live_simulation_engine.py -q

# Score-diversity measurement over the committed artifact
python -m offline.benchmarks.score_diversity
```

Always `OMP_NUM_THREADS=1` — OpenMP reduction order is not deterministic and without it
the reproducibility claim is false (ADR-016).
