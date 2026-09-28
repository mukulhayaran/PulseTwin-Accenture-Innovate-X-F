# Public datasets used to validate PulseTwin against real data

Everything else in this repository is validated against `offline/simulator/`'s output.
That is honest but incomplete: a model can be well-calibrated against its own simulator
and still say nothing about a real production line. This document names the public
datasets `benchmark/` uses to close that gap, what each one can and cannot actually prove,
and why the list is short by design rather than by omission.

Read `docs/benchmarking.md` for how to run these; this file is about what the numbers
mean, not how to produce them.

---

## AI4I 2020 Predictive Maintenance Dataset

**Registered as:** `ai4i` · **Source:** UCI Machine Learning Repository (direct download,
no authentication) · **10,000 rows, one simulated milling machine.**

**What it validates:**
- The dataset adapter interface end to end, on a small, fast, clean dataset — this is the
  pipeline smoke test, not the headline claim.
- That the canonical cross-dataset schema (`benchmark/adapters/base.py::CANONICAL_COLUMNS`)
  actually works when a dataset has real physical semantics: `Torque [Nm]` maps to the
  canonical `torque` field because it genuinely is torque, unlike Bosch below. This is the
  positive-case contrast Bosch's adapter needs to make its own limitation legible.
- Basic imbalanced-classification discipline (PR-AUC/precision/recall/F1, never accuracy
  alone) at low cost, so problems here are cheap to catch before Bosch.

**What it cannot validate:**
- Anything about a production LINE. It is one machine; there is no station, line, or
  multi-equipment structure, so `line_id`/`station_id` are `NA` for every row — not a bug,
  a fact about the dataset (see `benchmark/registry.py::AI4I.limitations`).
- Time-safe splitting or temporal drift — there is no production-order timestamp, so every
  split here is a seeded stratified split, never chronological.
- Missing-data robustness on its own — the released data has zero missing values; the
  sensor-coverage numbers for AI4I are entirely synthetic masking, not real missingness.

## Bosch Production Line Performance

**Registered as:** `bosch` · **Source:** Kaggle competition (`bosch-production-line-
performance`), requires a Kaggle account and accepted competition rules — this repository
never automates that download (CLAUDE.md: no secrets committed); `benchmark/adapters/
bosch.py::download()` detects a missing file and prints the exact manual-download
instruction rather than failing mysteriously. **~1.18M rows, ~968 anonymized numeric
features, ~0.58% positive rate.**

**Why Bosch is the primary production benchmark, not a second smoke test:** it is a real,
famous, heavily-imbalanced classification problem from an actual assembly-line context,
large enough that "don't load the whole thing into memory" is a genuine engineering
constraint rather than a hypothetical one, and its extreme imbalance is a strong showcase
for the PR-AUC/precision/recall discipline this project already applies to its own
simulator (`offline/ml/thresholds.py`, `docs/MODEL_CARD.md`).

**What it validates:**
- Defect prediction on real, large-scale, heavily-imbalanced manufacturing data.
- Chunked, memory-safe ingestion of a dataset too large to load naively (`pd.read_csv`
  in one call would be the wrong answer here, and the adapter is built around that).
- Sensor-coverage robustness (RANDOM and STATION_LEVEL masking — see `docs/
  benchmarking.md`) against a dataset with real, non-synthetic feature-sparsity structure.
- That the canonical-schema idea survives contact with a dataset it does NOT fit cleanly
  — see the next point, which is the point.

**What it cannot validate, and why — read this before quoting a Bosch number:**
- **Physical-signal fields.** Every one of Bosch's ~968 numeric features is anonymized:
  `L3_S36_F3939` encodes line 3, station 36, feature 3939 by POSITION — `benchmark/
  adapters/bosch.py::parse_feature_name()` recovers exactly that structure and nothing
  more. There is no published mapping from any column to torque, temperature, vibration,
  or pressure. `normalize()` therefore leaves `torque`/`temperature`/`vibration`/
  `pressure` as `NA` for every Bosch row — always, not "when data is missing." This is
  pinned directly by a test
  (`tests/test_benchmark_adapters.py::test_bosch_normalize_leaves_every_physical_signal_field_null`).
- **Time-safe splitting.** The standard release has no usable production-order timestamp;
  `create_train_test_split(strategy="chronological")` is refused outright (raises
  `ValueError`) rather than silently downgraded to something that looks safer than it is.
  Every Bosch split in this repo is seeded-stratified.
- **Root-cause analysis.** Station/feature correlation with `Response` is an association,
  not a cause — there is no way to know WHY a station correlates with failure when the
  station's own signals are anonymous. Fault-injection RCA evaluation (`benchmark/
  rca_eval.py`) is **not attempted on Bosch** for exactly this reason; it runs against
  `offline/simulator/`'s own output instead, where the true cause is actually known (see
  below).

## Why fault-injection RCA evaluation runs on the simulator, not on Bosch

The original design brief this subsystem was scoped from asked for a synthetic
fault-injection engine (9 injector types) to manufacture RCA ground truth on public
datasets. That does not work for Bosch, Scania, or SECOM: a `TorqueDriftInjector` needs a
column that is *known* to be torque, and none of these three datasets has one. Injecting a
"torque drift" into an anonymous column and then claiming to validate root-cause analysis
against it would be validating against a label this project invented, not evidence the
dataset provides — exactly the kind of fabricated-ground-truth claim CLAUDE.md's §1 rules
already forbid for economics figures and ML metrics, applied here to a third case.

`offline/simulator/causal_engine.py` already produces real, load-bearing ground truth with
none of that problem: `defect_mode` ('mode_a'/'mode_b'/'mode_c') per unit, generated by the
simulator's own physics and explicitly never shown to the scoring model. `configs/
plant_a.yaml`'s per-station `defect_role` field names exactly which station(s) drive each
mode. `benchmark/rca_eval.py` evaluates the EXISTING decision-path explanation
(`unit_factors`, already computed and stored for every scored unit — no new agent or
injection code) against that ground truth. See `docs/benchmarking.md` for the actual
numbers this produced, including two genuinely interesting findings: mode_c (an
interaction-effect defect, torque deficit AND vibration excess) was never once flagged by
the model, and mode_a's top-ranked explanation is often a correlated-but-wrong signal
rather than its true cause.

## Roadmap: datasets considered but not built this pass

Deferred, not rejected — each one plugs into `benchmark/registry.py`'s existing adapter
interface the same way AI4I and Bosch did, and adding one later is meant to be closer to
"write a new adapter" than "redesign the subsystem."

- **APS Failure at Scania Trucks** — same anonymization problem as Bosch (`aa_000`,
  `ab_000`, ...), and it is fleet/component sensor data, not production-line data. Would
  extend the "real, imbalanced automotive predictive maintenance" story on a second real
  dataset once one is already proven out.
- **SECOM** — 590 anonymized semiconductor-fab sensors. Same anonymization limitation as
  Bosch, plus it is not automotive at all; useful mainly for the high-dimensional/
  near-zero-variance feature-selection story, which nothing here currently needs.
- **NASA Milling / tool-wear data** — the one deferred dataset that likely DOES carry
  physical semantics (vibration, current, wear are typically labeled), which would make
  it a legitimate second home for fault-injection-style RCA evaluation, unlike Bosch/
  Scania/SECOM. Deferred for acquisition-risk reasons (the canonical public release is
  less standardized than Bosch/AI4I's), not a methodological objection.
- **MIMII DUE** — machine acoustic anomaly detection. Requires an audio feature-extraction
  pipeline (MFCC/spectral features, a `librosa`-class dependency this repo does not
  currently have) and large downloads; a legitimate "sensor-poor station" story, sized as
  its own multi-day piece of work, not a fast follow.
- **FordA** — a UCR/UEA time-series classification benchmark. Ford-branded but not
  production-line or predictive-maintenance data in the sense the other datasets are;
  lowest priority of the five.
