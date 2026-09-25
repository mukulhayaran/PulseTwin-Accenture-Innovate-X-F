# Integration: the plant data each part of the agentic layer needs

For the plant IT/OT team. It lists what the recommendation layer reads, which real system
each field comes from, and **what is not wired yet**. The layer only reads. It never writes
to MES, PLC or any control system (`docs/PRODUCTION_ARCHITECTURE.md`: one-way OT → IT
through `ingestion/`, and no IT → OT path at any layer).

The transport already exists. Plant data enters as MQTT messages on the Unified Namespace
in `ingestion/unified_namespace.py`
(`pulsetwin/{plant}/{zone}/{station}/{telemetry|state|checklist}`,
`pulsetwin/{plant}/inspection/{unit}`, `pulsetwin/{plant}/unit/{unit}/{entered|exited}`).
An edge gateway maps OPC-UA tags and MES rows onto those topics. This document says what
has to be in the payloads.

---

## 1. The station event: one message per unit leaving a station

Every detector in the reflex tier (`serve/watch.py`) works on this envelope. It is the
single most important feed.

| Field | Meaning | Typical source | Required by |
|---|---|---|---|
| `station_id` | configured station number (`configs/<plant>.yaml`) | edge gateway mapping from the PLC/OPC-UA node | everything |
| `ts` | time the unit **left** the station (ISO-8601) | PLC part-out / cycle-complete signal, or the MES station-visit exit timestamp | everything; silence detection is driven by the gaps between these |
| `unit_id` | VIN or body number | MES build sequence / AVI read point | genealogy, containment, scoring |
| `variant` | model / derivative | MES order data | tool-wear baselines are **per variant**; a variant-mix change otherwise reads as drift |
| `lot_id` | material lot of the part fitted | MES traceability (BOM lot scan) | lot containment, lot risk |
| `station_state` | `working` / `blocked` / `starved` / `down` at exit | PLC machine-state tags (PackML state or the OEE state word) | starvation, silence diagnosis (DOWN → breakdown, otherwise congestion) |
| `cycle_time_s` | the unit's cycle at this station | PLC cycle timer, or exit − entry from MES | starvation (mean cycle vs takt) |
| `blocked_before_s`, `buffer_wait_s` | time held before release / waiting in the buffer | PLC timers or conveyor accumulation logic | bottleneck features, dwell (paint Mode A) |
| `buffer_len`, `buffer_capacity` | accumulation occupancy after the station | conveyor PLC counters | counterfactual initial state |

## 2. Process signals: per unit, per station, on the telemetry topic

`ingestion/mqtt_subscriber.py` accepts exactly these (`SIG_COLS`). Each one needs a
per-station, per-variant baseline before the watch can use it
(`python -m offline.build_watch_rules`).

| Signal | Source system | Used by |
|---|---|---|
| `torque_nm` (final torque per rundown; add angle if available) | nutrunner controller (Atlas Copco / Bosch Rexroth via OPC-UA or the controller's result protocol) | tool-wear watch, defect model (structural fastening, Mode C) |
| `weld_current_a` | spot-weld controller, per spot | tool-wear watch |
| `vibration_mm_s` | condition-monitoring sensor | defect model (Mode C needs torque **and** vibration) |
| `temperature_c`, `humidity_pct` | paint-booth zone sensors (BMS / HVAC controller) | paint watch, defect model (Mode A) |
| `film_thickness_um` | paint-thickness gauge | defect model (Mode A co-trigger) |
| `ambient_humidity_pct` | outside-air / plant BMS sensor | paint watch (zone excess over ambient). **Not in `SIG_COLS` today**, see §5 |

## 3. Manual stations, inspection, and master data

| Feed | Source system | Used by | Constraint |
|---|---|---|---|
| Checklist entries (`checklist_time_s`, pass flag, note code) | MES operator terminal at manual stations | soft sensors, defect model (Mode B rushed prep) | **operator identity must be hashed to a group at the edge** (contract #3; `genai/verifier.py::_contains_operator_identity` is the definition) |
| Inspection outcome per unit (defect flag, defect/fault code, station) | QMS / EOL inspection records | model monitoring, lot containment, confirming a proposal was effective | the defect code must map to a mode (A/B/C), otherwise attribution is `unattributed` |
| Station master data: nominal cycle per variant, cycle CV, buffer capacities, MTBF, mean repair time, sensor fit | engineering / MES master data, CMMS history for MTBF and repair | the counterfactual twin (`configs/<plant>.yaml` `stations:`) | static. Review it when the line is re-balanced |
| Shift pattern, takt | MES / production planning | the watch (takt, silence threshold), shift aggregation | |

## 4. What each part of the layer reads

| Component | Tier | Reads | Latency budget |
|---|---|---|---|
| Watch (`serve/watch.py`): starvation, silence, paint, tool wear | reflex, **no LLM** | §1 envelope + §2 signals, per event; per-variant baselines from `artifacts/watch_rules_<plant>.json` | per event; windows of 8 events; silence at 12 takts (plant A) |
| Counterfactual (`offline/counterfactual.py`) | agent tier, deterministic | station master data (§3), the firing's evidence | ~90 s alone, ~4 min on a busy host (ADR-055) |
| Starvation / paint / tool-wear agents (`genai/`) | agent tier, LLM optional | the API only: watch firings, station stats, what-if grid, knowledge index. **They never read OT data directly** (`genai/tools.py` is the egress allowlist) | seconds to ~70 s |
| Containment / evidence graph (`/api/trace/containment/{unit}`) | API | genealogy: unit × station × entry/exit × lot × operator group | request time |
| Defect scorer (`serve/scorer.py`) | reflex | §2 signals up to the prediction cutoff station, §3 checklists | per unit |
| Human decision (`/api/proposals`) | human | the three above | a human |

## 5. Not wired yet: the list to close before a real feed goes live

1. **The watch does not run on MQTT data.** `serve/live_simulation.py` creates a
   `LineWatch` only for backend replay runs (`producer='backend'`). Messages from
   `ingestion/` are projected into `simulation_events` by `ingestion/live_projection.py`,
   and no detector sees them. A real feed needs a watch driver that calls `observe()` per
   projected event.
2. **Silence needs a wall clock on a real feed.** `LineWatch.tick(now)` (ADR-057) is what
   fires when a line stops completely and sends nothing. The replay producer never needs
   it. A real driver must call it on a timer, driven by the data watermark and not by
   wall time while ingestion is paused, or a network outage reads as a line stoppage.
3. **`ambient_humidity_pct` is not in the subscriber's `SIG_COLS`.** Without it the
   paint-environment detector cannot compute zone excess over ambient, and never fires.
4. **Weather-driven humidity is not detected by design** (`docs/AGENT_EVAL.md`). The rule
   fires on zone humidity the ambient does not explain, but Mode A's defect physics keys
   on absolute humidity. The plant must decide whether weather-driven humidity is an
   alarm.
5. **The counterfactual reproduces the detected condition on a model of the line, not the
   live line's state.** A real twin should start forks from the live snapshot (WIP per
   buffer, station states), which is what ADR-058 asks a real twin to provide.
