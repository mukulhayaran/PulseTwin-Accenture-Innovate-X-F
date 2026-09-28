# Claim and Citation Ledger

Every number PulseTwin publishes, the artifact it comes from, the qualifier it must carry,
and the wording it does **not** support.

**The governing fact, which qualifies everything below:**

> PulseTwin has never been connected to a real production line. Every metric on this page
> was measured on data PulseTwin's own simulator generated, with the single exception of
> §5, which uses a public dataset and validates the *pipeline*, not the automotive task.

Numbers marked `<!--metric:...-->` in README.md and the other documents listed in
`tools/metrics_summary.py::DOCUMENTED_FILES` are machine-checked against
`artifacts/metrics_summary.json` by `python tools/metrics_summary.py --check`, which CI
runs. A documented number that drifts from its artifact fails the build.

---

## 1. Defect prediction

### CLAIM — PR-AUC = 0.256

| | |
|---|---|
| **Source** | `artifacts/pulsetwin.db` → `metrics.pr_auc` (plant A) |
| **Artifact** | `artifacts/metrics_summary.json` → `plants.plant_a.pr_auc` |
| **Qualifier** | Synthetic held-out test week from PulseTwin's own simulator. 9,046 units, 228 positives, 2.5% base rate. Temporal split on whole-week boundaries. 95% CI [0.207, 0.308]. |
| **Allowed** | "On synthetic held-out data from our simulator, the model reaches PR-AUC 0.256 — 53% of a measured oracle ceiling of 0.481." |
| **Allowed** | "0.256 against a no-skill baseline of 0.025 is a 10× lift over the base rate." |
| **PROHIBITED** | ~~"PulseTwin predicts real automotive defects with 0.256 PR-AUC."~~ |
| **PROHIBITED** | ~~"PulseTwin achieves 26% accuracy."~~ PR-AUC is not accuracy. |
| **PROHIBITED** | Any comparison to a published real-factory benchmark. Different task, different data, no shared test set. |

### CLAIM — Oracle ceiling = 0.481, capture = 53%

| | |
|---|---|
| **Source** | `metrics.oracle_pr_auc`, `metrics.oracle_capture` |
| **Qualifier** | The oracle is the **simulator's own latent defect propensity** scored as if it were a predictor. It is the best any model could do given the simulator's deliberately unobservable causes (lot quality, paint-batch quality, irreducible noise). It is a property of this simulator, not of manufacturing. |
| **Allowed** | "The model captures 53% of the headroom that exists in our simulator." |
| **Allowed** | "The brief's 0.75–0.88 target is arithmetically unreachable given its own requirement of unobservable causes (`docs/AUDIT.md` §B34)." |
| **PROHIBITED** | ~~"53% of the theoretical maximum for defect prediction."~~ There is no such universal maximum. |
| **PROHIBITED** | ~~"A real plant would see the same ceiling."~~ Unknown and untestable here. |

### CLAIM — Precision 35% / recall 28% at the operating threshold

| | |
|---|---|
| **Source** | `metrics.precision_at_budget`, `metrics.recall_at_budget` |
| **Qualifier** | At the alert-budget threshold 0.4061, plant A, synthetic test split. |
| **Allowed** | "At a 6-alerts-per-shift budget the model catches 28% of defects at 35% precision **on plant A**." |
| **PROHIBITED** | Quoting plant A's figures as "PulseTwin's" in general: one synthetic plant is measured, and transfer to another line is not established. |

### CLAIM — 8.5 alerts per shift

| | |
|---|---|
| **Source** | `metrics.alerts_per_shift` |
| **Qualifier** | **Against a configured budget of 6.** This is an overshoot, not a target being met. |
| **Allowed** | "The threshold was fitted for 6 alerts/shift and delivers 8.5 on held-out data — a 42% overshoot, and the reason rolling recalibration is on the roadmap." |
| **PROHIBITED** | ~~"PulseTwin delivers a tuned 8.5 alerts per shift."~~ It was not tuned to 8.5; it missed. |

---

## 2. Within-alert discrimination — a negative result

### CLAIM — plant A's alert cohort has one unique score

| | |
|---|---|
| **Source** | `artifacts/benchmarks/score_diversity.json` |
| **Producer** | `python -m offline.benchmarks.score_diversity` |
| **Measured** | Plant A: 113 alerts, **1 unique score** (0.406079), unique ratio 0.9%, normalised entropy 0.000, within-cohort ranking AUC **0.500**. |
| **Qualifier** | Every tree in plant A's ensemble splits on `paint_buffer_dwell_s` at the root; past that threshold no later split discriminates, so all such units land in the same leaf in every tree. |
| **Allowed** | "The model identifies the at-risk set and **cannot rank within it** on plant A. A supervisor with time for six of forty flagged units gets no help choosing. This is measured, reproduced in the live twin, and unfixed." |
| **PROHIBITED** | ~~"PulseTwin prioritises the highest-risk units for inspection."~~ On plant A it demonstrably does not. |
| **PROHIBITED** | Presenting a ranked alert list as if the order carried information, on plant A. |

---

## 3. Bottleneck detection

### CLAIM — detector top-1 accuracy 0.93

| | |
|---|---|
| **Source** | `artifacts/benchmarks/bottleneck_detector_results.csv`, `bottleneck_summary.json` |
| **Producer** | `python -m offline.benchmarks.run_bottleneck_benchmark --plant plant_a --profile dev` |
| **Qualifier** | **Plant A, `baseline` scenario only.** 30 seeds, 5-day horizon, +10% cycle-time perturbation, ground truth by throughput sensitivity under Common Random Numbers with bootstrap CIs. |
| **Allowed** | "Against an independent throughput-sensitivity ground truth, the shipped active-share detector identifies the constraint first-choice 93% of the time on plant A's baseline scenario." |
| **Allowed** | "Plain utilisation ties it exactly (0.93) on this scenario, so this evidence does not justify preferring active share." |
| **PROHIBITED** | ~~"PulseTwin detects bottlenecks with 93% accuracy."~~ One plant, one scenario. Six of seven planned scenarios are **NOT RUN**. |
| **PROHIBITED** | ~~"Our detector beats the alternatives."~~ It ties one and beats four; the two adapted detectors place the truth second every time (MRR 0.500). |

### CLAIM — bottleneck detection accuracy 85.9% / 90.2%

| | |
|---|---|
| **Source** | `metrics.bottleneck_detection_accuracy` |
| **Qualifier** | **This is a self-consistency figure, not an accuracy.** The reference is the same active-share matrix the detector consumes, smoothed over 8 buckets (`offline/ml/bottleneck.py::ground_truth`). Detector and reference share any bias in the statistic. |
| **Allowed** | "85.9% agreement between a 15-minute detection and a 2-hour smoothed reference computed from the same signal." |
| **PROHIBITED** | ~~"85.9% bottleneck detection accuracy."~~ Retained in README **relabelled**, because deleting a published number is worse than explaining it. Cite the 0.93 figure in §3 instead. |

### CLAIM — forecast skill +0.136 / +0.205 vs persistence

| | |
|---|---|
| **Source** | `metrics.forecast_skill_vs_persistence` |
| **Qualifier** | 15-minute horizon, ridge regression on 4 lags, temporal split. Benchmarked against persistence, **not** against the throughput-sensitivity ground truth. |
| **Allowed** | "The forecaster beats 'assume nothing changes' by 13.6% MAE on plant A." |
| **PROHIBITED** | ~~"PulseTwin predicts bottleneck shifts 15 minutes ahead."~~ It predicts the active-share series; whether the *constraint identity* shifts correctly is not validated. |

---

## 4. Soft sensors — a published negative result

### CLAIM — ambient R² 0.965–0.996; tool-wear R² −0.55 to −0.07

| | |
|---|---|
| **Source** | `metrics_json.soft_sensor_validation` |
| **Qualifier** | Masked-station evaluation: real sensors on S9/S17/S21 are hidden and reconstructed. Synthetic throughout. A **negative** R² means worse than predicting the mean. |
| **Allowed** | "Ambient-driven signals (temperature, humidity, film thickness) reconstruct at R² 0.97–1.00. Tool-wear-driven signals (torque, vibration) **do not reconstruct at all** — R² at or below zero. We publish both." |
| **Allowed** | "Three of four retrofit instrumentation candidates score zero uplift." |
| **PROHIBITED** | ~~"PulseTwin replaces 90% of physical sensors."~~ |
| **PROHIBITED** | ~~"Soft sensors recover missing readings."~~ Only some, and the ones that matter most for Mode C are exactly the ones that fail. |
| **PROHIBITED** | Quoting the ambient R² without the tool-wear result in the same breath. |

### CLAIM — unsensored-station evidence is worth +0.060 PR-AUC

| | |
|---|---|
| **Source** | `metrics.soft_sensor_ablation_delta` |
| **Qualifier** | **The confidence intervals overlap.** The artifact itself records "improvement is within confidence intervals, reported as measured". |
| **Allowed** | "+0.060 PR-AUC (23% relative), though the confidence intervals overlap, so we report it as measured rather than as an established effect." |
| **PROHIBITED** | ~~"Soft sensors improve defect prediction by 23%."~~ Not at this sample size. |

---

## 5. External benchmark — a different claim entirely

### CLAIM — AI4I 2020 PR-AUC 0.773

| | |
|---|---|
| **Source** | `benchmark/reports/ai4i_defect_prediction_*.json` |
| **Dataset** | AI4I 2020 Predictive Maintenance Dataset (UCI Machine Learning Repository) — 10,000 rows, 3.4% failure rate, synthetic-but-public, widely used |
| **Licence** | CC BY 4.0. Cite: Matzka, S. (2020), *Explainable Artificial Intelligence for Predictive Maintenance Applications*, AI4I 2020. |
| **Task** | Binary machine-failure prediction from 8 process features. **Not** automotive assembly defect prediction. |
| **Split** | Stratified, seeded (42), class-balanced. Not temporal — the dataset has no meaningful time ordering. |
| **Model** | `HistGradientBoostingClassifier` + Platt calibration |
| **Measured** | PR-AUC 0.773 [0.657, 0.873]; ROC-AUC 0.988 [0.979, 0.994]; base rate 3.5% |
| **Purpose** | To show the training/evaluation *pipeline* produces sane results on data PulseTwin did not generate. |
| **Allowed** | "On the public AI4I 2020 dataset our pipeline reaches PR-AUC 0.773, confirming the training and evaluation code behaves correctly on external data." |
| **PROHIBITED** | ~~"PulseTwin achieves 0.773 PR-AUC."~~ Different task, different data. |
| **PROHIBITED** | ~~"This validates PulseTwin's defect prediction."~~ It validates the pipeline, nothing more. |
| **PROHIBITED** | Presenting 0.773 anywhere near the 0.256 figure without the distinction being unmissable. |
| **PROHIBITED** | Surfacing this in the Leadership view as business performance. It is a technical control. |

### Why AI4I is easier

Worth stating plainly so the gap is not read as a PulseTwin failure. AI4I's failure modes
are **directly observable** in its features (tool wear minutes, torque, rotational speed);
its target is a near-deterministic function of them. PulseTwin's simulator deliberately
hides its dominant causes. A higher score on an easier task is not evidence about the
harder one.

---

## 6. Live simulation

### CLAIM — alerts emerge from simulated physics, not from a script

| | |
|---|---|
| **Source** | `docs/LIVE_SIMULATION.md` §4; `scripts/live_validate.py` |
| **Measured** | Plant A, seed 42: 0 alerts through simulated hour 22.8, then 5 → 27 → 15 → 9 as the run reaches the EV-heavy/high-humidity days. Defect rate 2.974% against ~3% configured; throughput 52.1 UPH. |
| **Enforcement** | `tests/test_live_simulation_engine.py::test_no_module_writes_a_risk_value` parses the AST of every file in `offline/live_twin/` and fails on any assignment to a risk field not sourced from `Scorer.predict`. Verified to fail when `record.risk += 0.5 * 0.7` is injected. |
| **Allowed** | "In live simulation mode, risk is computed by the trained model from features derived from simulated physics; an incident changes a physical condition and the model reacts, or does not." |
| **PROHIBITED** | Making this claim about **Scenario Injection**, which writes `calibrated_risk += 0.5 * severity` directly (`serve/live_simulation.py::_raise_risk`). Those are separate modes with separate names for exactly this reason. |
| **PROHIBITED** | ~~"PulseTwin runs a live digital twin of a factory."~~ It runs a live simulation of a *simulated* factory. |

---

## 7. Economics

Every dollar figure traces to `configs/economics.yaml` or a named assumption in
`docs/ASSUMPTIONS.md`, and carries a `source` field through the API to the UI (contract 2,
CLAUDE.md §3).

| | |
|---|---|
| **Qualifier** | Industry-reference unit costs applied to **simulated** throughput and defect counts. Achieved throughput is read from the simulator, never from the config's target (ADR-002). |
| **Allowed** | "Applying published industry cost references to our simulated line yields …" |
| **PROHIBITED** | ~~"PulseTwin saves $X per year."~~ No real line, no real saving. |
| **PROHIBITED** | Any ROI figure presented without the synthetic qualifier attached in the same visual element. |

---

## 8. External facts and citations

Numbers in the documentation that did not come from this repository.

| Fact | Used in | Source |
|---|---|---|
| AI4I 2020 dataset, 10k rows, 3.4% failure rate | `benchmark/`, §5 | Matzka, S. (2020). *Explainable AI for Predictive Maintenance Applications*. UCI ML Repository. CC BY 4.0 |
| Roser active-period criterion for bottleneck detection | `offline/ml/bottleneck.py`, `docs/BOTTLENECK_BENCHMARK.md` | Roser, Nakano & Tanaka, *Shifting Bottleneck Detection* (Winter Simulation Conference, 2002) |
| Blockage/starvation "arrow" direction methods | `bottleneck_detectors.py` — **adapted, not reproduced** | Li, Chang, Ni et al., data-driven bottleneck detection literature |
| Nelson rules for SPC control charts | `mlops/fallback.py` | Nelson, L. S. (1984), *Journal of Quality Technology* 16(4) |
| Platt scaling for probability calibration | `offline/ml/train.py` | Platt, J. (1999), *Probabilistic Outputs for SVMs* |
| Decision-path ("Saabas") feature attribution | `serve/scorer.py::explain` | Saabas, A. — interpreting random forests. **Explicitly not SHAP**; the API reports `explanation_mode: "decision_path"` |
| Common Random Numbers as a variance-reduction technique | `offline/benchmarks/simrun.py` | Standard DES practice; e.g. Law & Kelton, *Simulation Modeling and Analysis* |

Unsourced external numbers are not permitted in README.md or any `docs/` file. If a figure
has no row here and no artifact, it does not go in.

---

## 9. Benchmarks that have NOT been run

Listed so their absence is explicit. None of these has a number anywhere in the repo.

| Benchmark | Status |
|---|---|
| Bottleneck scenarios `s12_constraint`, `s19_constraint`, `ambiguous_s12_s19` (plant A) | **NOT RUN** |
| Progressive sensor-loss degradation sweep | **NOT IMPLEMENTED** |
| Segmented drift by variant / shift / zone / humidity regime | **NOT IMPLEMENTED** |
| Explanation-diversity metrics | **NOT IMPLEMENTED** |
| Rolling alert-budget recalibration | **NOT IMPLEMENTED** |
| Any evaluation on real factory data | **NOT POSSIBLE** — no such data exists for this project |
