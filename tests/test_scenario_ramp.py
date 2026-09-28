"""LOOP criterion D: a live scenario develops gradually. Congestion's blocked share grows
after onset instead of jumping to 100 % on the first event; a breakdown stays a step."""

from datetime import datetime, timedelta
from types import SimpleNamespace

from serve.live_simulation import SCENARIO_RAMP_S, LiveSimulationManager

T0 = datetime(2026, 1, 28, 6, 0, 0)
STATION = 19


def _run(scenario: str, onset_s: float = 600.0, horizon_s: float = 3000.0, step_s: float = 5.0):
    control = {"scenario": scenario, "severity": 0.7, "affected_station": STATION,
               "affected_lot": None, "incident_start_s": onset_s, "first_event_ts": None,
               "mitigations": []}
    stub = SimpleNamespace(_raise_risk=lambda *a, **k: None)
    out = []
    n = int(horizon_s / step_s)
    for seq in range(1, n + 1):
        ts = (T0 + timedelta(seconds=seq * step_s)).isoformat()
        rec = {"ts": ts, "station_id": STATION, "unit_id": f"U{seq:06d}", "cycle_time_s": 50.0,
               "station_state": "working"}
        ev = LiveSimulationManager._transform(stub, rec, "RUN", "plant_a", 42, seq, control)
        out.append(((seq * step_s) - onset_s, ev["station_state"]))
    return out


def _share(events, lo, hi, state):
    window = [s for t, s in events if lo <= t < hi]
    return sum(s == state for s in window) / len(window)


def test_congestion_builds_up_instead_of_stepping():
    ev = _run("buffer_congestion")
    before = _share(ev, -600, 0, "blocked")
    first = _share(ev, 0, SCENARIO_RAMP_S / 4, "blocked")
    late = _share(ev, SCENARIO_RAMP_S, SCENARIO_RAMP_S + 900, "blocked")
    assert before == 0.0
    assert first < 0.3, first            # not an instant 100 %
    assert late > 0.9, late              # full effect once the ramp completes
    assert first < _share(ev, SCENARIO_RAMP_S / 2, SCENARIO_RAMP_S, "blocked") < late


def test_breakdown_is_a_step_change():
    ev = _run("station_breakdown")
    assert _share(ev, 0, 60, "down") > 0.9
