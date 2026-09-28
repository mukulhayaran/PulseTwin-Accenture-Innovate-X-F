"""The proactive watch (ADR-051): each detector fires on its own injected scenario and
stays silent on a normal run.

Events are produced by the live simulator's REAL `_transform` -- the same scenario
physics the demo injects -- over the committed replay stream, not by a hand-copied
imitation of it that could drift.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from offline.build_watch_rules import build_rules
from offline.config_schema import load_plant_config
from serve.live_simulation import LiveSimulationManager
from serve.watch import PAINT_ENV, STARVATION, TOOL_WEAR, LineWatch, load_rules

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
PLANTS = ["plant_a"]


def _events(plant: str, scenario: str, station: int | None, limit: int,
            severity: float = 0.7):
    fake = SimpleNamespace(plants={plant: {"budget_threshold": 1.0}},
                           _raise_risk=lambda event, sev: None)
    control = {"scenario": scenario, "severity": severity,
               "affected_station": station or 0, "affected_lot": None,
               "incident_start_s": 0.0, "first_event_ts": None}
    with open(ARTIFACTS / f"stream_{plant}.jsonl", encoding="utf-8") as f:
        for seq, line in enumerate(f, start=1):
            if seq > limit:
                return
            yield LiveSimulationManager._transform(
                fake, json.loads(line), "RUN", plant, 42, seq, control)


def _firings(plant: str, scenario: str, station: int | None = None, limit: int = 2000):
    watch = LineWatch(load_rules(ARTIFACTS, plant))
    out = []
    for event in _events(plant, scenario, station, limit):
        out.extend(watch.observe(event))
    return out


def _busiest_station_with(plant: str, signal: str) -> int:
    rules = load_rules(ARTIFACTS, plant)
    return min(int(k) for k, v in rules["stations"].items() if signal in v["sensors"])


@pytest.mark.parametrize("plant", PLANTS)
def test_the_rules_json_is_exactly_what_the_yaml_says(plant: str) -> None:
    """serve/ reads the JSON because it cannot parse YAML; this keeps the two in step."""
    cfg = load_plant_config(ROOT / "configs" / f"{plant}.yaml")
    assert load_rules(ARTIFACTS, plant) == build_rules(
        cfg, f"configs/{plant}.yaml", ARTIFACTS / f"stream_{plant}.jsonl")


@pytest.mark.parametrize("plant", PLANTS)
def test_a_normal_run_raises_no_starvation_or_paint_firing(plant: str) -> None:
    detectors = {f.detector for f in _firings(plant, "normal")}
    assert STARVATION not in detectors
    assert PAINT_ENV not in detectors


@pytest.mark.parametrize("plant", PLANTS)
def test_on_a_normal_run_tool_wear_fires_only_at_mode_c_stations(plant: str) -> None:
    """Measured: the only normal-run firings are the designed Mode C torque deficits --
    true positives the simulator put there, not noise."""
    cfg = load_plant_config(ROOT / "configs" / f"{plant}.yaml")
    stations = {f.station_id for f in _firings(plant, "normal", limit=5000)
                if f.detector == TOOL_WEAR}
    assert stations <= set(cfg.defect_physics.mode_c.contributing_stations)


@pytest.mark.parametrize("plant", PLANTS)
def test_high_humidity_fires_the_paint_detector_latched(plant: str) -> None:
    fired = [f for f in _firings(plant, "high_humidity") if f.detector == PAINT_ENV]
    assert fired
    # Latched, with a cooldown: a persisting episode may re-fire, but never inside it.
    cooldown = load_rules(ARTIFACTS, plant)["cooldown_s"]
    stamps = [datetime.fromisoformat(f.ts).timestamp() for f in fired]
    assert all(b - a >= cooldown for a, b in zip(stamps, stamps[1:]))
    ev = fired[0].evidence
    assert ev["humidity_excess_over_ambient_pct"] >= ev["excess_threshold_pct"]


@pytest.mark.parametrize("plant", PLANTS)
@pytest.mark.parametrize("scenario", ["station_breakdown", "buffer_congestion"])
def test_a_stalled_station_fires_starvation_there(plant: str, scenario: str) -> None:
    station = _busiest_station_with(plant, "torque_nm")
    fired = [f for f in _firings(plant, scenario, station) if f.detector == STARVATION]
    assert [f.station_id for f in fired] == [station]
    assert fired[0].evidence["stalled_share"] >= 0.5


@pytest.mark.parametrize("plant", PLANTS)
def test_torque_degradation_fires_tool_wear_at_that_station(plant: str) -> None:
    station = _busiest_station_with(plant, "torque_nm")
    fired = [f for f in _firings(plant, "torque_degradation", station)
             if f.detector == TOOL_WEAR and f.station_id == station]
    assert fired and fired[0].evidence["signal"] == "torque_nm"


def test_recovery_is_judged_only_on_events_after_a_mitigation() -> None:
    watch = LineWatch(load_rules(ARTIFACTS, "plant_a"))
    station = _busiest_station_with("plant_a", "torque_nm")
    events = list(_events("plant_a", "station_breakdown", station, 800))
    for e in events:
        watch.observe(e)
    assert not watch.is_clear(STARVATION, station)
    watch.mark_applied(STARVATION, station)
    for e in _events("plant_a", "normal", None, 800):
        watch.observe(e)
    assert watch.is_clear(STARVATION, station)


# ------------------------------------------------------------------ silence (ADR-057)

@pytest.mark.parametrize("plant", PLANTS)
def test_the_whole_normal_stream_never_goes_silent(plant: str) -> None:
    """The threshold was set above the longest normal gap; this is that measurement."""
    watch = LineWatch(load_rules(ARTIFACTS, plant))
    assert watch.silence_s > 0
    with open(ARTIFACTS / f"stream_{plant}.jsonl", encoding="utf-8") as f:
        silent = [x for line in f for x in watch.observe(json.loads(line))
                  if x.evidence.get("signal") == "silence"]
    assert silent == []


def test_a_stopped_line_fires_once_at_the_downstream_end_of_the_silence() -> None:
    rules = load_rules(ARTIFACTS, "plant_a")
    watch = LineWatch(rules)
    t0 = datetime(2026, 1, 5, 8, 0).timestamp()
    ev = lambda sid, t, state="working": {  # noqa: E731
        "station_id": sid, "ts": datetime.fromtimestamp(t).isoformat(),
        "station_state": state, "cycle_time_s": 60.0}
    for sid in (1, 2, 3, 4, 5):
        watch.observe(ev(sid, t0))
    # S1-S3 freeze together (a blockage at S3 holds everything upstream); S4-S5 run on.
    watch.observe(ev(3, t0 + 1, "blocked"))
    for k in range(1, 5):
        watch.observe(ev(4, t0 + 60 * k))
        watch.observe(ev(5, t0 + 60 * k))
    fired = watch.tick(t0 + watch.silence_s + 5)
    assert [(f.station_id, f.evidence["signal"]) for f in fired] == [(3, "silence")]
    assert fired[0].evidence["stalled_state"] == "blocked"
    assert fired[0].evidence["silent_stations"] == 3  # S4-S5 spoke 240 s later
    assert watch.tick(t0 + watch.silence_s + 600) == []  # latched: one stoppage, one firing
    for sid in (1, 2, 3, 4, 5):                          # the line restarts...
        watch.observe(ev(sid, t0 + 2 * watch.silence_s))
    assert watch.tick(t0 + 2 * watch.silence_s + 10) == []


def test_silence_blames_the_station_that_says_it_is_down() -> None:
    """ADR-059: a state tag outranks the downstream-end heuristic, and it does not reset
    the station's silence -- reporting DOWN is not producing."""
    watch = LineWatch(load_rules(ARTIFACTS, "plant_a"))
    t0 = datetime(2026, 1, 5, 8, 0).timestamp()
    iso = lambda t: datetime.fromtimestamp(t).isoformat()  # noqa: E731
    for sid in (1, 2, 3, 4, 5):
        watch.observe({"station_id": sid, "ts": iso(t0), "station_state": "working",
                       "cycle_time_s": 60.0})
    watch.observe({"event_type": "station_state", "station_id": 2, "ts": iso(t0 + 5),
                   "station_state": "down"})
    fired = watch.tick(t0 + watch.silence_s + 30)
    assert [f.station_id for f in fired] == [2]
    assert fired[0].evidence["down_stations"] == [2]
    assert fired[0].evidence["stalled_state"] == "down"

