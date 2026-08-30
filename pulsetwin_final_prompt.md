# PULSETWIN — MASTER BUILD PROMPT
# Digital Twin for Vehicle Assembly Line — Hackathon Round 2

---

## 0. WHO YOU ARE & HOW YOU WORK

You are a Staff-level ML + Full-Stack engineer who has shipped production digital twins in automotive manufacturing. You understand ISA-95, OPC-UA, discrete-event simulation, and cost-sensitive classification.

You will build **PulseTwin** — a digital twin prototype for a mixed-model vehicle assembly line — end to end, autonomously. Work through every phase in order. After each phase: run that phase's acceptance checks, print results, `git add -A && git commit -m "phase N: description"`, then continue immediately. Do NOT stop to ask for approval. Only stop if an acceptance check fails twice and you cannot resolve it.

**Your north star:** the judges' rubric is the seven "Real-World Complexities" bullets in the brief. Every bullet must be answered by working code, not by prose. If a feature doesn't map to a rubric bullet, it's decoration — cut it and spend the time on something that does.

**Memory constraint:** The deployed backend must run under **200 MB RSS**. The frontend must be a static export (zero server RAM). This means: heavy libraries (pandas, xgboost, shap, simpy, sklearn) exist only in the offline build pipeline. The serving layer uses only fastapi, uvicorn, pydantic, numpy, orjson, and stdlib (sqlite3, json, math, pathlib, csv). Precompute everything. Serve thin.

---

## 1. THE TUESDAY PROBLEM — THE SCENARIO SPINE

Everything in this prototype is built around one coherent, demonstrable scenario. Read this section carefully — the simulator, the models, the demo data, and the UI are all tuned to make this story playable on screen.

### The factory

A mixed-model assembly line builds three vehicle variants: **SEDAN** (50% of volume), **SUV** (35%), and **EV** (15% normally). The line has 30 stations across three zones:

- **Body Construction (S1–S12):** Welding, structural fastening. Sensors: weld current, torque, vibration.
- **Paint (S13–S18):** Surface prep, primer, base coat, clear coat, cure, inspection. Sensors: humidity, temperature, film thickness. Station 15 is a **manual prep station — no sensors, manual checklist only**.
- **Final Assembly (S19–S30):** Drivetrain, interior, electrical, fluids, alignment, final inspection at S30. Sensors: torque, vibration. Stations 22 and 27 are **legacy manual stations**. The EV variant has extra battery-mounting work at S19–S24 (1.4× cycle time multiplier).

### The Tuesday cascade

On Tuesdays and Wednesdays, the production schedule is EV-heavy (40% EV). This creates a specific cascade that no single station metric reveals:

1. **EV units bottleneck at S19–S24** due to the extra battery work. The constraint shifts from its normal position (S12) to S19.
2. **The bottleneck blocks upstream stations S15–S18** (paint zone). Units sit in the paint-cure buffer longer than the optimal window.
3. **Extended dwell in the paint buffer causes Defect Mode A:** paint micro-blistering. This is a physics-based defect — cure chemistry is time-sensitive.
4. **When the bottleneck clears in bursts, Station 15 (manual, no sensors) rushes.** Operators compress their prep checklist from 45 seconds to 20 seconds. This causes **Defect Mode B:** insufficient surface prep leading to adhesion failures.
5. **Neither defect is visible until Station 30** (final quality gate), 25–40 minutes later. By then, 30+ units have passed through the same conditions.
6. **The two defect modes correlate** (both caused by the bottleneck) **but have different root causes** (physics vs human behaviour). A model that finds one doesn't automatically find the other.

### Why this is hard to learn

- **Defect Mode A** depends on paint-buffer dwell time (observable from genealogy timestamps), ambient humidity (observable), and a **latent paint-batch quality variable** (unobservable — changes every ~200 units).
- **Defect Mode B** depends on operator behaviour at a station with **no continuous sensors** — only the checklist binary and the timestamp gap between consecutive units.
- **Both modes are intermittent:** they only fire when the EV mix is high AND humidity is above 55% AND the paint batch is from the weaker supplier. This co-occurrence happens ~30% of production hours.
- **Incoming part lot quality** (for body stations) varies by lot and is never directly measured. It's a confounder that creates noise.
- **Inspection at S30 is imperfect:** 92% detection, 2% false-reject. Some defects escape.
- **Target realised defect rate: 2.5–4%.** Two defect modes, each contributing roughly half.
- **Label noise: ~3%** (from imperfect inspection).
- **Achievable model ceiling: PR-AUC 0.75–0.88.** If the model exceeds 0.92, the simulator is too clean.

---

## 2. REPO STRUCTURE

Create this exactly. Print the tree before writing any code.

```
pulsetwin/
├── README.md
├── Makefile
├── docker-compose.yml
├── .gitignore
│
├── docs/
│   ├── BUSINESS_PROPOSAL.md
│   ├── ARCHITECTURE.md
│   ├── MODEL_CARD.md
│   ├── ASSUMPTIONS.md
│   └── DEMO_SCRIPT.md
│
├── configs/
│   ├── plant_a.yaml          # 30 stations, 3 legacy, the Tuesday Problem line
│   ├── plant_b.yaml          # 42 stations, 8 legacy, different layout
│   └── economics.yaml        # every $ figure with a source field
│
├── offline/                   # heavy libraries allowed here — NEVER ships in runtime image
│   ├── __init__.py
│   ├── simulator/
│   │   ├── __init__.py
│   │   ├── line_model.py      # discrete-event flow with buffers, blocking, starving
│   │   ├── physics.py         # correlated signal generation (torque, vibration, thermal, paint)
│   │   ├── causal_engine.py   # the Tuesday Problem defect modes
│   │   ├── operators.py       # shift patterns, operator skill, rush behaviour
│   │   ├── inspection.py      # imperfect end-of-line + in-line gates
│   │   └── generate.py        # CLI: python -m offline.simulator.generate --config configs/plant_a.yaml
│   ├── ml/
│   │   ├── __init__.py
│   │   ├── featurize.py       # station-level → one-row-per-unit feature matrix
│   │   ├── soft_sensor.py     # virtual metrology for legacy stations
│   │   ├── defect_model.py    # XGBoost + calibration + SHAP
│   │   ├── bottleneck.py      # shifting-bottleneck detection + 15-min forecast
│   │   ├── thresholds.py      # cost-optimal threshold + alarm budget curve
│   │   ├── validate.py        # temporal CV, ablations, masked-station validation
│   │   └── train.py           # CLI: python -m offline.ml.train --config configs/plant_a.yaml
│   ├── build_artifact.py      # bakes everything into artifacts/pulsetwin.db + stream JSONL
│   └── export_model.py        # dumps trained model to numpy arrays (.npz)
│
├── serve/                     # ONLY stdlib + fastapi + uvicorn + pydantic + numpy + orjson
│   ├── main.py
│   ├── schemas.py
│   ├── scorer.py              # pure-numpy tree traversal + isotonic calibration
│   ├── db.py                  # sqlite3 helpers
│   ├── routers/
│   │   ├── stream.py          # SSE replay from JSONL
│   │   ├── predict.py         # live defect prediction
│   │   ├── trace.py           # containment / unit genealogy
│   │   ├── bottleneck.py      # current + history + forecast
│   │   ├── whatif.py           # interpolated precomputed grid
│   │   ├── economics.py       # ROI + instrumentation planner
│   │   └── feedback.py        # supervisor alert verdicts
│   ├── Dockerfile
│   └── requirements.txt       # ONLY: fastapi uvicorn[standard] pydantic numpy orjson
│
├── frontend/
│   ├── next.config.ts
│   ├── tailwind.config.ts
│   ├── package.json
│   ├── tsconfig.json
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx           # landing / persona selector
│   │   ├── supervisor/
│   │   │   └── page.tsx
│   │   ├── manager/
│   │   │   └── page.tsx
│   │   └── leadership/
│   │       └── page.tsx
│   ├── components/
│   │   ├── layout/
│   │   │   ├── Navbar.tsx
│   │   │   ├── Sidebar.tsx
│   │   │   └── PlantSelector.tsx
│   │   ├── supervisor/
│   │   │   ├── LineMap.tsx            # 30 station tiles, zone-grouped, colour-coded
│   │   │   ├── StationTile.tsx        # individual station with state + inferred badge
│   │   │   ├── AlertFeed.tsx          # live scrolling alert cards
│   │   │   ├── AlertCard.tsx          # probability, causes, lead time, thumbs up/down
│   │   │   ├── ContainmentPanel.tsx   # units-at-risk drawer
│   │   │   ├── AlertBudgetCounter.tsx # "Alerts: 4/6 this shift"
│   │   │   ├── UnitTracker.tsx        # currently selected unit journey
│   │   │   └── ShiftSummary.tsx       # shift-level KPIs
│   │   ├── manager/
│   │   │   ├── BottleneckTimeline.tsx  # 24h/7d constraint migration chart
│   │   │   ├── StationLeagueTable.tsx  # ranked by lost throughput
│   │   │   ├── RootCausePareto.tsx     # top causes by contribution
│   │   │   ├── WhatIfConsole.tsx       # sliders + projected outcome
│   │   │   ├── TrendCharts.tsx         # cycle time, defect rate, throughput over time
│   │   │   └── VariantMixImpact.tsx    # defect rate by variant and shift
│   │   ├── leadership/
│   │   │   ├── KPICards.tsx            # big numbers: scrap saved, ROI, payback
│   │   │   ├── ValueBridge.tsx         # waterfall chart baseline → twin-enabled
│   │   │   ├── SensitivityToggle.tsx   # pessimistic / base / optimistic
│   │   │   ├── InstrumentationPlanner.tsx # retrofit table with maintenance windows
│   │   │   ├── CostBreakdown.tsx       # where value comes from
│   │   │   └── RolloutTimeline.tsx     # phased deployment roadmap
│   │   └── shared/
│   │       ├── ProvenanceBadge.tsx     # "MEASURED" | "INFERRED" badge
│   │       ├── ConfidenceBand.tsx      # visual CI band
│   │       ├── SpeedControl.tsx        # replay speed multiplier
│   │       ├── ConnectionStatus.tsx    # SSE connection indicator
│   │       ├── MetricCard.tsx          # reusable KPI card
│   │       └── ChartWrapper.tsx        # recharts wrapper with consistent styling
│   └── lib/
│       ├── api.ts              # all fetch/SSE logic, typed
│       ├── types.ts            # TypeScript interfaces matching backend schemas
│       ├── constants.ts        # colours, thresholds, zone definitions
│       └── hooks/
│           ├── useStream.ts    # SSE hook with reconnect + backoff
│           ├── useApi.ts       # generic fetch with loading/error states
│           └── useAlerts.ts    # alert accumulation + budget tracking
│
├── artifacts/                  # COMMITTED — the demo runs from these, no training needed
│   ├── pulsetwin.db            # SQLite: all precomputed results
│   ├── model.npz               # numpy-exported tree ensemble + calibration
│   ├── stream_plant_a.jsonl    # replay feed for plant A
│   ├── stream_plant_b.jsonl    # replay feed for plant B
│   └── metrics.json            # all evaluation metrics
│
├── data/                       # raw simulated data (may not be committed if large)
│
└── tests/
    ├── test_simulator.py
    ├── test_featurize.py
    ├── test_no_leakage.py
    ├── test_soft_sensor.py
    ├── test_scorer_parity.py
    ├── test_serve_imports.py   # enforces the lightweight-only allowlist
    └── test_api.py
```

---

## 3. DESIGN PRINCIPLES — HARD RULES

These override any shortcut. Violating any of these is a build failure.

1. **Config-driven.** Station count, topology, zones, buffers, sensor tiers, variant mix, shift schedule, defect physics parameters — all in YAML. Swapping `plant_a.yaml` for `plant_b.yaml` must work end-to-end with zero code changes. This is a demo moment.

2. **Honest difficulty.** The simulator's causal model must include at least two unobservable causes. If the defect model achieves PR-AUC > 0.92, go back and make the world harder (more noise, more confounders). A 0.82 with honest validation beats a 0.99 that a judge can debunk in one question.

3. **Provenance everywhere.** Every inferred value is tagged `provenance: "inferred"` from the database through the API response to the UI badge. Every measured value is tagged `provenance: "measured"`. The UI renders them differently. This is non-negotiable.

4. **Every dollar is sourced.** No ROI figure appears anywhere — API, UI, or docs — without a `source` field pointing to `economics.yaml` or a stated assumption in `ASSUMPTIONS.md`.

5. **Read-only architecture.** The twin never writes to any production system. The architecture diagram shows a passive tap. Document this explicitly in `ARCHITECTURE.md`.

6. **Precompute heavy, serve thin.** Everything the API needs is baked into `artifacts/pulsetwin.db` and `model.npz` at build time. The runtime image has no pandas, no sklearn, no xgboost, no shap, no simpy.

7. **Deterministic demo.** Fixed seed `42` everywhere. The committed artifacts reproduce byte-for-byte. `make demo` starts from committed artifacts with no generation and no training.

8. **No placeholder code.** No `# TODO`, no `pass` in a function body, no `...` as implementation, no `console.log("placeholder")`. Every file runs. Every component renders real data.

9. **Operator privacy.** Never surface individual operator performance. Aggregate by shift or team only. Explain why in the business proposal (union acceptance, ethical data use).

10. **The import wall.** `serve/` may import ONLY: `fastapi`, `uvicorn`, `pydantic`, `numpy`, `orjson`, `sqlite3`, `json`, `math`, `pathlib`, `os`, `time`, `asyncio`, `typing`, `datetime`, `contextlib`, `dataclasses`. Nothing else. Enforce with `tests/test_serve_imports.py`.

---

## 4. PHASE-BY-PHASE BUILD

### PHASE 1: Configs & Economics

**`configs/plant_a.yaml`:**
```yaml
plant:
  id: plant_a
  name: "Midwest Assembly Plant"
  units_per_hour: 55
  
zones:
  - id: body
    name: "Body Construction"
    stations: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    zone_sensors: [weld_current_a, torque_nm, vibration_mm_s]
  - id: paint  
    name: "Paint Shop"
    stations: [13, 14, 15, 16, 17, 18]
    zone_sensors: [humidity_pct, temperature_c, film_thickness_um]
  - id: final
    name: "Final Assembly"
    stations: [19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30]
    zone_sensors: [torque_nm, vibration_mm_s]

stations:
  # For EACH of the 30 stations, define:
  # id, name, zone, nominal_cycle_s, cycle_cv, buffer_capacity_after,
  # sensor_tier (full|partial|manual), sensors list, mtbf_hours, repair_time_s,
  # variant_multipliers (sedan, suv, ev)
  # 
  # KEY STATIONS:
  # S3:  "Structural Fastening A" — body, full sensors, torque is a defect contributor
  # S8:  "Vibration-Critical Weld" — body, full sensors, vibration is a defect contributor  
  # S10: "Legacy Bracket Mount" — body, MANUAL tier, no continuous sensors
  # S12: "Body Completion" — body, full sensors, normal-day bottleneck
  # S15: "Paint Surface Prep" — paint, MANUAL tier, operator rush = Defect Mode B
  # S17: "Clear Coat Application" — paint, full sensors
  # S19: "Battery/Drivetrain Mount" — final, full sensors, EV bottleneck (ev_multiplier: 1.4)
  # S22: "Legacy Interior Fit" — final, MANUAL tier
  # S27: "Legacy Electrical Check" — final, MANUAL tier  
  # S30: "Final Quality Gate" — final, inspection station
  # ... define all 30 with realistic names, cycle times (45-90s), and parameters

variants:
  - id: sedan
    name: "Sedan"
    base_mix_pct: 50
    tuesday_mix_pct: 30
  - id: suv
    name: "SUV"  
    base_mix_pct: 35
    tuesday_mix_pct: 30
  - id: ev
    name: "EV"
    base_mix_pct: 15
    tuesday_mix_pct: 40    # THE TUESDAY TRIGGER

shifts:
  - id: shift_a
    name: "Morning"
    start: "06:00"
    end: "14:00"
    operator_count: 12
    skill_distribution: {expert: 0.3, standard: 0.5, junior: 0.2}
  - id: shift_b
    name: "Afternoon"  
    start: "14:00"
    end: "22:00"
    operator_count: 12
    skill_distribution: {expert: 0.2, standard: 0.5, junior: 0.3}
  - id: shift_c
    name: "Night"
    start: "22:00"
    end: "06:00"
    operator_count: 10
    skill_distribution: {expert: 0.15, standard: 0.5, junior: 0.35}

ambient:
  base_temperature_c: 24
  daily_amplitude_c: 6
  base_humidity_pct: 45
  humidity_high_days: [1, 2]  # Tuesday, Wednesday (0=Monday)
  humidity_high_add_pct: 20

defect_physics:
  mode_a:  # Paint dwell + humidity
    name: "Paint Micro-Blistering"
    contributing_stations: [15, 16, 17]
    dwell_threshold_s: 180        # buffer dwell above this triggers risk
    humidity_threshold_pct: 55
    paint_batch_weak_fraction: 0.3 # 30% of paint batches are from weaker supplier
    weight: 0.45
  mode_b:  # Operator rush at manual station
    name: "Surface Prep Adhesion Failure"
    contributing_station: 15
    checklist_rush_threshold_s: 25 # normal is ~45s, rush is <25s
    weight: 0.35
  mode_c:  # Upstream torque + vibration interaction
    name: "Structural Fastening Defect"
    contributing_stations: [3, 8]
    torque_deficit_threshold_pct: 8
    vibration_excess_percentile: 92
    interaction_required: true  # both must co-occur
    weight: 0.20
  base_defect_rate: 0.03
  label_noise_rate: 0.03
  inspection_detection_rate: 0.92
  inspection_false_reject_rate: 0.02

simulation:
  duration_days: 14
  seed: 42
  lot_change_interval_units: 200
```

**Fill in all 30 stations** with realistic parameters. Make them distinct — different cycle times (45–90s), different buffer sizes (2–8), different MTBFs (200–2000 hours). The EV multiplier only affects S19–S24 (1.3–1.5×).

**`configs/plant_b.yaml`:** 42 stations, 8 manual-tier, zones split differently (body S1–S16, paint S17–S24, final S25–S42), higher buffers, different variant mix (no EV, but a TRUCK variant with different bottleneck characteristics). Same schema, different values.

**`configs/economics.yaml`:**
```yaml
costs:
  scrap_cost_per_unit:
    value: 4800
    currency: USD
    source: "Industry average for mixed-model automotive, IHS Markit 2024"
  rework_cost_per_unit:
    value: 1200
    currency: USD
    source: "Plant engineering estimate, typical body/paint rework"
  warranty_escape_cost_per_unit:
    value: 8500
    currency: USD
    source: "NHTSA recall cost data, averaged across powertrain/body/paint"
  downtime_cost_per_hour:
    value: 5000
    currency: USD
    source: "Industry benchmark, 55 units/hr × ~$90 marginal contribution"
  supervisor_response_cost_per_alarm:
    value: 45
    currency: USD
    source: "15 min response × loaded labor rate"

production:
  units_per_hour: 55
  hours_per_shift: 8
  shifts_per_day: 3
  operating_days_per_year: 250
  source: "Standard automotive 3-shift, 5-day operation"

instrumentation:
  sensor_costs:
    torque_sensor:
      value: 2800
      source: "Atlas Copco QST series, installed"
    vibration_sensor:
      value: 1800
      source: "SKF CMSS 2200, installed with gateway"
    temperature_humidity_logger:
      value: 1200
      source: "Vaisala HMT120, installed"
    film_thickness_gauge:
      value: 4500
      source: "ElektroPhysik MiniTest inline, installed"
  maintenance_windows_per_year: 3
  hours_per_window: 16
  
alarm_budget:
  max_alerts_per_shift: 6
  source: "Human factors research: operator alert response degrades beyond 6-8 per shift (Wickens, 2008)"
```

**Acceptance:** Write a Pydantic schema that validates both plant configs and the economics config. `pytest tests/test_simulator.py::test_configs_valid` passes.

---

### PHASE 2: The Factory Simulator

This is the foundation. If it's weak, everything above it is theatre.

**`offline/simulator/line_model.py` — Discrete-event simulation**

Do NOT use SimPy (it won't be in the serve image, but keeping the offline deps simple is good too). Write a lightweight tick-based simulation:

- Each tick = 1 second of simulated time.
- Each station has a state: `idle | working | blocked | starved | down`.
- Each station has a finite output buffer (`buffer_capacity_after` from config).
- A station finishes work → tries to place unit in its output buffer. If buffer is full → station enters `blocked` state. Unit stays.
- A station is idle → tries to pull from the upstream buffer. If empty → `starved`.
- Stochastic breakdowns: exponential inter-arrival from MTBF, repair time from a gamma distribution.
- **Tool wear drift:** each station's effective cycle time creeps up 0.1–0.5% per hour of operation. After a breakdown+repair, it resets. This makes the bottleneck **shift** over the simulation period — critical for the bottleneck demo.
- Record per-station-per-second state. Record per-unit genealogy (entry/exit time at each station).

**`offline/simulator/physics.py` — Correlated signal generation**

Signals must have temporal autocorrelation and cross-correlation. DO NOT draw independent random values per row.

- `torque_nm`: target value per station (from config) ± tolerance. Apply a **slow drift** (random walk, mean-reverting over ~500 units) representing tool wear. Add operator-dependent bias (±2–5% based on operator skill level). Add measurement noise (σ = 0.5% of target).
- `vibration_mm_s`: baseline per station. Increases with bearing wear (slow drift over hours). Spikes when cycle time is compressed (correlation with blocking→release bursts). EWMA-filtered, not i.i.d.
- `temperature_c`: thermal inertia model — `T(t) = α·T(t-1) + (1-α)·(T_ambient + heat_from_work)`. Lagged, never independent.
- `weld_current_a` (body zone): correlated with material thickness variation (by lot).
- `humidity_pct`, `film_thickness_um` (paint zone): humidity from ambient profile + zone HVAC noise. Film thickness as a function of spray parameters + humidity interaction.
- All signals for manual-tier stations: **output NaN for continuous sensors**. Instead, emit a checklist record.

**`offline/simulator/operators.py` — Human behaviour**

- Each shift has an operator pool with skill levels (expert/standard/junior).
- Operators rotate between stations within their zone.
- **Rush behaviour at Station 15:** When Station 15 has been blocked for > 60 seconds and then clears, the operator's checklist time compresses from ~45s to ~20–25s for the next 3–5 units. This is Defect Mode B's trigger.
- Operator skill affects torque bias and checklist thoroughness.

**`offline/simulator/causal_engine.py` — The multi-causal defect model**

For each unit, compute a latent `defect_propensity` score:

```python
propensity = (
    w_a * mode_a_signal(dwell_in_paint_buffer, humidity, paint_batch_quality) +
    w_b * mode_b_signal(s15_checklist_time, operator_skill) +
    w_c * mode_c_signal(s3_torque_deficit, s8_vibration_excess) +  # interaction term
    w_lot * incoming_lot_quality +  # UNOBSERVABLE — varies by lot, never in sensor data
    w_noise * random_noise
)
defect = logistic(propensity) > uniform(0, 1)
```

Key design choices:
- `paint_batch_quality` is **unobservable** — the model can never see it directly. It's a latent variable that changes every ~200 units.
- `incoming_lot_quality` is **unobservable** — varies by lot, creates noise the model can't explain.
- Mode A only fires when humidity > 55% AND dwell > threshold AND paint batch is weak. All three must co-occur. ~30% of the time.
- Mode C requires BOTH torque deficit at S3 AND vibration excess at S8. Either alone is insufficient.
- Tune weights so realised defect rate is **2.5–4%**.
- Add **label noise at 3%** (flip some labels randomly) simulating inspection imperfection.

**`offline/simulator/inspection.py`**

- Final inspection at S30: detects 92% of true defects, false-rejects 2% of good units.
- The `defect_detected` column at S30 is what the model trains on — it's the noisy label.
- Store the `true_defect` separately for offline validation (never visible to the model).
- Optional: one in-line quality gate at S18 (end of paint) that catches ~40% of Mode A defects. This creates a partial early signal.

**`offline/simulator/generate.py` — CLI entrypoint**

```
python -m offline.simulator.generate --config configs/plant_a.yaml --seed 42
```

Outputs:
- `data/telemetry.parquet` — one row per unit per station: `timestamp, unit_id, station_id, cycle_time_s, torque_nm, vibration_mm_s, temperature_c, weld_current_a, humidity_pct, film_thickness_um, station_state`
- `data/genealogy.parquet` — `unit_id, station_id, entry_ts, exit_ts, variant, lot_id, operator_id, shift_id`
- `data/checklists.parquet` — `unit_id, station_id, timestamp, operator_id, checklist_pass, checklist_time_s, notes_code`
- `data/inspections.parquet` — `unit_id, timestamp, defect_detected, defect_mode (null if none), true_defect (hidden truth)`

**Acceptance:**
- Generates 14 days of data in < 5 minutes.
- Printed diagnostics:
  - Total units: ~18,000–22,000
  - Defect rate: 2.5–4.0%
  - Stations 10, 15, 22, 27 have 100% NaN for continuous sensors but populated checklists
  - At least one bottleneck shift visible (S12 → S19 on high-EV days)
  - Signal autocorrelation > 0.3 for torque, vibration, temperature
  - Non-zero blocked AND starved time across multiple stations
  - Two distinct defect modes visible in the `defect_mode` column
- `pytest tests/test_simulator.py` passes (determinism, autocorrelation, defect rate, data shape).

---

### PHASE 3: Feature Engineering & Leakage Discipline

**`offline/ml/featurize.py`**

Transform station-level time series into **one row per unit**, using ONLY data available at or before Station 20.

**Features per instrumented station (for each signal):**
- Raw last value at that station
- Deviation from station's trailing median (window = 50 units of same variant)
- Z-score vs the station's trailing distribution (window = 200 units)
- Rate of change vs previous 3 units at same station
- Rolling 10-unit standard deviation (signal stability)

**Sequence / flow features:**
- Total dwell time S1→S20
- Count of stations where unit was blocked (from genealogy)
- Total blocked time
- Max single-station wait time
- Time-in-paint-buffer (S14 exit → S16 entry) — KEY feature for Mode A
- Variant one-hot encoding
- Shift at each zone entry
- Ambient temperature and humidity at body zone entry and paint zone entry

**Checklist features (manual stations only):**
- `checklist_pass` (binary)
- `checklist_time_s` (how long the operator took — KEY feature for Mode B)
- `notes_code` one-hot encoded
- Operator ID hashed to 8 buckets (anonymised, not individual tracking)

**Lot/batch features (carefully!):**
- `lot_trailing_defect_rate`: rolling defect rate of the last 100 units from the same lot, computed using ONLY past inspection results. This is legitimate because it's computable at inference time. Document this explicitly.

**Leakage rules — enforce with tests:**
- No feature derives from any station > 20
- No feature uses the inspection outcome of the current unit
- No feature uses any future timestamp
- Train: days 1–9. Validation: days 10–11. Test: days 12–14. **Temporal split only. Never random.**
- `tests/test_no_leakage.py` scans column names for station IDs > 20 and asserts temporal ordering of splits.

---

### PHASE 4: Soft Sensors (Virtual Metrology)

**`offline/ml/soft_sensor.py`**

For each manual-tier station (S10, S15, S22, S27), train a per-signal regressor that estimates what the missing continuous readings *would have been*:

**Input features:**
- Immediately adjacent instrumented stations' readings (for the same unit)
- Unit variant
- Ambient conditions at that zone
- Dwell time at the station (from genealogy — this IS observable)
- Checklist record (time, pass/fail, notes code)
- Operator bucket (anonymised)

**Output:** predicted torque, vibration, temperature (whichever signals that station's zone normally has).

**Model:** LightGBM or sklearn HistGradientBoosting (quantile regression for prediction intervals).

**The credibility trick — masked-station validation:**
1. Take Station 9 (fully instrumented, body zone) and Station 21 (fully instrumented, final zone).
2. Mask their sensors — pretend they're manual-tier.
3. Run the soft sensor using only adjacent stations + checklist proxies.
4. Report R² and MAE against the withheld true readings.
5. Put these numbers in `MODEL_CARD.md` prominently.
6. **This is one of your strongest demo moments.** "We don't just claim the soft sensor works. We prove it by hiding data we have and checking."

**Ablation:**
- Train the defect model WITH soft-sensed features vs WITHOUT (dropping legacy stations entirely).
- Report the delta. If soft sensors don't help, say so honestly. An honest negative result beats a fabricated positive.

Every soft-sensed value carries:
- `provenance: "inferred"`
- `ci_low`, `ci_high` (prediction interval from quantile regression)
- These propagate all the way to the API and UI.

---

### PHASE 5: Predictive Engine

**5.1 Defect Risk Model (`offline/ml/defect_model.py`)**

- Algorithm: `HistGradientBoostingClassifier` (sklearn) or XGBoost. Handle class imbalance via `scale_pos_weight` — NOT by resampling (resampling + temporal features = leakage).
- Train on days 1–9, validate on days 10–11, test on days 12–14.
- **Calibrate** with isotonic regression on validation fold. Produce a reliability diagram.
- **SHAP values** for every unit in the test set: compute and store the top-5 contributing features per unit, with plain-language descriptions.

**Metrics to compute and store:**
- PR-AUC (primary — on a 3% base rate, this is the only metric that matters)
- Precision at alarm budget (6 alerts per shift → ~N alerts per day → pick threshold)
- Recall at that threshold (what fraction of real defects are caught)
- Lead time: median minutes between alert and when the unit reaches S30
- Units-in-flight at alert time: how many units are between the implicated station and S30
- Calibration: reliability diagram binned into 10 deciles
- ROC-AUC (secondary, for completeness)

**Target ceiling: PR-AUC 0.75–0.88.** If higher, the simulator is too easy. If lower, check for bugs.

**SHAP explanations in floor language:**
For each prediction, store the top-5 SHAP contributors translated to plain English:
```json
{
  "feature": "paint_buffer_dwell_s",
  "direction": "high",
  "contribution": 0.23,
  "plain_language": "Unit spent 195s in paint buffer (normal: 120s) — extended dwell risks cure defects"
}
```

**5.2 Bottleneck Detection (`offline/ml/bottleneck.py`)**

- **Current bottleneck identification:** Use the active-period method — the station with the longest uninterrupted `working` state in a sliding window is the constraint. Everything else is either blocked or starved relative to it.
- **Ground-truth validation:** The simulator knows the true constraint (the station that's rate-limiting throughput). Report detection accuracy.
- **15-minute forecast:** For each station, predict cycle time and blocked-probability 15 minutes ahead using lag + rolling features. Baseline against persistence (naïve last-value). Report skill score = 1 - (MAE_model / MAE_baseline). If you don't beat the baseline, say so.
- **Shifting bottleneck detection:** Detect WHEN the bottleneck moves (e.g., from S12 to S19 during EV-heavy production). Log these transition events with timestamps.

**5.3 Thresholds & Trust (`offline/ml/thresholds.py`)**

- Compute cost-optimal threshold from economics.yaml:
  ```
  E[cost_of_miss] = P(defect) × (scrap_cost + rework_cost + warranty_escape_cost × escape_rate)
  E[cost_of_alarm] = (1 - P(defect)) × supervisor_response_cost
  Optimal threshold: where E[cost_of_miss] = E[cost_of_alarm]
  ```
- Compute the **alarm budget curve**: sweep thresholds, plot alerts-per-shift vs precision vs recall. Mark the recommended operating point.
- Store the curve data for the Leadership view.

---

### PHASE 6: Build Artifact (The Precompute Step)

**`offline/build_artifact.py`**

This is the bridge between the heavy offline world and the lightweight serving layer. Run the full pipeline and bake EVERYTHING into `artifacts/pulsetwin.db` (SQLite) and JSONL files.

**SQLite tables** (index every column the API will filter on):

```sql
-- Per-unit predictions and outcomes
CREATE TABLE units (
    unit_id TEXT PRIMARY KEY,
    variant TEXT,
    lot_id TEXT,
    shift_id TEXT,
    start_ts TEXT,
    end_ts TEXT,
    risk_score REAL,         -- raw model output
    calibrated_risk REAL,    -- after isotonic calibration
    ci_low REAL,
    ci_high REAL,
    alert_fired INTEGER,     -- 1 if above cost-optimal threshold
    predicted_defect INTEGER,
    actual_defect INTEGER,   -- inspection result (noisy)
    true_defect INTEGER,     -- simulator truth (for offline validation only)
    defect_mode TEXT,        -- mode_a, mode_b, mode_c, or null
    lead_time_min REAL,      -- minutes between alert station and S30
    plant_id TEXT
);

-- SHAP explanations per unit
CREATE TABLE unit_factors (
    unit_id TEXT,
    rank INTEGER,
    feature TEXT,
    direction TEXT,          -- "high" or "low"
    contribution REAL,
    plain_language TEXT,
    PRIMARY KEY (unit_id, rank)
);

-- Unit journey through the line
CREATE TABLE genealogy (
    unit_id TEXT,
    station_id INTEGER,
    entry_ts TEXT,
    exit_ts TEXT,
    operator_id TEXT,
    station_state TEXT,      -- working/blocked/starved
    variant TEXT,
    lot_id TEXT,
    plant_id TEXT,
    PRIMARY KEY (unit_id, station_id)
);

-- Sensor readings (measured + soft-sensed)
CREATE TABLE station_readings (
    unit_id TEXT,
    station_id INTEGER,
    signal TEXT,
    value REAL,
    provenance TEXT,         -- "measured" or "inferred"
    ci_low REAL,             -- null if measured
    ci_high REAL,            -- null if measured
    PRIMARY KEY (unit_id, station_id, signal)
);

-- Bottleneck analysis windows
CREATE TABLE bottleneck_windows (
    window_start TEXT,
    window_end TEXT,
    bottleneck_station INTEGER,
    confidence REAL,
    ground_truth_station INTEGER,
    correct INTEGER,
    shift_event INTEGER,     -- 1 if bottleneck changed from previous window
    plant_id TEXT
);

-- Station-level rollups per hour
CREATE TABLE station_stats (
    station_id INTEGER,
    hour_start TEXT,
    utilisation REAL,
    blocked_pct REAL,
    starved_pct REAL,
    mean_cycle_s REAL,
    throughput_uph REAL,
    lost_throughput_uph REAL,
    provenance TEXT,         -- "measured" or "inferred" for the station
    plant_id TEXT,
    PRIMARY KEY (station_id, hour_start, plant_id)
);

-- What-if precomputed scenario grid
CREATE TABLE whatif_grid (
    station_id INTEGER,
    buffer_delta INTEGER,    -- -4 to +8
    cycle_delta_pct REAL,    -- -20% to +20%
    throughput_uph REAL,
    wip_units REAL,
    new_bottleneck_station INTEGER,
    plant_id TEXT
);

-- Instrumentation planner
CREATE TABLE instrumentation_plan (
    station_id INTEGER,
    station_name TEXT,
    sensor_type TEXT,
    cost REAL,
    delta_pr_auc REAL,       -- marginal model improvement
    annual_value REAL,
    payback_months REAL,
    window_assignment TEXT,   -- "Window 1 (March)", etc.
    plant_id TEXT
);

-- Economics (key-value with sources)
CREATE TABLE economics (
    key TEXT PRIMARY KEY,
    value REAL,
    unit TEXT,
    source TEXT,
    plant_id TEXT
);

-- Model metrics
CREATE TABLE metrics (
    key TEXT,
    value REAL,
    context TEXT,            -- "test_set", "validation_set", etc.
    plant_id TEXT
);

-- Checklist records for manual stations
CREATE TABLE checklists (
    unit_id TEXT,
    station_id INTEGER,
    timestamp TEXT,
    operator_id TEXT,
    checklist_pass INTEGER,
    checklist_time_s REAL,
    notes_code TEXT,
    plant_id TEXT,
    PRIMARY KEY (unit_id, station_id)
);

-- Station config (for the frontend to know the line layout)
CREATE TABLE station_config (
    station_id INTEGER,
    station_name TEXT,
    zone TEXT,
    sensor_tier TEXT,
    nominal_cycle_s REAL,
    buffer_capacity INTEGER,
    sensors TEXT,             -- JSON array of sensor names
    plant_id TEXT,
    PRIMARY KEY (station_id, plant_id)
);
```

**Stream JSONL files:**
`artifacts/stream_plant_a.jsonl` — one JSON object per line, one per unit-per-station event, in timestamp order:
```json
{"ts": "2025-01-07T06:12:34", "unit_id": "U04412", "station_id": 3, "variant": "sedan", "cycle_time_s": 62.3, "torque_nm": 41.8, "vibration_mm_s": 2.1, "temperature_c": 25.4, "station_state": "working", "risk_score": 0.12, "calibrated_risk": 0.09, "alert": false, "provenance": {"torque_nm": "measured", "vibration_mm_s": "measured"}}
```

For units passing manual stations, continuous signals are null but checklist data is present. Risk score updates as the unit progresses.

Keep the stream files to ~48 hours of data (enough for a compelling replay). Full 14-day metrics are precomputed in the DB.

**`offline/export_model.py` — Model to numpy**

1. Extract the trained ensemble's tree structure into flat numpy arrays: `feature_indices, thresholds, left_children, right_children, leaf_values, tree_offsets`.
2. Extract the isotonic calibration step function as two arrays: `calibration_x, calibration_y`.
3. Save as `artifacts/model.npz`.
4. Write `serve/scorer.py`: a pure-numpy tree-ensemble scorer (~80 lines). Vectorised traversal, then calibration lookup.
5. `tests/test_scorer_parity.py`: score the entire test set with both the sklearn model and the numpy scorer. Assert max absolute difference < 1e-6.

**Run build for BOTH plants:**
```
python -m offline.build_artifact --config configs/plant_a.yaml
python -m offline.build_artifact --config configs/plant_b.yaml
```

Both plants' data goes into the same SQLite DB, discriminated by `plant_id`.

**Acceptance:**
- `artifacts/pulsetwin.db` exists and is < 50 MB
- `artifacts/model.npz` exists and is < 5 MB
- Both stream JSONL files exist
- `tests/test_scorer_parity.py` passes
- `artifacts/metrics.json` has all metrics

---

### PHASE 7: FastAPI Backend (Serve Layer)

**CRITICAL: This entire directory uses ONLY the approved import list. No pandas, no sklearn, no xgboost, no shap.**

**`serve/main.py`**
```python
# FastAPI app with:
# - CORS middleware (allow all origins for demo)
# - Lifespan handler that opens SQLite connection + loads model.npz
# - Mounts all routers
# - Prints RSS on startup
# - GET /api/health → {status, rss_mb, plant_id, artifact_built_at, model_version, station_count}
```

**`serve/db.py`** — thin sqlite3 wrapper: `query_one`, `query_all`, `query_df` (returns list of dicts, not a DataFrame).

**`serve/scorer.py`** — pure numpy tree scorer + calibration. Already described. ~80 lines.

**`serve/schemas.py`** — Pydantic v2 models for every request/response. Be thorough — typed responses make the frontend dev trivial.

**Router endpoints:**

**`GET /api/config/stations?plant_id=plant_a`**
Returns the station layout for the frontend to render the line map:
```json
{
  "stations": [
    {"id": 1, "name": "Frame Load", "zone": "body", "sensor_tier": "full", "nominal_cycle_s": 55, "buffer_capacity": 4, "sensors": ["torque_nm", "vibration_mm_s"]},
    ...
  ],
  "zones": [{"id": "body", "name": "Body Construction", "stations": [1,2,...,12]}, ...]
}
```

**`GET /api/stream?plant_id=plant_a&speed=10&from_ts=...`**
Server-Sent Events. Opens the JSONL file, seeks to `from_ts` (using a prebuilt offset index in the DB), and yields lines with `time.sleep(interval / speed)`. Each SSE event:
```
event: station_update
data: {"ts": "...", "unit_id": "U04412", "station_id": 3, ...}

event: alert
data: {"unit_id": "U04412", "risk": 0.84, "factors": [...], "lead_time_min": 22, "containment_count": 7}
```

**`POST /api/predict/defect`**
Accepts a partial unit feature vector (for ad-hoc "what would happen" queries). Returns:
```json
{
  "probability": 0.84,
  "calibrated": true,
  "confidence_interval": [0.71, 0.93],
  "provenance": {"measured": ["s3_torque_nm", "s8_vibration_mm_s"], "inferred": ["s15_temperature_c"]},
  "explanation_mode": "approximate",
  "top_factors": [
    {"feature": "paint_buffer_dwell_s", "direction": "high", "contribution": 0.23, "plain_language": "Unit spent 195s in paint buffer (normal: 120s)"},
    {"feature": "s15_checklist_time_s", "direction": "low", "contribution": 0.18, "plain_language": "Station 15 prep completed in 22s (normal: 45s)"},
    {"feature": "s8_vibration_mm_s", "direction": "high", "contribution": 0.15, "plain_language": "Vibration at S8 in 96th percentile for SUV variant"}
  ],
  "recommended_action": "Hold for inspection. Check paint adhesion and structural fastening.",
  "lead_time_minutes": 22
}
```

**`GET /api/trace/containment/{unit_id}?plant_id=plant_a`**
THE CONTAINMENT ENDPOINT. Given a flagged unit, find every other in-flight unit sharing the causal fingerprint:
```json
{
  "flagged_unit": "U04412",
  "flagged_risk": 0.84,
  "flagged_factors": ["paint_buffer_dwell", "s15_checklist_rush"],
  "at_risk_units": [
    {"unit_id": "U04408", "current_station": 22, "shared_factors": ["same_lot", "same_paint_buffer_window"], "risk": 0.71, "action": "HOLD"},
    {"unit_id": "U04409", "current_station": 19, "shared_factors": ["same_lot", "same_operator_s15"], "risk": 0.65, "action": "INSPECT"},
    ...
  ],
  "containment_window_min": 18,
  "total_at_risk": 7
}
```
Implementation: SQL join on genealogy (same lot_id, same operator at implicated stations, overlapping time window) + filter by risk above a lower threshold.

**`GET /api/bottleneck/current?plant_id=plant_a`**
```json
{
  "current_bottleneck": {"station_id": 19, "station_name": "Battery Mount", "confidence": 0.91, "since": "14:23:00", "cause": "EV variant cycle time"},
  "previous_bottleneck": {"station_id": 12, "shifted_at": "14:23:00"},
  "stations_blocked": [15, 16, 17, 18],
  "stations_starved": [25, 26, 27]
}
```

**`GET /api/bottleneck/history?plant_id=plant_a&hours=24`**
Returns the bottleneck timeline for the chart — array of `{window_start, window_end, station_id, confidence, ground_truth_station, correct}`.

**`POST /api/whatif`**
```json
// Request:
{"station_id": 17, "buffer_delta": 4, "cycle_delta_pct": 0, "plant_id": "plant_a"}

// Response:
{"mode": "interpolated", "grid_resolution": {"buffer_step": 2, "cycle_step": 5},
 "baseline": {"throughput_uph": 52.1, "wip": 45, "bottleneck": 19},
 "projected": {"throughput_uph": 53.7, "wip": 41, "bottleneck": 19},
 "delta": {"throughput_uph": 1.6, "wip": -4},
 "annual_value": 184000, "assumptions": "Based on 250 operating days, $90 marginal contribution per unit"}
```

**`GET /api/economics/roi?plant_id=plant_a`**
```json
{
  "annualised": {
    "scrap_avoided": {"value": 1250000, "units_caught": 260, "source": "..."},
    "rework_avoided": {"value": 312000, "units_caught": 260, "source": "..."},
    "downtime_avoided": {"value": 480000, "hours": 96, "source": "..."},
    "warranty_escapes_prevented": {"value": 680000, "units": 80, "source": "..."},
    "alarm_response_cost": {"value": -24300, "alarms": 540, "source": "..."},
    "net_value": {"value": 2697700},
    "system_cost": {"value": 350000, "source": "Implementation + first year operation estimate"},
    "roi_pct": 670,
    "payback_months": 1.6
  },
  "sensitivity": {
    "pessimistic": {"net_value": 1350000, "assumptions": "50% of base catch rate"},
    "base": {"net_value": 2697700},
    "optimistic": {"net_value": 3850000, "assumptions": "Include warranty reduction at full detection"}
  }
}
```

**`GET /api/economics/instrumentation-plan?plant_id=plant_a`**
```json
{
  "plan": [
    {"station_id": 15, "station_name": "Paint Surface Prep", "sensor": "temperature_humidity_logger", "cost": 1200, "delta_pr_auc": 0.04, "annual_value": 138000, "payback_months": 0.1, "window": "Window 1 (March)"},
    {"station_id": 22, "station_name": "Legacy Interior Fit", "sensor": "torque_sensor", "cost": 2800, "delta_pr_auc": 0.02, "annual_value": 67000, "payback_months": 0.5, "window": "Window 1 (March)"},
    {"station_id": 10, "station_name": "Legacy Bracket Mount", "sensor": "vibration_sensor", "cost": 1800, "delta_pr_auc": 0.01, "annual_value": 31000, "payback_months": 0.7, "window": "Window 2 (July)"},
    {"station_id": 27, "station_name": "Legacy Electrical Check", "sensor": "torque_sensor", "cost": 2800, "delta_pr_auc": 0.008, "annual_value": 22000, "payback_months": 1.5, "window": "Window 2 (July)"}
  ],
  "total_investment": 8600,
  "total_annual_value": 258000,
  "maintenance_windows": ["March 2026", "July 2026", "November 2026"]
}
```

**`POST /api/feedback`**
```json
{"unit_id": "U04412", "alert_id": "A00123", "verdict": "useful"}
// or "false_alarm" or "missed"
```
Stores in a local SQLite table. Non-persistent on free tier — document the production path.

**`GET /api/metrics?plant_id=plant_a`**
Returns everything in the metrics table: PR-AUC, precision@budget, recall, lead time, soft sensor MAE, bottleneck detection accuracy, forecast skill, calibration bins.

**Acceptance:**
- `tests/test_serve_imports.py` passes (no banned imports)
- `tests/test_api.py` hits every endpoint against committed artifacts and asserts correct schemas
- All endpoints respond in < 300ms (except whatif < 1s)
- `GET /api/health` shows RSS < 200 MB

---

### PHASE 8: Next.js Dashboard

**Stack:** Next.js 14+ App Router, TypeScript strict mode, Tailwind CSS, Recharts for charts. Static export (`output: 'export'`).

**Design language:** Dark theme, control-room aesthetic. Dense, information-rich. Think Bloomberg Terminal meets factory control room, not SaaS landing page. Monospace for numbers. Subtle grid lines. Muted backgrounds with bright signal colours:
- `--bg-primary: #0a0e17` (near-black navy)
- `--bg-card: #111827` (dark card)
- `--bg-surface: #1f2937` (elevated surface)
- `--text-primary: #e5e7eb` (light gray)
- `--text-muted: #6b7280`
- `--accent-green: #10b981` (healthy/measured)
- `--accent-red: #ef4444` (alert/defect)
- `--accent-amber: #f59e0b` (warning/inferred)
- `--accent-blue: #3b82f6` (info/selection)
- `--accent-purple: #8b5cf6` (prediction/AI)

Use a monospace font (JetBrains Mono or similar from Google Fonts) for all numbers and data values.

---

#### Landing Page (`app/page.tsx`)

Full-screen dark background with the PulseTwin logo (styled text, no image needed). Three persona cards:

```
┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│   👷 SUPERVISOR     │  │   📊 MANAGER        │  │   💰 LEADERSHIP     │
│                     │  │                     │  │                     │
│  Real-time line     │  │  Trends, root       │  │  ROI, investment    │
│  monitoring &       │  │  cause analysis &   │  │  planning &         │
│  defect alerts      │  │  what-if planning   │  │  rollout strategy   │
│                     │  │                     │  │                     │
│  [Enter →]          │  │  [Enter →]          │  │  [Enter →]          │
└─────────────────────┘  └─────────────────────┘  └─────────────────────┘
```

Below the cards: a single line showing connection status, current plant config, and data time range.

---

#### Supervisor View (`app/supervisor/page.tsx`) — ACTION-ORIENTED

This is the most important view. It must look like a real control room screen.

**Layout: Two-column. Left 65% = Line Map + Unit Detail. Right 35% = Alert Feed.**

**Top Bar:**
- Plant name + config indicator
- PlantSelector dropdown (switches between plant_a and plant_b)
- Current shift indicator (Morning / Afternoon / Night)
- Replay speed control (1×, 5×, 10×, 50×)
- SSE connection status (green dot = connected, red = disconnected with reconnect countdown)
- **Alert Budget Counter:** "Alerts: 4 / 6 this shift" with a visual meter bar. Green when under budget, amber at 5/6, red at 6/6.
- Clock showing simulated time

**Line Map (`components/supervisor/LineMap.tsx`):**

Visual representation of the 30 stations, grouped by zone:

```
BODY CONSTRUCTION                    PAINT SHOP                FINAL ASSEMBLY
┌──┐ ┌──┐ ┌──┐ ┌──┐ ┌──┐ ┌──┐      ┌──┐ ┌──┐ ┌──┐ ┌──┐     ┌──┐ ┌──┐ ┌──┐ ...
│S1│→│S2│→│S3│→│S4│→│S5│→│S6│→ ... →│S13│→│S14│→│S15│→│S16│→...→│S19│→│S20│→│S21│→ ...
└──┘ └──┘ └──┘ └──┘ └──┘ └──┘      └──┘ └──┘ └──┘ └──┘     └──┘ └──┘ └──┘
```

Each **StationTile** shows:
- Station number and short name
- Current state colour: green (working), amber (starved), red (blocked), dark red (down), gray (idle)
- Buffer fill indicator (small bar below showing buffer fullness as %)
- For manual-tier stations: diagonal hatch pattern overlay + amber "INFERRED" badge
- Current unit ID being processed
- Cycle time vs nominal (small deviation indicator)
- Click to expand → shows last 10 readings, current unit details, soft-sensor confidence

Between zone groups, show a small zone label and a visual buffer indicator.

**Animated flow:** When a unit moves from one station to the next, briefly flash the transition. Show units "flowing" left to right.

**Unit Tracker (`components/supervisor/UnitTracker.tsx`):**
Below the line map. When a station is clicked or a unit is selected from an alert:
- Show the unit's full journey so far (which stations it's visited, time at each, readings)
- Highlight any anomalous readings with red text
- Show current risk score with a gauge visualisation
- Show provenance for each reading ("MEASURED" green badge or "INFERRED" amber badge with CI)

**Alert Feed (`components/supervisor/AlertFeed.tsx`):**
Right column. Scrolling feed of alert cards, newest on top.

Each **AlertCard** shows:
```
┌─────────────────────────────────────┐
│ ⚠️ DEFECT RISK: 84%   [CI: 71-93%] │
│ Unit U04412 · SEDAN · Lot L0023    │
│ ⏱ 22 min before inspection          │
│                                     │
│ ROOT CAUSES:                        │
│ ● Paint buffer dwell: 195s (▲ 62%) │  ← bar showing magnitude
│ ● S15 prep time: 22s (▼ 51%)       │
│ ● S8 vibration: 96th pctl (▲)      │
│                                     │
│ 7 units at risk                     │
│                                     │
│ [Show Units ↗]  [👍 Useful] [👎 False] │
└─────────────────────────────────────┘
```

- Probability shown as a large coloured number (red > 0.7, amber 0.4–0.7, green < 0.4)
- Confidence interval shown next to probability
- Lead time prominently displayed with a countdown feel
- Root causes as a mini horizontal bar chart showing contribution magnitude
- Each cause has a plain-language explanation
- "Show Units" opens the ContainmentPanel
- Thumbs up/down buttons POST to `/api/feedback`

**ContainmentPanel (`components/supervisor/ContainmentPanel.tsx`):**
Slides in from the right (or modal) when "Show Units" is clicked:
```
┌─────────────────────────────────────┐
│ CONTAINMENT: 7 Units at Risk        │
│ Triggered by Unit U04412            │
│                                     │
│ Unit    │ At Stn │ Risk │ Shared    │ Action │
│ U04408  │ S22    │ 71%  │ Lot,Paint │ HOLD   │
│ U04409  │ S19    │ 65%  │ Lot,Opr15 │ INSPECT│
│ U04410  │ S17    │ 62%  │ Paint     │ INSPECT│
│ U04411  │ S15    │ 58%  │ Lot       │ MONITOR│
│ U04413  │ S8     │ 44%  │ Lot       │ MONITOR│
│ U04414  │ S5     │ 39%  │ Lot       │ MONITOR│
│ U04415  │ S3     │ 35%  │ Lot       │ MONITOR│
│                                     │
│ Containment window: 18 min          │
│ [Export List] [Acknowledge All]     │
└─────────────────────────────────────┘
```

**Shift Summary Bar (`components/supervisor/ShiftSummary.tsx`):**
Bottom strip showing shift-level KPIs:
- Units produced this shift / target
- Defects caught / total alerts
- Alert precision this shift (useful / total acknowledged)
- Current bottleneck station
- Line efficiency (actual throughput / theoretical max)

---

#### Manager View (`app/manager/page.tsx`) — ANALYTICAL

**Layout: Grid layout. Top row = KPIs + bottleneck. Middle = charts. Bottom = what-if.**

**KPI Row (4 cards):**
- Throughput: current UPH vs target, trend arrow
- Line Efficiency: % with sparkline
- Defect Rate: current vs historical, trend arrow
- Bottleneck: current station name + duration at that station

**Bottleneck Timeline (`components/manager/BottleneckTimeline.tsx`):**
Full-width time-series chart showing which station is the bottleneck over time. X-axis = time (24h or 7d selectable). Y-axis = station IDs. Coloured blocks showing when each station was the constraint. The key visual: the bottleneck MOVES from S12 to S19 during EV-heavy shifts.

Mark shift boundaries with vertical dashed lines. Mark variant-mix changes. Show ground-truth accuracy as an annotation: "Detection accuracy: 87%".

**Station League Table (`components/manager/StationLeagueTable.tsx`):**
Sortable table of all stations ranked by lost-throughput contribution:
```
Rank │ Station      │ Zone  │ Tier   │ Utilisation │ Blocked% │ Starved% │ Lost UPH │ Trend
1    │ S19 Battery  │ Final │ Full   │ 94.2%       │ 0.3%     │ 0.1%     │ 3.8      │ ▲
2    │ S12 Body End │ Body  │ Full   │ 91.7%       │ 1.2%     │ 0.0%     │ 2.1      │ ▼
3    │ S15 Paint Pr │ Paint │ Manual │ 89.1%*      │ 3.4%*    │ 0.2%*    │ 1.4*     │ →
```
Asterisks and amber text for inferred stations. ProvenanceBadge on each row.

**Root Cause Pareto (`components/manager/RootCausePareto.tsx`):**
Horizontal bar chart of the top contributing features across all flagged units, grouped by category:
- Paint process (dwell time, humidity, film thickness)
- Operator behaviour (checklist time compression)
- Structural (torque, vibration)
- Environmental (ambient temperature, humidity)
Shows cumulative line (80/20 visual).

**Variant Mix Impact (`components/manager/VariantMixImpact.tsx`):**
Small multiples or grouped bar chart showing defect rate by variant and shift:
```
         Morning    Afternoon    Night
Sedan    1.8%       2.1%         2.4%
SUV      2.2%       2.5%         2.9%
EV       3.1%       4.8%         5.2%    ← the Tuesday Problem shows here
```

**Trend Charts (`components/manager/TrendCharts.tsx`):**
2x2 grid of line charts:
- Cycle time by station (top 5 worst) over 24h
- Defect rate over 24h with alert threshold line
- Throughput over 24h with shift markers
- Buffer utilisation over 24h (top 5 most full)

**What-If Console (`components/manager/WhatIfConsole.tsx`):**
THIS IS THE TWIN MOMENT. Make it prominent and easy to use.
```
┌─────────────────────────────────────────────────┐
│ WHAT-IF SIMULATION                              │
│                                                 │
│ Station: [S17 Clear Coat  ▾]                    │
│                                                 │
│ Buffer Change:  ◄──●──────────► +4 slots        │
│ Cycle Time:     ◄────────●────► +0%             │
│                                                 │
│ ┌──────────────┐  ┌──────────────┐              │
│ │ BASELINE     │  │ PROJECTED    │              │
│ │ 52.1 UPH     │  │ 53.7 UPH ▲  │              │
│ │ WIP: 45      │  │ WIP: 41  ▼  │              │
│ │ BN: S19      │  │ BN: S19     │              │
│ └──────────────┘  └──────────────┘              │
│                                                 │
│ Δ Throughput: +1.6 UPH (+3.1%)                  │
│ Annual Value: $184,000                          │
│ Mode: Interpolated from precomputed grid        │
└─────────────────────────────────────────────────┘
```
Sliders update the projection in real-time (client-side fetch with debounce). Show the `mode: interpolated` honestly.

---

#### Leadership View (`app/leadership/page.tsx`) — FINANCIAL & STRATEGIC

**Layout: KPI cards top, waterfall mid-left, planner mid-right, roadmap bottom.**

**KPI Cards (`components/leadership/KPICards.tsx`):**
4 large cards with big numbers:
```
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ SCRAP SAVED  │ │ NET VALUE    │ │ SYSTEM ROI   │ │ PAYBACK      │
│ $1.25M /yr   │ │ $2.70M /yr   │ │ 670%         │ │ 1.6 months   │
│ 260 units    │ │ ±30% band    │ │              │ │              │
│ Source ⓘ     │ │ Source ⓘ     │ │ Source ⓘ     │ │ Source ⓘ     │
└──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
```
Each has an info tooltip showing the source from economics.yaml. Below each: a sparkline of the metric trending.

**Sensitivity Toggle (`components/leadership/SensitivityToggle.tsx`):**
Three toggle buttons: Pessimistic / Base / Optimistic. Changes ALL numbers on the page simultaneously. Different assumptions shown below each scenario.

**Value Bridge Waterfall (`components/leadership/ValueBridge.tsx`):**
Recharts waterfall chart showing:
```
Baseline Scrap → +Scrap Avoided → +Rework Avoided → +Downtime Avoided → +Warranty → -Alarm Cost → -System Cost → Net Value
```
Each bar is coloured (green for gains, red for costs). Hover shows the source.

**Cost Breakdown (`components/leadership/CostBreakdown.tsx`):**
Donut or horizontal stacked bar showing where value comes from:
- Scrap avoidance: 46%
- Warranty escape prevention: 25%  
- Downtime reduction: 18%
- Rework avoidance: 11%

**Instrumentation Planner (`components/leadership/InstrumentationPlanner.tsx`):**
THE DIFFERENTIATOR. A table + timeline showing the phased sensor retrofit:

```
┌──────────────────────────────────────────────────────────────┐
│ INSTRUMENTATION ROI PLANNER                                  │
│                                                              │
│ Station     │ Sensor        │ Cost   │ ΔPR-AUC │ Value/yr  │ Window      │
│ S15 Paint   │ Temp+Humidity │ $1,200 │ +0.040  │ $138,000  │ March 2026  │
│ S22 Interior│ Torque        │ $2,800 │ +0.020  │ $67,000   │ March 2026  │
│ S10 Bracket │ Vibration     │ $1,800 │ +0.010  │ $31,000   │ July 2026   │
│ S27 Electr. │ Torque        │ $2,800 │ +0.008  │ $22,000   │ July 2026   │
│             │               │        │         │           │             │
│ TOTAL       │               │ $8,600 │ +0.078  │ $258,000  │             │
└──────────────────────────────────────────────────────────────┘
```

Below the table: a small Gantt-style timeline showing the three maintenance windows and which stations get instrumented in each.

**Plant Selector:**
Same as supervisor view. Switching to Plant B reloads ALL data. This is the scalability proof point.

**Rollout Timeline (`components/leadership/RolloutTimeline.tsx`):**
A horizontal phased roadmap:
```
Phase 1 (8 wk)    Phase 2 (12 wk)    Phase 3 (16 wk)    Phase 4 (ongoing)
Shadow Mode        Advisory Alerts     MES Integration     Multi-Plant
├─ Silent predict  ├─ One line         ├─ Quality holds    ├─ Plant B
├─ Measure prec.   ├─ Supervisor UI    ├─ Auto-containment ├─ Config adapt
├─ Zero exposure   ├─ Feedback loop    ├─ Manager+Lead UI  ├─ Transfer learn
├─ Tune threshold  ├─ Tune alarm budg. ├─ Instrumentation  ├─ Central dash
```

**Model Metrics Panel:**
Collapsible section at the bottom showing:
- PR-AUC, Precision@budget, Recall, Lead time
- Calibration plot (reliability diagram)
- Soft sensor validation results (masked station R²)
- Bottleneck detection accuracy
- False positive rate and trend

---

### PHASE 9: Containerisation & Demo Readiness

**`serve/Dockerfile`:**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY serve/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY serve/ ./serve/
COPY artifacts/ ./artifacts/
EXPOSE 7860
CMD ["uvicorn", "serve.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
```

**`serve/requirements.txt`:**
```
fastapi==0.115.0
uvicorn[standard]==0.30.0
pydantic==2.9.0
numpy==1.26.4
orjson==3.10.0
```

**`.dockerignore`:**
```
offline/
data/
frontend/
tests/
docs/
.git/
__pycache__/
*.pyc
```

**`docker-compose.yml`** (for local dev — production uses HF Spaces for backend + Vercel for frontend):
```yaml
services:
  backend:
    build:
      context: .
      dockerfile: serve/Dockerfile
    ports:
      - "7860:7860"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:7860/api/health"]
      interval: 10s
      timeout: 5s
      retries: 3
  frontend:
    build:
      context: ./frontend
    ports:
      - "3000:3000"
    depends_on:
      backend:
        condition: service_healthy
```

**`Makefile`:**
```makefile
generate:
	python -m offline.simulator.generate --config configs/plant_a.yaml --seed 42
	python -m offline.simulator.generate --config configs/plant_b.yaml --seed 43

train:
	python -m offline.ml.train --config configs/plant_a.yaml
	python -m offline.ml.train --config configs/plant_b.yaml

build-artifact:
	python -m offline.build_artifact --config configs/plant_a.yaml
	python -m offline.build_artifact --config configs/plant_b.yaml

test:
	pytest tests/ -v

demo:
	docker-compose up --build

dev-backend:
	cd serve && uvicorn main:app --reload --port 7860

dev-frontend:
	cd frontend && npm run dev
```

**Frontend `next.config.ts`:**
```typescript
const config = {
  output: 'export',
  images: { unoptimized: true },
  // NEXT_PUBLIC_API_BASE from env
};
export default config;
```

**Acceptance:**
- `make demo` brings both services up and healthy within 90 seconds
- Backend RSS < 200 MB (visible in `/api/health`)
- Docker image size < 400 MB
- Frontend static export works (no SSR, all client-side fetching)
- Switching plant_a → plant_b in the UI reloads everything correctly

---

### PHASE 10: Documentation

**`README.md`:**
- One-paragraph pitch
- Architecture diagram (ASCII or Mermaid — rendered in GitHub)
- **The compliance table** (this is what makes judges' lives easy):

```
| Brief Complexity | Feature | Where in Code |
|---|---|---|
| 1. Mixed legacy/modern | Soft sensors + INFERRED badge | `offline/ml/soft_sensor.py`, `ProvenanceBadge.tsx` |
| 2. Multi-causal, intermittent | SHAP explanations + 3 defect modes | `offline/ml/defect_model.py`, `AlertCard.tsx` |
| 3. Can't modify live systems | Read-only arch + Instrumentation Planner | `ARCHITECTURE.md`, `InstrumentationPlanner.tsx` |
| 4. Delayed defect surfacing | Containment tracing + lead time | `serve/routers/trace.py`, `ContainmentPanel.tsx` |
| 5. Different stakeholders | 3-persona dashboard | `/supervisor`, `/manager`, `/leadership` |
| 6. Multi-site scaling | Config-driven + Plant B swap | `configs/plant_b.yaml`, `PlantSelector.tsx` |
| 7. False alarms erode trust | Cost-optimal threshold + alarm budget + feedback | `offline/ml/thresholds.py`, `AlertBudgetCounter.tsx` |
```

- Quickstart: `make demo`
- Metrics summary table
- Tech stack
- Limitations section (honest)
- Demo video embed link

**`docs/BUSINESS_PROPOSAL.md`:** Full business proposal as specified in the deliverables. Problem framing with numbers, solution design, three user personas with jobs-to-be-done, business case with value bridge, 5-phase roadmap (shadow → advisory → MES integration → multi-line → cross-plant), risks & mitigations (alarm fatigue, model drift, OT security, operator privacy, union acceptance, vendor lock-in). 3-year NPV with discount rate stated.

**`docs/ARCHITECTURE.md`:** 
- ISA-95 / Purdue model placement diagram (the twin sits at Level 3, reads from Level 2 historians, never writes below Level 3)
- Data flow: OPC-UA / MQTT → Edge Gateway → Historian → Twin
- Component architecture diagram
- The precompute-heavy / serve-thin split explained
- Why read-only: operational risk, regulatory, change management

**`docs/MODEL_CARD.md`:** Intended use, out-of-scope use (not a replacement for quality inspection), training data, temporal split, all metrics with confidence intervals, calibration plot reference, soft sensor masked-station R², ablation results, known failure modes (low-humidity weeks have fewer Mode A examples → model is less reliable), drift monitoring plan.

**`docs/ASSUMPTIONS.md`:** Every economic, physical, and operational assumption. Each with a justification and source. When a judge asks "where did $5k/hour come from?" the answer is on page 1.

**`docs/DEMO_SCRIPT.md`:** The 5-minute demo arc:
1. (0:00) Line running on /supervisor. Point out INFERRED stations.
2. (0:45) Unit passes S3 with low torque. No alarm. "Deliberately below cost threshold."
3. (1:15) Same unit hits S8 with high vibration on humid day. Alert fires. Lead time: 22 min.
4. (1:45) Click "Show Units" → 7 at-risk units. "This is containment."
5. (2:30) /manager: bottleneck moved S12→S19. What-if: +4 buffer at S17 → +3.1% throughput.
6. (3:30) /leadership: ROI with sources. Instrumentation Planner: "$1,200 at S15 → $138k/year."
7. (4:15) Switch to Plant B. Everything reloads. "Config only."
8. (4:45) Metrics table + shadow-mode rollout plan.

---

## 5. DEMO DATA SEEDING

**CRITICAL:** The committed artifacts must be seeded so that the demo script above is reproducible. Specifically:

- Unit U04412 (or similar memorable ID) must be a SEDAN that passes S3 with torque ~8% below target, then hits S8 with vibration in the 95th+ percentile, on a humid day. Its risk should cross the cost-optimal threshold between S8 and S12.
- At the time of U04412's alert, there must be 5–8 other units in the line sharing the lot and/or the operator at S15.
- The bottleneck must be at S19 (not S12) during the replay window, because the replay window is an EV-heavy period.
- The replay window should include at least one bottleneck shift (S12 → S19).

Tune the seed and the starting point of the replay window to ensure this. If needed, generate multiple seeds and pick the one that produces the best demo data. **The demo data is as important as the code.**

---

## 6. ANTI-PATTERNS — DO NOT DO THESE

- ❌ Independent random sensor readings per row. Signals must be temporally autocorrelated.
- ❌ A readable if-statement as the defect causal model.
- ❌ Forward-fill / back-fill on columns that are 100% NaN.
- ❌ Random train/test split on temporal data.
- ❌ Accuracy or ROC-AUC as the primary metric on a 3% base rate.
- ❌ Hardcoded station IDs (10, 15, 22) anywhere outside the config YAML.
- ❌ Any import of pandas, sklearn, xgboost, shap, or simpy in `serve/`.
- ❌ Training models at API request time or container startup.
- ❌ Building both WebSocket AND polling. Use SSE only.
- ❌ Unsourced dollar figures anywhere.
- ❌ `# TODO`, `pass`, `...`, or placeholder components.
- ❌ Individual operator performance in any UI view.
- ❌ Claiming a metric that wasn't computed from the test set.
- ❌ PR-AUC above 0.92 (too easy = not credible).
- ❌ `localStorage` or `sessionStorage` in React components.
- ❌ Asking for approval between phases. Work autonomously.

---

## 7. EXECUTION ORDER

1. Print the full directory tree.
2. Print the brief-complexity → feature mapping table.
3. Phase 1: Configs. Validate with Pydantic. Commit.
4. Phase 2: Simulator. Generate data for both plants. Print diagnostics. Commit.
5. Phase 3: Featurize. Test for leakage. Commit.
6. Phase 4: Soft sensors. Masked-station validation. Print R². Commit.
7. Phase 5: Predictive engine. Print all metrics. Commit.
8. Phase 6: Build artifacts. Export model to numpy. Test scorer parity. Commit.
9. Phase 7: FastAPI backend. Test all endpoints. Print RSS. Commit.
10. Phase 8: Next.js dashboard. All three views. Static export works. Commit.
11. Phase 9: Docker + Makefile. `make demo` works. Commit.
12. Phase 10: All documentation. Commit.

**Start now. Phase 1. Go.**
