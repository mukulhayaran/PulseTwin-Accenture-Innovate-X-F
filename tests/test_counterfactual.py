"""The Monte Carlo counterfactual (ADR-055) on the real physical twin, at toy size."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"

if not (ARTIFACTS / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from offline import counterfactual as CF  # noqa: E402
from serve.live_simulation import MITIGATIONS  # noqa: E402


def _threshold() -> float:
    con = sqlite3.connect(f"file:{ARTIFACTS / 'pulsetwin.db'}?mode=ro", uri=True)
    try:
        return float(con.execute("SELECT value FROM metrics WHERE plant_id='plant_a' "
                                 "AND key='budget_threshold'").fetchone()[0])
    finally:
        con.close()


@pytest.mark.parametrize("detector,evidence,scenario", [
    ("tool_wear", {"deviation_pct": -11.0}, "torque_degradation"),
    ("paint_env", {"humidity_excess_over_ambient_pct": 11.0}, "high_humidity"),
    ("starvation", {"stalled_share": 0.6, "stalled_state": "down"}, "station_breakdown"),
    ("starvation", {"stalled_share": 1.0, "stalled_state": "blocked"}, "buffer_congestion"),
])
def test_the_hypothesis_comes_from_the_firing_evidence(detector, evidence, scenario):
    got, severity, basis = CF.hypothesis(detector, evidence)
    assert got == scenario and 0 < severity <= 1 and basis
    if scenario == "buffer_congestion":
        assert severity == CF.MAX_CONGESTION  # a zero-capacity buffer deadlocks the DES


BREAKDOWN = ("plant_a", "starvation", 12, {"stalled_share": 0.6, "stalled_state": "down"})


@pytest.fixture(scope="module")
def breakdown():
    return CF.forecast(*BREAKDOWN, _threshold(), replicas=2, horizon_s=1200.0,
                       onset_s=600.0, workers=2)


def test_parallel_arms_are_bit_identical_to_serial(breakdown):
    serial = CF.forecast(*BREAKDOWN, _threshold(), replicas=2, horizon_s=1200.0,
                         onset_s=600.0, workers=1)
    assert serial["arms"] == breakdown["arms"] and serial["ranking"] == breakdown["ranking"]


def test_every_catalogue_action_for_the_condition_gets_an_arm(breakdown):
    got = {a["action_type"] for a in breakdown["arms"]}
    want = {a for a, m in MITIGATIONS.items() if "station_breakdown" in m["scenarios"]}
    assert got == want | {CF.DO_NOTHING}
    assert breakdown["provenance"] == "inferred"
    assert set(breakdown["ranking"]) == want


def test_every_number_carries_its_interval(breakdown):
    for arm in breakdown["arms"]:
        for metric in ("throughput_uph", "expected_defects", "alerts"):
            v = arm[metric]
            assert v["ci_low"] <= v["mean"] <= v["ci_high"]
        if arm["action_type"] != CF.DO_NOTHING:
            assert "distinguishable" in arm["vs_do_nothing"]
        t = arm["trajectory"]
        assert len(t["t_min"]) == len(t["p10"]) == len(t["p50"]) == len(t["p90"])
        assert all(lo <= mid <= hi for lo, mid, hi in zip(t["p10"], t["p50"], t["p90"]))


def test_a_mitigation_never_knocks_a_repaired_station_down_again():
    """The first build called engine._apply_physical_state() here, which re-forces a
    breakdown while the incident is active: -11.7 UPH, blamed on the action."""
    from offline.benchmarks.simrun import with_horizon
    from offline.config_schema import load_plant_config
    from offline.live_twin.engine import LiveTwinEngine
    from offline.live_twin.incidents import Incident

    cfg = with_horizon(load_plant_config(ROOT / "configs" / "plant_a.yaml"), 1.0)
    twin = LiveTwinEngine(cfg, seed=42, artifacts=ARTIFACTS, budget_threshold=_threshold(),
                          warmup_units=90, calibration_days=2.0)
    twin.start()
    twin.inject(Incident("station_breakdown", 0.6, station_id=12))
    rt = next(r for r in twin.sim.stations if r.cfg.id == 12)
    while rt.state == "down":
        twin.advance(300)
    CF._mitigate(twin, "station_breakdown", "increase_feed_buffer", 12)
    assert rt.state != "down"


def test_a_breakdown_says_it_is_down_and_severity_lengthens_the_repair():
    """ADR-059: the twin emits the PLC state change, and breakdown_repair_scale is live.
    Two forks of one engine draw the same repair; only the severity differs."""
    import copy

    from offline.benchmarks.simrun import with_horizon
    from offline.config_schema import load_plant_config
    from offline.live_twin.engine import LiveTwinEngine
    from offline.live_twin.incidents import Incident

    cfg = with_horizon(load_plant_config(ROOT / "configs" / "plant_a.yaml"), 1.0)
    base = LiveTwinEngine(cfg, seed=42, artifacts=ARTIFACTS, budget_threshold=_threshold(),
                          warmup_units=90, calibration_days=2.0)
    base.start()

    def downtime(severity: float) -> float:
        twin = copy.deepcopy(base)
        twin.inject(Incident("station_breakdown", severity, station_id=12))
        events = [e for e in twin.advance(4 * 3600) if e["event_type"] == "station_state"
                  and e["station_id"] == 12]
        assert events[0]["station_state"] == "down"
        up = next(e for e in events if e["previous_state"] == "down")
        return up["sim_time_s"] - events[0]["sim_time_s"]

    assert downtime(1.0) / downtime(0.1) == pytest.approx((1 + 3 * 1.0) / (1 + 3 * 0.1))


def test_a_blocked_neighbour_of_a_down_station_is_a_breakdown_there():
    scenario, _, basis = CF.hypothesis("starvation", {
        "stalled_share": 0.75, "stalled_state": "blocked", "down_stations": [15]})
    assert scenario == "station_breakdown" and "S15 reported DOWN" in basis
