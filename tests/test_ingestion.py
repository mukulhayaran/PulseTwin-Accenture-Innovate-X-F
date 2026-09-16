"""ingestion/ unit tests -- topic schema, data-quality scoring, dedup, and the config_lite
formulas that MUST match offline/config_schema.py exactly (a real MQTT broker is not
required for any of this; the wire protocol itself is paho-mqtt's problem, not ours).
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytest.importorskip("yaml")

from ingestion.unified_namespace import (  # noqa: E402
    checklist_topic,
    control_dropout_topic,
    inspection_topic,
    parse_topic,
    state_topic,
    telemetry_topic,
    unit_entered_topic,
    unit_exited_topic,
)


# --------------------------------------------------------------------- topic round-trip


@pytest.mark.parametrize("builder,kind", [
    (lambda: telemetry_topic("plant_a", "body", 8), "telemetry"),
    (lambda: state_topic("plant_a", "body", 8), "state"),
    (lambda: checklist_topic("plant_a", "paint", 15), "checklist"),
])
def test_station_topics_round_trip(builder, kind):
    topic = builder()
    parsed = parse_topic(topic)
    assert parsed is not None
    assert parsed.kind == kind
    assert parsed.plant_id == "plant_a"
    assert parsed.station_id in (8, 15)


def test_unit_topics_round_trip():
    assert parse_topic(unit_entered_topic("plant_a", "U001")).kind == "entered"
    assert parse_topic(unit_exited_topic("plant_a", "U001")).unit_id == "U001"
    assert parse_topic(inspection_topic("plant_a", "U001")).kind == "inspection"


def test_control_dropout_topic_parses():
    parsed = parse_topic(control_dropout_topic("plant_b"))
    assert parsed.kind == "dropout"
    assert parsed.plant_id == "plant_b"


def test_unparseable_topic_returns_none():
    assert parse_topic("not/a/pulsetwin/topic") is None
    assert parse_topic("pulsetwin/plant_a/body/not-a-number/telemetry") is None


# ------------------------------------------------------------------------ config_lite


def test_prediction_cutoff_matches_offline_config_schema():
    """The real-time feature buffer MUST trigger a prediction at the same station the
    offline model was trained to cut off at, or the served feature vector shape drifts
    from what serve/scorer.py expects."""
    from ingestion.config_lite import prediction_cutoff_station
    from offline.config_schema import load_plant_config

    for plant in ("plant_a", "plant_b"):
        cfg = load_plant_config(f"{plant}.yaml")
        assert prediction_cutoff_station(plant) == cfg.prediction_cutoff_station()


# -------------------------------------------------------------------- data quality


def test_valid_reading_ranges():
    from ingestion.data_quality import is_valid_reading

    assert is_valid_reading("torque_nm", 45.0)
    assert not is_valid_reading("torque_nm", -999.0)
    assert not is_valid_reading("vibration_mm_s", float("nan"))
    assert not is_valid_reading("humidity_pct", 150.0)
    assert is_valid_reading("unknown_signal", 1e9)  # no configured range -- passes through


def test_tracker_scores_a_healthy_station():
    from ingestion.data_quality import DataQualityTracker

    tracker = DataQualityTracker(window_s=300.0)
    t = 1000.0
    for i in range(20):
        tracker.record("plant_a", 8, "vibration_mm_s", 3.0, t + i * 10, t + i * 10 + 0.5, 10.0)
    score = tracker.score("plant_a", 8)
    assert score["validity"] == 1.0
    assert score["timeliness"] == 1.0
    assert score["overall"] > 0.9


def test_tracker_flags_late_arrivals():
    from ingestion.data_quality import DataQualityTracker, LATE_FACTOR

    tracker = DataQualityTracker(window_s=300.0)
    nominal = 10.0
    tracker.record("plant_a", 8, "vibration_mm_s", 3.0, event_ts=0.0,
                    arrival_ts=nominal * LATE_FACTOR * 3, nominal_cycle_s=nominal)
    score = tracker.score("plant_a", 8)
    assert score["timeliness"] == 0.0


def test_tracker_excludes_invalid_readings_from_validity():
    from ingestion.data_quality import DataQualityTracker

    tracker = DataQualityTracker()
    for i in range(5):
        tracker.record("plant_a", 3, "torque_nm", -999.0, i, i, 10.0)
    score = tracker.score("plant_a", 3)
    assert score["validity"] == 0.0


def test_dropout_detected_after_factor_times_nominal_cycle():
    from ingestion.data_quality import DataQualityTracker

    tracker = DataQualityTracker()
    tracker.record("plant_a", 8, "vibration_mm_s", 3.0, event_ts=0.0, arrival_ts=0.0,
                    nominal_cycle_s=10.0)
    dropouts_before = tracker.check_dropouts(now=15.0)   # well within 3x10s
    assert dropouts_before == []

    dropouts_after = tracker.check_dropouts(now=40.0)    # past 3x10s = 30s
    assert len(dropouts_after) == 1
    assert dropouts_after[0]["station_id"] == 8
    assert dropouts_after[0]["signal"] == "vibration_mm_s"


def test_dropout_clears_once_a_fresh_reading_arrives():
    from ingestion.data_quality import DataQualityTracker

    tracker = DataQualityTracker()
    tracker.record("plant_a", 8, "vibration_mm_s", 3.0, 0.0, 0.0, 10.0)
    assert tracker.check_dropouts(now=40.0)

    tracker.record("plant_a", 8, "vibration_mm_s", 3.1, 41.0, 41.0, 10.0)
    assert tracker.check_dropouts(now=42.0) == []
