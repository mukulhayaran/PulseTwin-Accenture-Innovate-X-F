# Bottleneck Benchmark

> Synthetic simulator data throughout. Nothing here is a claim about a real production
> line. See `docs/CLAIMS.md`.

Every number on this page is read from `artifacts/benchmarks/`. Regenerate with the
commands in §7; the artifacts carry the git SHA, seed set, profile and environment that
produced them.

---

## 1. Why the previous ground truth was not enough

`offline/ml/bottleneck.py` detects the constraint as the per-bucket argmax of **active
share** (working + down seconds, the Roser active-period criterion). It then scored itself
against a "ground truth" defined, in the same file, as:

```python
def ground_truth(active, ids):
    sm = smooth(active)                      # the SAME matrix, 8-bucket moving average
    return np.array(ids)[np.argmax(sm, axis=0)]
```

The module's own docstring is candid that this was chosen to avoid a trivially identical
comparison:

> *"Scoring a 15-minute argmax against a 15-minute argmax is circular and returns 100%,
> which is worse than a low number because it invites doubt about everything else."*

That reasoning is right, and the fix is still insufficient. Both quantities are functions
of **the same statistic**. Smoothing changes the time scale, not the measurement. If active
share is a biased estimate of the constraint — and for a line where blocking dominates, it
is — then the detector and its reference are wrong *together*, and the agreement between
them is high precisely because they share the bias.

**85.9% is a self-consistency figure, not an accuracy.** It
remains in `README.md` and in the artifact, relabelled, because deleting a published number
is worse than explaining it.

---

## 2. Throughput-sensitivity ground truth

The constraint of a production line has an operational definition that owes nothing to any
detector: **the station whose speed most limits the line's output.** Measure it directly.

For each station *i*:

1. Run the line. → `throughput_baseline`
2. Slow station *i*'s effective cycle time by `relative` (default +10%). → `throughput_perturbed`
3. `sensitivity_i = (throughput_perturbed − throughput_baseline) / relative`
4. Repeat under Common Random Numbers across many seeds.
5. The bottleneck is `argmin_i sensitivity_i` — the most negative, i.e. slowing it hurts most.

Units are **UPH lost per unit of relative slowdown**, so a sensitivity of −40 means a 10%
slowdown at that station costs 4.0 units per hour. Sensitivity is signed and is essentially
always negative; a station near zero is not a constraint at all — the line absorbs its
slowdown in buffer and idle time.

### Perturbation mechanics

The perturbation scales `nominal_cycle_s` on a deep copy of the config. That keeps it
inside the config contract: variant multipliers, tool wear, cycle CV and the manual-station
checklist coupling all continue to apply on top of it exactly as for any other station. A
perturbed config is still a valid config, validated by the same schema.

No simulator logic is duplicated. `offline/benchmarks/simrun.py` is a thin wrapper that
constructs the random streams and calls the existing `LineSimulator`.

### Common Random Numbers

A 5-day run's throughput varies by roughly a unit per hour across seeds. The effect of
slowing one station out of thirty by 10% is of the same order. Differencing two
independently seeded runs therefore measures mostly noise.

Under CRN the baseline and perturbed runs are built from the **same** `SeedSequence`, so
the paired difference isolates the treatment. `run_line(cfg, seed)` spawns the same six
streams `offline/simulator/generate.py` does and hands the first two to the operator pool
and the DES — which means `run_line(cfg, cfg.simulation.seed)` reproduces the pipeline's
own line run exactly, rather than a similar-but-different realisation.

**CRN is approximate in a DES, and we say so.** Changing a cycle time changes event
ordering, so after the first perturbed completion the two runs consume their shared streams
at slightly different points; the coupling degrades over the horizon rather than holding
exactly. That is the standard, accepted limitation, and it is why the benchmark still
averages over many seeds and bootstraps a confidence interval rather than trusting one
paired difference. `tests/test_bottleneck_benchmark.py::test_crn_pairing_beats_independent_seeding`
asserts the variance reduction is real rather than assumed.

### Confidence intervals

Percentile bootstrap (2,000 resamples, seed 42) over the per-seed paired sensitivities.
Deterministic given the seed set.

A scenario is reported as **separated** when the winner's bootstrap CI does not overlap the
runner-up's *and* the winner's |mean sensitivity| exceeds 1.0 UPH per unit relative change.
An unseparated scenario is not a failure — it is the honest description of an ambiguous
line, and the per-scenario rows carry `ground_truth_separated` so those cases can be
excluded from any aggregate.

### Profiles

| Profile | Seeds | Horizon | Use |
|---|---|---|---|
| `smoke` | 3 | 1 day | Machinery check only. **Never cite a number from this profile.** |
| `dev` | 30 | 5 days | Normal development runs. One scenario ≈ 960 line runs. |
| `full` | 60 | 14 days | Publication-quality. |

---

## 3. Detectors

Seven, all reading only what a real monitoring system could read. **Three of the keys carry
`_adapted`, and that is load-bearing:** the underlying idea is published, but the published
algorithm consumes a data structure this simulator does not emit, so what is implemented is
an adaptation. We do not claim to reproduce a published algorithm we have adapted.
`tests/test_bottleneck_benchmark.py::test_adapted_detectors_declare_themselves` asserts the
key and the provenance string agree.

| Key | Criterion | Provenance |
|---|---|---|
| `active_share` | argmax of (working + down) seconds | **Faithful. This is PulseTwin's shipped detector.** |
| `utilization` | argmax of working ÷ observed | Faithful; textbook utilisation |
| `busy_ratio` | argmax of (working + down + blocked) ÷ observed | Faithful; occupancy including blocked time |
| `active_period` | argmax of **mean uninterrupted active-span length** | Faithful; Roser's active-period method, measured at event level |
| `average_queue_length` | argmax of time-weighted mean **input**-buffer occupancy | Faithful; classic queueing criterion |
| `turning_point_adapted` | fraction of buckets in which the station is the momentary (per-bucket argmax) constraint | **ADAPTED** to 15-minute buckets; the published method walks an event-level trace of active periods |
| `arrow_blocked_starved_adapted` | blockage/starvation direction arrows between adjacent stations; the bottleneck is where arrows converge | **ADAPTED** to bucketed durations; the decision rule is the published one, the inputs are coarser |

Two of these needed measurements the simulator did not previously take, so `LineSimulator`
gained an **opt-in** `instrument=True` flag that records output-buffer occupancy and
active-period statistics. It is off by default and
`tests/test_bottleneck_benchmark.py::test_instrumentation_does_not_change_the_run` proves
the shipped pipeline is byte-identical with it on or off.

None of the detectors is tuned against the ground truth. They are textbook criteria
implemented once and scored as they fell.

### Metrics

Per seed, against the scenario's ground-truth station:

- **top-1** — did the detector's first choice match?
- **top-3** — was the truth in the first three?
- **MRR** — reciprocal of the truth's rank.
- **station distance** — gap in *physical position along the line* between the detector's
  first choice and the truth. Meaningful because both plants are serial lines, so "wrong by
  one station" and "wrong by twenty" are very different errors. **This metric would need
  redefining under a topology with parallel branches.**

Reported as the mean over seeds with a percentile-bootstrap CI.

---

## 4. Results

**Status: `plant_a/baseline` measured at the `dev` profile. The remaining scenarios were
still running when this document was written — see §6.**

Source: `artifacts/benchmarks/bottleneck_summary.json`, git `847b594`, numpy 2.2.3,
30 seeds (1000–1029), 5-day horizon, +10% perturbation.

### 4.1 Ground truth, plant A baseline

Baseline throughput **53.52 UPH**. Sensitivity in UPH lost per unit relative slowdown.

| Station | Mean | 95% CI | SD |
|---|---|---|---|
| **S19** Battery/Drivetrain Mount | **−40.63** | [−41.59, −39.63] | 2.62 |
| S12 Body Completion | −35.62 | [−36.12, −35.09] | 1.48 |
| S18 Cure Oven | −25.12 | [−26.07, −24.21] | 2.67 |
| S17 Clear Coat Application | −21.38 | [−22.38, −20.30] | 2.97 |
| S08 Vibration-Critical Weld | −14.33 | [−15.13, −13.50] | 2.26 |
| S30 Final Quality Gate | −13.22 | [−14.09, −12.21] | 2.65 |
| S22 Legacy Interior Fit | −12.10 | [−13.68, −10.67] | 4.25 |
| S06 Side Panel Weld | −9.57 | [−10.40, −8.81] | 2.24 |
| … | | | |
| S01 Frame Load | −0.15 | [−0.65, +0.39] | 1.49 |
| S13 Paint Pre-Treatment | −0.15 | [−0.69, +0.44] | 1.61 |
| S25 Interior Trim | −0.12 | [−0.64, +0.36] | 1.47 |

**Ground truth = S19**, runner-up S12, margin 5.01, **separated** (CIs do not overlap).

Two sanity checks the method had to pass and did:

- The three least-constraining stations sit at ≈ −0.15 with CIs **straddling zero**.
  Slowing them by 10% costs the line nothing measurable, which is what "not a bottleneck"
  should look like. A method that ranked every station as somewhat constraining would be
  measuring noise.
- The ordering is physically sensible: S19 (EV multiplier 1.50) and S12 (61.0s flat) are
  the two constraints `configs/plant_a.yaml`'s own header documents, and S17/S18 are the
  tight-buffer paint stations immediately upstream of the S19 cascade.

**A finding the config header does not predict:** over a full work week S19 outranks S12,
and by a margin of only 5.01 UPH. The header describes S12 as the "normal-day constraint"
and S19 as the "EV-heavy-day constraint"; measured over Monday–Friday the EV days dominate
the weekly average. At a 3-day horizon (Mon–Wed, both EV days) the margin widens to ~19.4.
**The constraint identity depends on the averaging window**, which is exactly the shifting
bottleneck the product claims to track.

### 4.2 Detector league table, plant A baseline

30 seeds. Ground truth S19.

| Detector | top-1 | top-3 | MRR | Mean station distance |
|---|---|---|---|---|
| **`active_share`** ← shipped | **0.93** | 1.00 | 0.967 | 0.5 |
| `utilization` | 0.93 | 1.00 | 0.967 | 0.5 |
| `active_period` | 0.70 | 1.00 | 0.850 | 2.1 |
| `turning_point_adapted` | 0.00 | 1.00 | 0.500 | 7.0 |
| `arrow_blocked_starved_adapted` | 0.00 | 1.00 | 0.500 | 7.0 |
| `average_queue_length` | 0.00 | 0.00 | 0.078 | 9.0 |
| `busy_ratio` | 0.00 | 0.00 | 0.069 | 18.0 |

**PulseTwin's shipped detector is vindicated against an independent reference.** 0.93 top-1
is a real accuracy, measured against throughput sensitivity, and it is the strongest claim
this project can make about bottleneck detection — considerably better founded than the
85.9% self-consistency figure it replaces, even though the number is higher.

`utilization` ties it exactly on this scenario. That is not a coincidence: with few
breakdowns in a 5-day window, working + down ≈ working. The two criteria diverge when
downtime rather than cycle time limits the line, which this scenario does not exercise.
**On this evidence there is no reason to prefer active share over plain utilisation**, and
saying so is more useful than claiming the shipped choice is uniquely right.

#### The failures are informative and are kept

- **`busy_ratio` (top-1 0.00, distance 18.0)** counts blocked time as busy, so it selects
  stations immediately *upstream* of the constraint — the ones piling up because they
  cannot hand off. Its top choices are S1, S2, S3: the start of the line, 18 stations away
  from the truth. This is the predicted failure mode, and its docstring predicted it before
  the benchmark ran.
- **`average_queue_length` (top-1 0.00, distance 9.0)** fails for the mirror-image reason:
  a bottleneck starved from upstream has no queue, and a station with a large configured
  buffer accumulates more regardless of whether it constrains anything.
- **`turning_point_adapted` and `arrow_blocked_starved_adapted`** both score top-3 1.00 with
  top-1 0.00 and MRR exactly 0.500 — they place the truth **second, every time**. They
  consistently prefer S12 over S19. Both are sensitive to sustained blocking, and S12's
  block signature is stronger even though S19 costs more throughput. A detector that is
  reliably second is far more useful than one that is randomly wrong, and MRR is what
  surfaces that distinction.

---

## 5. Reproducing

```bash
# The measured result in section 4 (~50 min on 3 workers)
OMP_NUM_THREADS=1 python -m offline.benchmarks.run_bottleneck_benchmark \
    --plant plant_a --profile dev --workers 3

# One scenario only
OMP_NUM_THREADS=1 python -m offline.benchmarks.run_bottleneck_benchmark \
    --plant plant_a --scenario s12_constraint

# Publication profile: 60 seeds, 14-day horizon
OMP_NUM_THREADS=1 python -m offline.benchmarks.run_bottleneck_benchmark \
    --plant plant_a --profile full --workers 3

# Machinery tests (short horizons, few seeds -- not the published numbers)
OMP_NUM_THREADS=1 python -m pytest tests/test_bottleneck_benchmark.py -q
```

Artifacts are **merged** across invocations keyed on (plant, scenario, station/detector), so
the two plants can be run as separate jobs and a single scenario re-run after a fix without
discarding the rest. Results are checkpointed after every scenario.

The nightly workflow (`.github/workflows/nightly.yml`) runs both plants and uploads the
artifacts; it deliberately does **not** commit them, because a regenerated benchmark that
moves a documented number is a claim change and needs a human.

---

## 6. Limitations

- **Measured: `plant_a/baseline` and `plant_a/s12_constraint`** (30 seeds each; the
  baseline rerun on 2026-09-26 at 06080a7 reproduced the earlier numbers exactly).
  `s12_constraint` is an engineered, well-separated constraint: `active_share`,
  `utilization`, `active_period` and both adapted detectors all score top-1 1.00 on it,
  `average_queue_length` 0.00 (top-3 1.00, distance 2), and `busy_ratio` 0.00 (distance 11).
  A perfect score on a deliberately obvious constraint is the expected result, not evidence
  of a perfect detector: the baseline (0.93) is the informative number, and the macro
  league table in `bottleneck_summary.json` now averages both scenarios (0.967).
  `s19_constraint` and `ambiguous_s12_s19` are **NOT RUN**; no
  numbers are quoted for them.
- **One plant, one horizon, one perturbation size.** The +10% probe is a local
  sensitivity; a line with a strongly non-linear response could rank differently under
  +25%. Not tested.
- **Ground truth is a whole-run quantity**, so detectors are compared on a run-level
  ranking. The shipped detector also makes per-bucket calls, which this benchmark does not
  score — the shifting-bottleneck *timing* claim is not validated here.
- **`station_distance` assumes a serial line.** It is not meaningful under the parallel /
  merge / rework topologies in `docs/REQUIREMENTS.md`.
- **The `separated` rule is a heuristic**, not a hypothesis test with a corrected
  significance level across 30 stations.
- **CRN degrades over the horizon** (§2). The paired differences are less tightly coupled
  at day 5 than at hour 1.
