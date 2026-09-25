"""The watch-vs-threshold evaluation's own logic (ADR-056). Pure, no twin run."""

from __future__ import annotations

from offline.benchmarks import agent_eval as A

RULES = {
    "takt_s": 60.0, "cooldown_s": 3600.0,
    "paint_env": {"stations": [16], "humidity_threshold_pct": 55.0, "humidity_excess_pct": 5.0},
    "tool_wear": {"signals": ["torque_nm"], "deviation_pct": 8.0,
                  "baselines": {"3|torque_nm|suv": 40.0}},
}


def _stall(sid: int, state: str = "blocked", cycle: float = 90.0) -> dict:
    return {"station_id": sid, "station_state": state, "cycle_time_s": cycle}


def test_threshold_alerts_on_every_crossing_and_deadband_suppresses_repeats():
    raw, band = A.ThresholdAlerter(RULES), A.ThresholdAlerter(RULES, cooldown_s=3600.0)
    raw_n = sum(len(raw.observe(_stall(5), t)) for t in (0.0, 10.0, 20.0))
    band_n = sum(len(band.observe(_stall(5), t)) for t in (0.0, 10.0, 20.0))
    assert raw_n == 3 and band_n == 1
    assert band.observe(_stall(5), 3601.0)  # re-arms after the cooldown


def test_threshold_uses_the_same_conditions_as_the_watch():
    a = A.ThresholdAlerter(RULES)
    assert not a.observe(_stall(5, cycle=50.0), 0.0)            # stalled but under takt
    assert not a.observe(_stall(5, state="working"), 0.0)       # slow but not stalled
    ok = {"station_id": 3, "variant": "suv", "torque_nm": 36.0}  # -10%
    assert [x.detector for x in a.observe(ok, 0.0)] == [A.TOOL_WEAR]
    # Ambient-driven humidity is NOT an excess: the rule the twin's incident cannot trip.
    humid = {"station_id": 16, "humidity_pct": 70.0, "ambient_humidity_pct": 68.0}
    assert not a.observe(humid, 0.0)


def test_a_match_needs_detector_place_and_time():
    fault = {"scenario": "station_breakdown", "station_id": 15}
    at = lambda d, s, t: A.Alert(d, s, t, {})  # noqa: E731
    assert A._is_match(at(A.STARVATION, 16, 100.0), fault, 50.0)       # neighbour counts
    assert not A._is_match(at(A.STARVATION, 17, 100.0), fault, 50.0)   # two away does not
    assert not A._is_match(at(A.STARVATION, 15, 40.0), fault, 50.0)    # before onset
    assert not A._is_match(at(A.TOOL_WEAR, 15, 100.0), fault, 50.0)    # wrong detector
    torque = {"scenario": "torque_degradation", "station_id": 3}
    assert not A._is_match(at(A.TOOL_WEAR, 4, 100.0), torque, 50.0)   # exact station only
    assert not A._is_match(at(A.STARVATION, 15, 100.0), None, 50.0)   # clean run


def test_placebo_scores_the_clean_run_against_the_fault():
    fault = {"scenario": "station_breakdown", "station_id": 15}
    clean = A.RunResult(fault=None, seed=42, units=100,
                        alerts={s: [] for s in A.SYSTEMS})
    clean.alerts["threshold"].append(A.Alert(A.STARVATION, 14, 500.0, {}))
    r = A.RunResult(fault=fault, seed=42, units=90)
    A._score_against_clean(r, clean, t_onset=100.0)
    assert r.loss_pct == 10.0
    assert r.placebo == {"watch": False, "threshold": True, "deadband": False}
