"""The live digital-twin engine: stepped physics in, model-generated alerts out.

    twin = LiveTwinEngine(cfg, seed=42, artifacts=Path("artifacts"))
    twin.start()                      # warm-up: fills feature history, freezes calibration
    events = twin.advance(60.0)       # advance 60 simulated seconds, return what happened
    twin.inject(Incident("torque_degradation", 0.8, station_id=5))

THE CHAIN, END TO END. Every alert this engine produces travels the whole path:

    DES event  ->  station/buffer/blocking state   (offline.simulator.line_model, STEPPED)
               ->  sensor readings                 (offline.live_twin.signals)
               ->  features                        (feature_contract, shared with ingestion)
               ->  raw ensemble score              (serve.scorer.Scorer)
               ->  Platt calibration               (Scorer.predict)
               ->  operating threshold             (the plant's budget threshold)
               ->  alert

There is exactly ONE place a risk number is produced -- `_score_unit`, which calls
`Scorer.predict`. Nothing in this module adds to, scales, or floors a risk value, and
`tests/test_live_simulation_engine.py::test_no_module_writes_a_risk_value` reads this
file's source to keep it that way.

WHAT WARM-UP IS FOR. Three things genuinely need history before the first honest alert:
trailing medians (50 units), z-scores (200 units), and the causal hazard calibration. The
engine runs a warm-up phase during which it computes everything but suppresses alerts, and
reports `warming_up` so a UI can say so rather than showing a confident number built on
eight samples.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from functools import partial
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from feature_contract import (
    LOT_TRAILING_UNITS,
    NOTE_CODES,
    TRAILING_MEDIAN_UNITS,
    TRAILING_Z_UNITS,
    signal_features,
)
from offline.config_schema import PlantConfig
from offline.live_twin.causal import LiveCausal
from offline.live_twin.incidents import CONGESTION_SLOWDOWN, Incident, IncidentState
from offline.live_twin.signals import LiveSignalGenerator
from offline.simulator.line_model import SIM_START, LineSimulator
from offline.simulator.operators import OperatorPool
from offline.simulator.physics import build_latents
from serve.model_loader import load_scorer
from serve.scorer import Scorer

# Visit tuple layout emitted by LineSimulator. Mirrors generate.VISIT_COLUMNS; named here
# so an index change in one place fails loudly rather than shifting a column silently.
V_UNIT_SEQ, V_STATION, V_ENTRY, V_EXIT, V_CYCLE, V_PRIOR = 0, 1, 2, 3, 4, 5
V_BLOCKED, V_BUFWAIT, V_CHECKLIST, V_OPERATOR, V_SHIFT = 6, 7, 8, 9, 10

DEFAULT_WARMUP_UNITS = 260   # > TRAILING_Z_UNITS, so z-scores are defined at handover
# Long enough to span the weekday humidity cycle that gates Mode A. See
# LiveCausal.calibrate_from_batch for why this cannot be the live warm-up cohort.
DEFAULT_CALIBRATION_DAYS = 3.0
MAX_HISTORY = max(TRAILING_Z_UNITS, TRAILING_MEDIAN_UNITS)


@dataclass
class UnitRecord:
    """Everything known about one unit still on the line."""

    unit_seq: int
    unit_id: str
    variant: str
    lot_id: str
    released_at: float
    visits: list[tuple] = field(default_factory=list)
    signals: dict[int, dict[str, float]] = field(default_factory=dict)
    features: dict[str, float] = field(default_factory=dict)
    risk: float | None = None
    risk_raw: float | None = None
    alerted: bool = False
    scored_at: float | None = None
    inspected: bool = False
    actual_defect: int | None = None
    defect_mode: str = ""
    dropped_signals: set[str] = field(default_factory=set)


class LiveTwinEngine:
    """A running line. One instance per live run; not thread-safe by design -- the owning
    process drives it from a single loop (see run.py)."""

    def __init__(
        self,
        cfg: PlantConfig,
        seed: int,
        artifacts: Path,
        budget_threshold: float,
        warmup_units: int = DEFAULT_WARMUP_UNITS,
        max_units: int = 200_000,
        calibration_days: float = DEFAULT_CALIBRATION_DAYS,
    ) -> None:
        self.cfg = cfg
        self.seed = seed
        self.budget_threshold = float(budget_threshold)
        self.warmup_units = int(warmup_units)
        self.calibration_days = float(calibration_days)
        self.cutoff = cfg.prediction_cutoff_station()
        self.inspection_station = max(s.id for s in cfg.stations if s.is_inspection_station)

        root = np.random.SeedSequence(seed)
        streams = [np.random.default_rng(s) for s in root.spawn(6)]
        self.rng_pool, self.rng_des, self.rng_latent = streams[0], streams[1], streams[2]
        self.rng_phys, self.rng_causal, self.rng_insp = streams[3], streams[4], streams[5]

        self.pool = OperatorPool(cfg, self.rng_pool)
        self.sim = LineSimulator(cfg, self.pool, self.rng_des, instrument=True)
        # Bound methods, not lambdas: copy.deepcopy rebinds a bound method to the COPIED
        # engine, so a counterfactual fork scales its own repairs and emits its own events.
        self.sim.repair_scale = self._repair_scale
        self.sim.state_listener = self._on_state_change
        self._state_events: list[dict] = []
        self.latents = build_latents(cfg, max_units, self.rng_latent)
        self.signals = LiveSignalGenerator(
            cfg, self.latents, self.pool.torque_bias_lookup(), self.rng_phys
        )
        self.incidents = IncidentState()
        self.causal = LiveCausal(cfg)
        # The plant's CHAMPION (artifacts/model_selection_<plant>.json, ADR-061), and its
        # own alert policy: a trailing-window budget for an XGBoost champion (ADR-062),
        # the static budget_threshold passed in otherwise.
        self.scorer, champion = load_scorer(artifacts, cfg.plant_id)
        if self.scorer is None:
            self.scorer = Scorer(artifacts / f"model_{cfg.plant_id}.npz")
            champion = {}
        self._rolling = champion.get("rolling")

        self._stations = {s.id: s for s in cfg.stations}
        self._base_capacity = {s.id: s.buffer_capacity_after for s in cfg.stations}
        self._base_cycle = {s.id: s.nominal_cycle_s for s in cfg.stations}
        self._base_checklist = {
            s.id: s.checklist_nominal_s for s in cfg.stations if s.checklist_nominal_s
        }

        self.units: dict[int, UnitRecord] = {}
        self._consumed_visits = 0
        # partial, not lambda: identical behaviour, and picklable -- ADR-064 caches the
        # warmed engine on disk so each experiment skips the ~13 s warm-up.
        self._history: dict[tuple[int, str], deque] = defaultdict(
            partial(deque, maxlen=MAX_HISTORY))
        self._variant_history: dict[tuple[int, str, str], deque] = defaultdict(
            partial(deque, maxlen=TRAILING_MEDIAN_UNITS))
        self._lot_outcomes: dict[str, deque] = defaultdict(
            partial(deque, maxlen=LOT_TRAILING_UNITS))
        self._warmup_rows: list[dict] = []
        self._bucket_cache: dict[str, int] = {}
        self._cutoff_seen = 0
        self._warmup_event: dict | None = None
        self._clock_target: float | None = None
        self.warming_up = True
        self.started = False

        self.units_completed = 0
        self.units_scored = 0
        self.alerts_opened = 0
        self.defects_true = 0
        self.events_processed = 0

    # ---------------------------------------------------------------------- clock

    @property
    def now(self) -> float:
        return self.sim.now

    def timestamp(self, sim_s: float | None = None) -> str:
        return (SIM_START + timedelta(seconds=float(self.now if sim_s is None else sim_s))).isoformat()

    # --------------------------------------------------------------------- control

    def start(self) -> dict:
        """Calibrate, warm the feature history, and open for business.

        Two phases, for the reasons in LiveCausal.calibrate_from_batch:
          1. a short BATCH run of the real offline pipeline fixes the hazard constants;
          2. a live warm-up fills trailing medians and z-scores with alerts suppressed.
        """
        if self.started:
            return self.snapshot()
        self.causal.calibrate_from_batch(self.calibration_days, self.seed, self.rng_causal)
        self.sim.start()
        self.started = True

        guard = 0
        while self.warming_up and self.sim.step():
            self.events_processed += 1
            self._drain_visits()
            if self._cutoff_seen >= self.warmup_units:
                self._warmup_event = self._finish_warmup()
            guard += 1
            if guard > 5_000_000:  # pragma: no cover - a horizon-bounded run cannot reach this
                raise RuntimeError("warm-up did not complete within the event budget")
        return self.snapshot()

    def advance(self, sim_seconds: float, max_events: int = 100_000) -> list[dict]:
        """Advance the clock by `sim_seconds` of SIMULATED time, returning emitted events.

        Wall-clock pacing is the caller's job (run.py divides by the speed multiplier).
        Simulation results therefore do not depend on how fast it is played -- which is
        what `tests/test_live_simulation_engine.py::test_speed_does_not_change_results`
        pins.
        """
        if not self.started:
            self.start()

        # The target is an ABSOLUTE clock carried on the engine, not `self.now +
        # sim_seconds`. A DES step jumps to the next scheduled event, which routinely
        # overshoots the requested boundary; re-anchoring each call on the overshot clock
        # made `advance` non-additive, so thirty advances of 60s ran FURTHER than one
        # advance of 1800s (989 events against 961) and a run played at a different speed
        # was a different line. Accumulating against an absolute target absorbs each
        # overshoot into the next interval instead of compounding it.
        # Pinned by tests/test_live_simulation_engine.py::test_speed_does_not_change_results.
        if self._clock_target is None:
            self._clock_target = self.now
        self._clock_target += float(sim_seconds)

        emitted: list[dict] = []
        steps = 0
        while self.now < self._clock_target and steps < max_events:
            if not self.sim.step():
                break
            self.events_processed += 1
            steps += 1
            emitted.extend(self._drain_visits())
        return emitted

    def inject(self, incident: Incident) -> dict:
        """Apply a PHYSICAL incident. Nothing here touches a score."""
        incident.start_s = self.now if incident.start_s <= 0 else incident.start_s
        self.incidents.inject(incident)
        self._apply_physical_state()
        return {
            "event_type": "incident_injected",
            "ts": self.timestamp(),
            "sim_time_s": self.now,
            "scenario": incident.scenario,
            "severity": incident.severity,
            "station_id": incident.station_id,
            "lot_id": incident.lot_id,
            "mechanism": _MECHANISM.get(incident.scenario, "physical condition changed"),
        }

    def _apply_physical_state(self) -> None:
        """Push incident effects into simulator state that the DES reads directly.

        Buffer capacity and checklist standards live on the runtime/config objects the
        simulator consults every event, so an incident changes them in place and the
        simulator's own mechanics do the rest. Breakdowns go through the simulator's
        OWN failure path (`_on_failure`), not through a state assignment, so repair
        scheduling, epoch invalidation and blocked/starved propagation all behave
        exactly as they do for a spontaneous failure.
        """
        now = self.now
        self.apply_congestion_pull(now)
        for rt in self.sim.stations:
            sid = rt.cfg.id
            rt.capacity = self.incidents.buffer_capacity(sid, self._base_capacity[sid], now)
            if sid in self._base_checklist:
                rt.cfg.checklist_nominal_s = (
                    self._base_checklist[sid] * self.incidents.checklist_scale(sid, now)
                )
            if self.incidents.forces_breakdown(sid, now) and rt.state != "down":
                self.sim._on_failure(rt, now)

    def apply_congestion_pull(self, now: float) -> None:
        """A congested station's NEXT station pulls slower, which is what makes the
        congested station block (incidents.CONGESTION_SLOWDOWN). Recomputed from the base
        cycle, so a mitigation that lowers the severity lowers the slowdown with it."""
        stations = self.sim.stations
        for i in range(1, len(stations)):
            sev = self.incidents.congestion_severity(stations[i - 1].cfg.id, now)
            sid = stations[i].cfg.id
            stations[i].cfg.nominal_cycle_s = self._base_cycle[sid] * (1.0 + CONGESTION_SLOWDOWN * sev)

    # ---------------------------------------------------------------------- events

    def _drain_visits(self) -> list[dict]:
        """Turn every visit the simulator has completed since the last call into events."""
        emitted: list[dict] = self._state_events
        self._state_events = []
        visits = self.sim.visits
        while self._consumed_visits < len(visits):
            visit = visits[self._consumed_visits]
            self._consumed_visits += 1
            emitted.extend(self._on_visit(visit))
        return emitted

    def _repair_scale(self, station_id: int, now: float) -> float:
        """A breakdown incident's severity lengthens its repairs. Until this hook existed
        `IncidentState.breakdown_repair_scale` was defined and never called (ADR-059)."""
        return self.incidents.breakdown_repair_scale(station_id, now)

    def _on_state_change(self, station_id: int, old: str, new: str, now: float) -> None:
        """What a PLC state tag publishes: the station went DOWN, or came back. A down
        station completes no unit, so without this its own events never say it is down
        and its neighbours' `blocked` reads as congestion (ADR-059)."""
        self._state_events.append({
            "event_type": "station_state",
            "ts": self.timestamp(now),
            "sim_time_s": now,
            "station_id": station_id,
            "station_state": new,
            "previous_state": old,
            "warming_up": self.warming_up,
        })

    def _unit(self, unit_seq: int) -> UnitRecord:
        record = self.units.get(unit_seq)
        if record is None:
            source = self.sim.units[unit_seq]
            record = UnitRecord(
                unit_seq=unit_seq,
                unit_id=source.unit_id,
                variant=source.variant,
                lot_id=source.lot_id,
                released_at=source.released_at,
            )
            self.units[unit_seq] = record
        return record

    def _on_visit(self, visit: tuple) -> list[dict]:
        unit_seq = int(visit[V_UNIT_SEQ])
        station_id = int(visit[V_STATION])
        station = self._stations[station_id]
        record = self._unit(unit_seq)
        record.visits.append(visit)

        entry_ts = float(visit[V_ENTRY])
        dropped = self.incidents.dropped_signals(station_id, entry_ts)
        readings = self.signals.emit(
            station,
            unit_seq=unit_seq,
            entry_ts=entry_ts,
            cycle_s=float(visit[V_CYCLE]),
            prior_state=str(visit[V_PRIOR]),
            operator_id=visit[V_OPERATOR],
            tool_health=self.incidents.tool_health(station_id, entry_ts),
            humidity_add_pct=self.incidents.humidity_add_pct(station_id, entry_ts),
            dropped=dropped,
        )
        record.signals[station_id] = readings
        record.dropped_signals |= set(dropped)

        # Per-signal features use the SHARED contract, so the live twin, the MQTT
        # ingestion path and the offline pipeline cannot drift from one another.
        for name, value in readings.items():
            if name.startswith("ambient_") or value is None or not math.isfinite(value):
                continue
            key = (station_id, name)
            record.features.update(
                signal_features(
                    f"s{station_id}_{name}",
                    value,
                    list(self._history[key]),
                    list(self._variant_history[(station_id, name, record.variant)]),
                )
            )
            self._history[key].append(value)
            self._variant_history[(station_id, name, record.variant)].append(value)

        if station.sensor_tier == "manual":
            self._checklist_features(record, visit, station)

        events: list[dict] = []
        if dropped:
            events.append({
                "event_type": "sensor_dropout",
                "ts": self.timestamp(entry_ts),
                "sim_time_s": entry_ts,
                "unit_id": record.unit_id,
                "station_id": station_id,
                "signals": sorted(dropped),
                "note": "value ABSENT, not imputed -- the model routes it down its "
                        "native missing-value branch",
            })

        events.append(self._station_event(record, visit, station_id, readings, dropped))

        if station_id == self.cutoff and record.risk is None:
            self._cutoff_seen += 1
            scored = self._score_unit(record)
            if scored:
                events.append(scored)

        if station_id == self.inspection_station:
            events.extend(self._inspect(record))

        return events

    def _station_event(self, record, visit, station_id, readings, dropped) -> dict:
        rt = next(s for s in self.sim.stations if s.cfg.id == station_id)
        event = {
            "event_type": "station_update",
            "ts": self.timestamp(float(visit[V_EXIT])),
            "sim_time_s": float(visit[V_EXIT]),
            "unit_id": record.unit_id,
            "unit_seq": record.unit_seq,
            "station_id": station_id,
            "station_state": rt.state,
            "cycle_time_s": round(float(visit[V_CYCLE]), 3),
            "blocked_before_s": round(float(visit[V_BLOCKED]), 3),
            "buffer_wait_s": round(float(visit[V_BUFWAIT]), 3),
            "buffer_len": len(rt.out_buf),
            "buffer_capacity": rt.capacity,
            "variant": record.variant,
            "lot_id": record.lot_id,
            "shift_id": visit[V_SHIFT],
            "warming_up": self.warming_up,
            "provenance": "inferred" if dropped else "measured",
        }
        for name, value in readings.items():
            if value is not None and math.isfinite(value):
                event[name] = round(float(value), 4)
        if record.risk is not None:
            event["calibrated_risk"] = record.risk
            event["alert"] = int(record.alerted)
        return event

    def _checklist_features(self, record: UnitRecord, visit: tuple, station) -> None:
        """Manual-station features -- the ONLY evidence for Mode B.

        Mirrors offline.ml.featurize._checklist_features, including the operator-identity
        HASHING (CLAUDE.md contract 3 / ADR-017): the bucket leaves this method, the
        operator id never does. `pd.util.hash_pandas_object` is used rather than Python's
        `hash()` because the offline buckets come from it and PYTHONHASHSEED would make
        `hash()` disagree between processes.
        """
        checklist_s = visit[V_CHECKLIST]
        if checklist_s is None or not math.isfinite(float(checklist_s)):
            return
        sid = station.id
        nominal = self._base_checklist.get(sid) or float(checklist_s)
        record.features[f"s{sid}_checklist_time_s"] = float(checklist_s)
        record.features[f"s{sid}_checklist_ratio"] = float(checklist_s) / max(nominal, 1e-9)

        entry = self._checklist_record(record.unit_seq, sid)
        if entry is None:
            return
        _, _, _, operator_id, checklist_pass, _, note_code, _ = entry
        record.features[f"s{sid}_checklist_pass"] = float(bool(checklist_pass))
        record.features[f"s{sid}_operator_bucket"] = float(self._operator_bucket(operator_id))
        for code in NOTE_CODES:
            record.features[f"s{sid}_note_{code.lower()}"] = float(note_code == code)

    def _checklist_record(self, unit_seq: int, station_id: int) -> tuple | None:
        """The operator pool's checklist tuple for this (unit, station).

        The pool appends to a flat list; scanning it from the tail is cheap because the
        record we want was written moments ago, and it avoids maintaining a second index
        that could disagree with the pool's own.
        """
        checklists = self.pool.checklists
        for i in range(len(checklists) - 1, max(-1, len(checklists) - 400), -1):
            row = checklists[i]
            if row[0] == unit_seq and row[1] == station_id:
                return row
        return None

    def _operator_bucket(self, operator_id: str) -> int:
        cached = self._bucket_cache.get(operator_id)
        if cached is None:
            cached = int(
                pd.util.hash_pandas_object(pd.Series([operator_id]), index=False).iloc[0]
                % self.cfg.operators.operator_bucket_count
            )
            self._bucket_cache[operator_id] = cached
        return cached

    # ----------------------------------------------------------------- flow features

    def _flow_features(self, record: UnitRecord) -> dict[str, float]:
        """Unit-level flow features, mirroring offline.ml.featurize._flow_features.

        Only visits at or before the prediction cutoff may contribute -- the same
        temporal-leakage guard the offline pipeline applies.
        """
        rows = [v for v in record.visits if int(v[V_STATION]) <= self.cutoff]
        if not rows:
            return {}
        entries = [float(v[V_ENTRY]) for v in rows]
        exits = [float(v[V_EXIT]) for v in rows]
        cycles = [float(v[V_CYCLE]) for v in rows]
        waits = [float(v[V_BUFWAIT]) for v in rows]
        priors = [str(v[V_PRIOR]) for v in rows]

        out = {
            "total_dwell_s": max(exits) - min(entries),
            "total_blocked_s": sum(float(v[V_BLOCKED]) for v in rows),
            "total_buffer_wait_s": sum(waits),
            "max_station_wait_s": max(waits),
            "stations_blocked_count": float(priors.count("blocked")),
            "stations_starved_count": float(priors.count("starved")),
            "mean_cycle_s": sum(cycles) / len(cycles),
            "max_cycle_s": max(cycles),
            "hour_of_day": float((SIM_START + timedelta(seconds=entries[0])).hour),
            "day_of_week": float((SIM_START + timedelta(seconds=entries[0])).weekday()),
        }

        window = self.cfg.defect_physics.mode_a.dwell_window
        if window.to_station <= self.cutoff:
            by_station = {int(v[V_STATION]): v for v in rows}
            a, b = by_station.get(window.from_station), by_station.get(window.to_station)
            if a is not None and b is not None:
                dwell = float(b[V_ENTRY if window.to_edge == "entry" else V_EXIT]) - float(
                    a[V_EXIT if window.from_edge == "exit" else V_ENTRY]
                )
                out["paint_buffer_dwell_s"] = dwell
                out["paint_buffer_dwell_ratio"] = (
                    dwell / self.cfg.defect_physics.mode_a.dwell_nominal_s
                )

        for zone in self.cfg.zones:
            in_zone = [s for s in zone.stations if s <= self.cutoff]
            if not in_zone:
                continue
            first = record.signals.get(min(in_zone))
            if first:
                out[f"{zone.id}_entry_humidity_pct"] = first.get("ambient_humidity_pct", float("nan"))
                out[f"{zone.id}_entry_temperature_c"] = first.get("ambient_temperature_c", float("nan"))

        for variant in self.cfg.variant_ids:
            out[f"variant_{variant}"] = 1.0 if record.variant == variant else 0.0
        shift = str(record.visits[0][V_SHIFT]).lower()
        for name in ("shift_a", "shift_b", "shift_c"):
            out[f"shift_{name}"] = 1.0 if shift.endswith(name[-1]) else 0.0

        outcomes = self._lot_outcomes.get(record.lot_id)
        if outcomes:
            out["lot_trailing_defect_rate"] = sum(outcomes) / len(outcomes)
        return out

    # ------------------------------------------------------------------- SCORING
    #
    # The ONLY place in this package that produces a risk value.

    def _score_unit(self, record: UnitRecord) -> dict | None:
        if self.warming_up:
            return None
        values = dict(record.features)
        values.update(self._flow_features(record))
        vector = self.scorer.vector_from(values)

        record.risk_raw = float(self.scorer.predict_raw_proba(vector)[0])
        record.risk = float(self.scorer.predict(vector)[0])
        cut = self._rolling.threshold if self._rolling is not None else self.budget_threshold
        record.alerted = record.risk >= cut
        if self._rolling is not None:
            self._rolling.observe(record.risk)
        record.scored_at = self.now
        self.units_scored += 1

        event = {
            "event_type": "alert" if record.alerted else "scored",
            "ts": self.timestamp(),
            "sim_time_s": self.now,
            "unit_id": record.unit_id,
            "station_id": self.cutoff,
            "calibrated_risk": round(record.risk, 6),
            "risk_raw": round(record.risk_raw, 6),
            "alert": int(record.alerted),
            "threshold": self.budget_threshold,
            "variant": record.variant,
            "lot_id": record.lot_id,
            "provenance": "inferred" if record.dropped_signals else "measured",
            "features_present": int(np.isfinite(vector).sum()),
            "features_total": len(self.scorer.feature_names),
            "risk_source": "model",  # never an injected constant; see module docstring
        }
        if record.alerted:
            self.alerts_opened += 1
            event["explanation_mode"] = "decision_path"
            event["top_factors"] = self.scorer.explain(vector, top=3)
        return event

    # ---------------------------------------------------------------- inspection

    def _inspect(self, record: UnitRecord) -> list[dict]:
        """Decide the unit's true outcome and close the loop."""
        self.units_completed += 1
        row = self._causal_row(record)

        if self.warming_up:
            self._warmup_rows.append(row)
            return []

        signals = {
            "mode_a": row["signal_mode_a"],
            "mode_b": row["signal_mode_b"],
            "mode_c": row["signal_mode_c"],
        }
        propensity = self.causal.propensity(
            signals, row["latent_lot_quality"], float(self.rng_causal.random())
        )
        record.actual_defect = int(self.rng_causal.random() < propensity)
        record.defect_mode = self.causal.attribute() if record.actual_defect else ""
        record.inspected = True
        self.defects_true += record.actual_defect
        self._lot_outcomes[record.lot_id].append(record.actual_defect)

        return [{
            "event_type": "inspection_outcome",
            "ts": self.timestamp(),
            "sim_time_s": self.now,
            "unit_id": record.unit_id,
            "station_id": self.inspection_station,
            "actual_defect": record.actual_defect,
            "defect_mode": record.defect_mode,
            "propensity": round(propensity, 6),
            "was_alerted": int(record.alerted),
            "calibrated_risk": record.risk,
        }]

    def _causal_row(self, record: UnitRecord) -> dict:
        """The unit's defect-mode drivers, mirroring causal_engine.build_unit_signals."""
        dp = self.cfg.defect_physics
        by_station = {int(v[V_STATION]): v for v in record.visits}
        window = dp.mode_a.dwell_window

        dwell = 0.0
        a, b = by_station.get(window.from_station), by_station.get(window.to_station)
        if a is not None and b is not None:
            dwell = float(b[V_ENTRY if window.to_edge == "entry" else V_EXIT]) - float(
                a[V_EXIT if window.from_edge == "exit" else V_ENTRY]
            )

        paint = [record.signals.get(s, {}) for s in dp.mode_a.contributing_stations]
        humid = [p["ambient_humidity_pct"] for p in paint if "ambient_humidity_pct" in p]
        humidity = sum(humid) / len(humid) if humid else 0.0

        gate = dwell > dp.mode_a.dwell_threshold_s and humidity > dp.mode_a.humidity_threshold_pct
        dwell_excess = min(2.0, max(0.0, (dwell - dp.mode_a.dwell_threshold_s) / dp.mode_a.dwell_nominal_s))
        hum_factor = 1.0 / (1.0 + math.exp(-(humidity - dp.mode_a.humidity_threshold_pct) / 2.5))
        weak = bool(self.latents.paint_batch_is_weak[record.unit_seq])
        mode_a = dwell_excess * hum_factor * (1.0 if weak else 0.06) if gate else 0.0

        b_visit = by_station.get(dp.mode_b.contributing_station)
        checklist = (
            float(b_visit[V_CHECKLIST])
            if b_visit is not None and b_visit[V_CHECKLIST] is not None
            and math.isfinite(float(b_visit[V_CHECKLIST]))
            else dp.mode_b.checklist_nominal_s
        )
        nominal, rush = dp.mode_b.checklist_nominal_s, dp.mode_b.checklist_rush_threshold_s
        mode_b = (
            min(1.4, max(0.0, (nominal - checklist) / (nominal - rush)))
            if checklist < rush else 0.0
        )

        tq_station, vb_station = dp.mode_c.contributing_stations
        from offline.simulator.physics import _station_targets
        target = _station_targets(tq_station)["torque_nm"]
        torque = record.signals.get(tq_station, {}).get("torque_nm")
        deficit = 100.0 * (target - torque) / target if torque is not None else 0.0
        vibration = record.signals.get(vb_station, {}).get("vibration_mm_s")
        pctile = self.causal.vibration_percentile(vibration)

        t_sig = min(1.5, max(0.0,
            (deficit - dp.mode_c.torque_deficit_threshold_pct) / dp.mode_c.torque_deficit_threshold_pct))
        v_ref = dp.mode_c.vibration_excess_percentile
        v_sig = 0.0 if not math.isfinite(pctile) else min(1.0, max(0.0, (pctile - v_ref) / (100.0 - v_ref)))
        mode_c = t_sig * v_sig if dp.mode_c.interaction_required else (t_sig + v_sig) / 2

        lot_quality = float(self.latents.lot_quality[record.unit_seq]) + \
            self.incidents.lot_quality_delta(record.lot_id, self.now)

        return {
            "unit_seq": record.unit_seq,
            "paint_buffer_dwell_s": dwell,
            "paint_humidity_pct": humidity,
            "prep_checklist_s": checklist,
            "mode_c_torque_nm": torque if torque is not None else float("nan"),
            "mode_c_torque_deficit_pct": deficit,
            "mode_c_vibration_mm_s": vibration if vibration is not None else float("nan"),
            "mode_c_vibration_pctile": pctile,
            "signal_mode_a": mode_a,
            "signal_mode_b": mode_b,
            "signal_mode_c": mode_c,
            "latent_lot_quality": lot_quality,
            "latent_paint_batch_weak": int(weak),
        }

    def _finish_warmup(self) -> dict:
        self.warming_up = False
        units = len(self._warmup_rows)
        self._warmup_rows.clear()
        return {
            "event_type": "warmup_complete",
            "ts": self.timestamp(),
            "sim_time_s": self.now,
            "units_observed": units,
            "calibration": self.causal.calibration.to_dict() if self.causal.calibration else None,
            "note": "feature history established and hazard calibration frozen; "
                    "alerts are live from here",
        }

    # ---------------------------------------------------------------------- state

    def snapshot(self) -> dict:
        stations = []
        for rt in self.sim.stations:
            stations.append({
                "station_id": rt.cfg.id,
                "name": rt.cfg.name,
                "state": rt.state,
                "unit_id": rt.unit.unit_id if rt.unit else None,
                "buffer_len": len(rt.out_buf),
                "buffer_capacity": rt.capacity,
                "base_capacity": self._base_capacity[rt.cfg.id],
            })
        in_progress = [
            u for u in self.units.values() if not u.inspected and u.visits
        ]
        return {
            "plant_id": self.cfg.plant_id,
            "seed": self.seed,
            "sim_time_s": round(self.now, 3),
            "sim_timestamp": self.timestamp(),
            "warming_up": self.warming_up,
            "events_processed": self.events_processed,
            "units_completed": self.units_completed,
            "units_scored": self.units_scored,
            "units_in_progress": len(in_progress),
            "alerts_opened": self.alerts_opened,
            "defects_true": self.defects_true,
            "observed_defect_rate": (
                self.defects_true / self.units_completed if self.units_completed else 0.0
            ),
            "throughput_uph": (
                self.units_completed / (self.now / 3600.0) if self.now > 0 else 0.0
            ),
            "threshold": self.budget_threshold,
            "prediction_cutoff_station": self.cutoff,
            "inspection_station": self.inspection_station,
            "stations": stations,
            "incidents": self.incidents.describe(self.now),
            "calibration": (
                self.causal.calibration.to_dict() if self.causal.calibration else None
            ),
        }


_MECHANISM = {
    "torque_degradation": "tool health at the station decays; achieved torque falls and "
                          "vibration rises through the shared physical cause",
    "high_humidity": "ambient relative humidity rises; film thickness thins through the "
                     "spray-transfer relationship",
    "station_breakdown": "the station is forced down through the simulator's own failure "
                         "path; downstream starves and upstream blocks",
    "buffer_congestion": "the station's output buffer capacity is physically reduced",
    "sensor_dropout": "named sensors stop reporting; the value is absent, not imputed",
    "rushed_checklist": "the manual station's checklist standard is shortened; the "
                        "operator model produces genuinely faster checklists",
    "bad_material_lot": "the unobservable incoming-lot quality latent is degraded for one "
                        "lot; no directly observable cause exists",
}
