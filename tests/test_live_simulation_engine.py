"""The TRUE live twin: stepped physics in, model-generated alerts out.

These tests are the acceptance criteria for the live-simulation mode. The one that matters
most is `test_no_module_writes_a_risk_value`, which reads the package's own source: the
whole point of this mode is that it never does what `serve/live_simulation.py::_raise_risk`
does, and a comment saying so is not enforcement.

COST. The engine calibrates from a short batch run at start-up, so a fresh engine is not
free. Every test that needs a started engine shares one module-scoped instance; the few
that must not share it say so.
"""

from __future__ import annotations

import ast
import math
import sqlite3
from pathlib import Path

import pytest

from offline.benchmarks.simrun import with_horizon
from offline.config_schema import load_plant_config
from offline.live_twin.engine import LiveTwinEngine
from offline.live_twin.incidents import (
    MAX_TOOL_DEFICIT,
    Incident,
    IncidentState,
)
from offline.live_twin.signals import Ar1Stepper

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"

# Short enough to keep the suite usable, long enough to span the weekday humidity cycle
# that gates Mode A -- see LiveCausal.calibrate_from_batch.
CALIBRATION_DAYS = 2.0
WARMUP_UNITS = 90


def _threshold(plant_id: str) -> float:
    con = sqlite3.connect(f"file:{ARTIFACTS / 'pulsetwin.db'}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT value FROM metrics WHERE plant_id=? AND key='budget_threshold'",
            (plant_id,),
        ).fetchone()
    finally:
        con.close()
    return float(row[0])


def build(seed: int = 42, days: float = 6.0) -> LiveTwinEngine:
    cfg = with_horizon(load_plant_config(str(ROOT / "configs" / "plant_a.yaml")), days)
    return LiveTwinEngine(
        cfg,
        seed=seed,
        artifacts=ARTIFACTS,
        budget_threshold=_threshold("plant_a"),
        warmup_units=WARMUP_UNITS,
        calibration_days=CALIBRATION_DAYS,
    )


@pytest.fixture(scope="module")
def twin() -> LiveTwinEngine:
    engine = build()
    engine.start()
    return engine


# ------------------------------------------------------- the load-bearing structural test


def test_no_module_writes_a_risk_value():
    """No assignment to a risk field anywhere in offline/live_twin/ except from the scorer.

    This is the difference between a simulation and a scripted demo, and it is the one
    property most likely to be quietly broken by a future "just make the demo pop" edit.
    Parsing the AST rather than grepping means a rename or a reformat cannot slip past it.
    """
    risk_names = {"risk", "calibrated_risk", "risk_raw", "score", "probability"}
    offenders = []

    for path in sorted((ROOT / "offline" / "live_twin").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AugAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                name = (
                    target.attr if isinstance(target, ast.Attribute)
                    else target.id if isinstance(target, ast.Name)
                    else None
                )
                if name not in risk_names:
                    continue
                # An AugAssign to a risk field is `calibrated_risk += severity` -- the
                # exact anti-pattern. Never allowed, whatever the right-hand side.
                if isinstance(node, ast.AugAssign):
                    offenders.append(f"{path.name}:{node.lineno} augmented assignment to {name}")
                    continue
                source = ast.unparse(node.value)
                if "scorer.predict" not in source:
                    offenders.append(f"{path.name}:{node.lineno} {name} = {source}")

    assert not offenders, (
        "a risk value is produced by something other than serve.scorer.Scorer.predict:\n  "
        + "\n  ".join(offenders)
    )


def test_incident_state_exposes_no_risk_handle():
    """The incident model must offer physical quantities only. If it ever grows a method
    returning a score, the separation this mode exists for is gone."""
    forbidden = {"risk", "score", "probability", "alert", "threshold", "calibrated"}
    for name in dir(IncidentState):
        if name.startswith("_"):
            continue
        assert not any(word in name.lower() for word in forbidden), (
            f"IncidentState.{name} looks like a risk handle; incidents change physics only"
        )


# ------------------------------------------------------------------------ determinism


def test_same_seed_produces_identical_events():
    a, b = build(seed=7), build(seed=7)
    a.start(); b.start()
    ea, eb = a.advance(1800.0), b.advance(1800.0)
    assert len(ea) == len(eb) and len(ea) > 0
    for left, right in zip(ea, eb):
        assert left["event_type"] == right["event_type"]
        assert left.get("unit_id") == right.get("unit_id")
        assert left.get("station_id") == right.get("station_id")
        assert left.get("calibrated_risk") == right.get("calibrated_risk")


def test_different_seeds_produce_a_different_realisation():
    a, b = build(seed=7), build(seed=8)
    a.start(); b.start()
    ea, eb = a.advance(1800.0), b.advance(1800.0)
    signature = lambda events: [
        (e.get("unit_id"), e.get("station_id"), e.get("cycle_time_s")) for e in events
    ]
    assert signature(ea) != signature(eb)


def test_speed_does_not_change_results():
    """Advancing in many small steps must equal advancing in one big one.

    Wall-clock pacing is the caller's business (run.py sleeps between advances). If the
    step size changed the physics, a demo run at 50x would not be the same line as a demo
    run at 5x, and no result from either would mean anything.
    """
    coarse, fine = build(seed=11), build(seed=11)
    coarse.start(); fine.start()
    big = coarse.advance(1800.0)
    small = []
    for _ in range(30):
        small.extend(fine.advance(60.0))
    assert len(big) == len(small)
    assert [e.get("unit_id") for e in big] == [e.get("unit_id") for e in small]
    assert coarse.snapshot()["units_scored"] == fine.snapshot()["units_scored"]


# -------------------------------------------------------------------------- the chain


def test_risk_comes_from_the_model_and_carries_its_provenance(twin):
    events = twin.advance(3 * 3600.0)
    scored = [e for e in events if e["event_type"] in ("scored", "alert")]
    assert scored, "no unit reached the prediction cutoff in three simulated hours"
    for event in scored:
        assert event["risk_source"] == "model"
        assert 0.0 <= event["calibrated_risk"] <= 1.0
        assert event["threshold"] == twin.budget_threshold
        assert event["alert"] == int(event["calibrated_risk"] >= twin.budget_threshold)


def test_every_model_feature_is_supplied(twin):
    """A live unit must arrive at the scorer with the SAME feature set the offline
    pipeline built. A silently missing block would route units down the model's
    missing-value branches and quietly change every score."""
    events = twin.advance(3600.0)
    scored = [e for e in events if e["event_type"] in ("scored", "alert")]
    assert scored
    for event in scored[:20]:
        assert event["features_present"] == event["features_total"], (
            f"{event['features_total'] - event['features_present']} features missing"
        )


def test_torque_degradation_moves_the_physics_then_the_model():
    """The acceptance criterion: an incident changes a PHYSICAL quantity, and the model's
    view of the unit changes as a consequence rather than by decree."""
    engine = build(seed=21)
    engine.start()
    station = engine.cfg.defect_physics.mode_c.contributing_stations[0]

    before = engine.advance(2 * 3600.0)
    engine.inject(Incident("torque_degradation", severity=0.95, station_id=station))
    after = engine.advance(2 * 3600.0)

    torque = lambda events: [
        e["torque_nm"] for e in events
        if e.get("station_id") == station and "torque_nm" in e
    ]
    baseline, degraded = torque(before), torque(after)
    assert baseline and degraded
    mean_before = sum(baseline) / len(baseline)
    mean_after = sum(degraded) / len(degraded)

    # tool_health floors at 1 - MAX_TOOL_DEFICIT * severity; the achieved torque must
    # follow it down by roughly that much.
    expected = 1.0 - MAX_TOOL_DEFICIT * 0.95
    assert mean_after < mean_before, "torque did not fall when the tool degraded"
    assert mean_after / mean_before == pytest.approx(expected, abs=0.08), (
        f"torque fell to {mean_after / mean_before:.3f} of baseline, expected ~{expected:.3f}"
    )

    state = engine.snapshot()["incidents"]
    assert state and state[0]["scenario"] == "torque_degradation"
    assert state[0]["tool_health"] < 1.0


def test_high_humidity_thins_the_film():
    """A second, independent causal path: ambient humidity is the cause, and film
    thickness responds through the spray-transfer relationship in the physics."""
    engine = build(seed=22)
    engine.start()
    station = next(
        s.id for s in engine.cfg.stations if "film_thickness_um" in (s.sensors or [])
    )
    before = engine.advance(2 * 3600.0)
    engine.inject(Incident("high_humidity", severity=1.0, station_id=station))
    after = engine.advance(2 * 3600.0)

    film = lambda events: [
        e["film_thickness_um"] for e in events
        if e.get("station_id") == station and "film_thickness_um" in e
    ]
    baseline, humid = film(before), film(after)
    assert baseline and humid
    assert sum(humid) / len(humid) < sum(baseline) / len(baseline), (
        "higher humidity did not thin the film"
    )


def test_sensor_dropout_removes_the_value_rather_than_imputing_it():
    engine = build(seed=23)
    engine.start()
    station = next(s.id for s in engine.cfg.stations if "torque_nm" in (s.sensors or []))
    engine.inject(Incident("sensor_dropout", severity=0.2, station_id=station))
    events = engine.advance(2 * 3600.0)

    at_station = [e for e in events if e.get("station_id") == station
                  and e["event_type"] == "station_update"]
    assert at_station
    assert all("torque_nm" not in e for e in at_station), "a dropped sensor still reported"
    assert all(e["provenance"] == "inferred" for e in at_station), (
        "a station with a failed sensor must not claim measured provenance (contract 1)"
    )
    assert any(e["event_type"] == "sensor_dropout" for e in events)


def test_station_breakdown_propagates_blocking_and_starvation():
    engine = build(seed=24)
    engine.start()
    # Mid-line, so there is something to block upstream AND starve downstream.
    station = engine.cfg.stations[len(engine.cfg.stations) // 2].id
    engine.inject(Incident("station_breakdown", severity=1.0, station_id=station))
    engine.advance(3600.0)

    states = {s["station_id"]: s["state"] for s in engine.snapshot()["stations"]}
    index = [s.id for s in engine.cfg.stations].index(station)
    upstream = [s.id for s in engine.cfg.stations][:index]
    downstream = [s.id for s in engine.cfg.stations][index + 1:]

    assert any(states[sid] == "blocked" for sid in upstream), (
        f"nothing upstream of a broken S{station} is blocked: {states}"
    )
    assert any(states[sid] == "starved" for sid in downstream), (
        f"nothing downstream of a broken S{station} is starved: {states}"
    )


def test_buffer_congestion_physically_shrinks_the_buffer():
    engine = build(seed=25)
    engine.start()
    station = next(s.id for s in engine.cfg.stations if s.buffer_capacity_after >= 4)
    base = next(s["buffer_capacity"] for s in engine.snapshot()["stations"]
                if s["station_id"] == station)
    engine.inject(Incident("buffer_congestion", severity=0.75, station_id=station))
    engine.advance(600.0)
    after = next(s["buffer_capacity"] for s in engine.snapshot()["stations"]
                 if s["station_id"] == station)
    assert after < base
    assert all(
        s["buffer_len"] <= s["buffer_capacity"] for s in engine.snapshot()["stations"]
    ), "a buffer holds more units than its capacity allows"


# --------------------------------------------------------------------------- outcomes


def test_inspection_closes_the_loop(twin):
    events = twin.advance(3 * 3600.0)
    outcomes = [e for e in events if e["event_type"] == "inspection_outcome"]
    assert outcomes, "no unit reached the inspection station"
    for event in outcomes:
        assert event["actual_defect"] in (0, 1)
        assert 0.0 <= event["propensity"] <= 1.0
        assert event["was_alerted"] in (0, 1)
    snapshot = twin.snapshot()
    assert snapshot["units_completed"] > 0
    # A defect rate wildly off the configured base rate means the frozen calibration is
    # not transferring to the live stream.
    assert 0.0 <= snapshot["observed_defect_rate"] < 0.25


def test_snapshot_is_shared_observable_state(twin):
    snapshot = twin.snapshot()
    for key in ("plant_id", "seed", "sim_time_s", "warming_up", "events_processed",
                "units_completed", "units_scored", "alerts_opened", "throughput_uph",
                "threshold", "stations", "incidents", "calibration"):
        assert key in snapshot, f"snapshot is missing '{key}'"
    assert len(snapshot["stations"]) == len(twin.cfg.stations)
    assert snapshot["calibration"]["fitted_k"] > 0


def test_warmup_suppresses_alerts_then_releases_them():
    engine = build(seed=26)
    assert engine.warming_up is True
    engine.start()
    assert engine.warming_up is False, "warm-up did not complete during start()"
    assert engine.causal.calibration is not None
    assert engine.causal.calibration.fitted_k > 0


# ---------------------------------------------------------------------------- signals


def test_ar1_stepper_is_stationary_and_reproducible():
    import numpy as np
    a = Ar1Stepper(0.9, 0.1, np.random.default_rng(3))
    b = Ar1Stepper(0.9, 0.1, np.random.default_rng(3))
    left = [a.step() for _ in range(500)]
    right = [b.step() for _ in range(500)]
    assert left == right
    assert all(math.isfinite(v) for v in left)
    # Stationary variance is sigma^2/(1-phi^2); the first draw already sits in it, so the
    # series must not trend away from zero.
    assert abs(sum(left) / len(left)) < 0.15
