"""Golden parity between the shared live formulas and the offline feature artifact."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from feature_contract import signal_features
from ingestion.config_lite import feature_context
from ingestion.feature_buffer import FeatureBuffer
from offline.config_schema import load_plant_config
from serve.scorer import Scorer


ROOT = Path(__file__).resolve().parents[1]


class FakeRedis:
    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}

    def hset(self, name, key=None, value=None, mapping=None):
        target = self.hashes.setdefault(str(name), {})
        if mapping is not None:
            target.update({str(k): str(v) for k, v in mapping.items()})
        elif key is not None:
            target[str(key)] = str(value)

    def hget(self, name, key):
        return self.hashes.get(str(name), {}).get(str(key))

    def hgetall(self, name):
        return dict(self.hashes.get(str(name), {}))

    def hincrbyfloat(self, name, key, amount):
        value = float(self.hget(name, key) or 0) + float(amount)
        self.hset(name, key, value)
        return value

    def hincrby(self, name, key, amount):
        value = int(float(self.hget(name, key) or 0)) + int(amount)
        self.hset(name, key, value)
        return value

    def rpush(self, name, value):
        self.lists.setdefault(str(name), []).append(str(value))

    def lrange(self, name, start, stop):
        values = self.lists.get(str(name), [])
        if stop == -1:
            stop = len(values) - 1
        return values[start:stop + 1]

    def ltrim(self, name, start, stop):
        values = self.lists.get(str(name), [])
        if start < 0:
            start = max(0, len(values) + start)
        if stop == -1:
            stop = len(values) - 1
        self.lists[str(name)] = values[start:stop + 1]

    def expire(self, *_args):
        return True

    def delete(self, name):
        self.hashes.pop(str(name), None)
        self.lists.pop(str(name), None)


@pytest.mark.parametrize("plant", ["plant_a"])
def test_live_signal_features_and_prediction_match_offline_golden_history(plant: str):
    cfg = load_plant_config(f"configs/{plant}.yaml")
    telemetry = pd.read_parquet(ROOT / "data" / plant / "telemetry.parquet")
    offline = pd.read_parquet(ROOT / "data" / plant / "features.parquet")
    cutoff = cfg.prediction_cutoff_station()

    # A late unit has enough strictly-prior history for every rolling window.
    target_seq = int(offline.index[-25])
    target_rows = telemetry[
        (telemetry["unit_seq"] == target_seq) & (telemetry["station_id"] <= cutoff)
    ]
    live_values: dict[str, float] = {}
    for row in target_rows.itertuples():
        station_rows = telemetry[
            (telemetry["station_id"] == row.station_id)
            & (telemetry["entry_ts"] < row.entry_ts)
        ].sort_values("entry_ts")
        for signal in cfg.station(int(row.station_id)).sensors:
            value = getattr(row, signal)
            if pd.isna(value):
                continue
            previous = station_rows[signal].dropna().tolist()
            same_variant = station_rows.loc[
                station_rows["variant"] == row.variant, signal
            ].dropna().tolist()
            live_values.update(signal_features(
                f"s{int(row.station_id)}_{signal}", float(value), previous, same_variant
            ))

    assert len(live_values) > 20
    for name, value in live_values.items():
        expected = float(offline.loc[target_seq, name])
        assert value == pytest.approx(expected, rel=2e-5, abs=2e-5), name

    # Replacing the offline values with values generated through the live contract must
    # leave the registered numpy scorer's prediction effectively unchanged.
    scorer = Scorer(ROOT / "artifacts" / f"model_{plant}.npz")
    offline_features = offline.loc[target_seq].to_dict()
    live_equivalent = dict(offline_features)
    live_equivalent.update(live_values)
    p_offline = float(scorer.predict(scorer.vector_from(offline_features).reshape(1, -1))[0])
    p_live = float(scorer.predict(scorer.vector_from(live_equivalent).reshape(1, -1))[0])
    assert np.isclose(p_live, p_offline, rtol=1e-6, atol=1e-6)


@pytest.mark.parametrize("plant", ["plant_a"])
def test_complete_online_feature_vector_matches_offline_contract(plant: str):
    """Exercise the actual Redis-backed assembler, not a parallel feature reimplementation."""
    cfg = load_plant_config(f"configs/{plant}.yaml")
    data = ROOT / "data" / plant
    telemetry = pd.read_parquet(data / "telemetry.parquet")
    checklists = pd.read_parquet(data / "checklists.parquet")
    inspections = pd.read_parquet(data / "inspections.parquet")
    offline = pd.read_parquet(data / "features.parquet")
    cutoff = cfg.prediction_cutoff_station()
    target_seq = int(offline.index[-25])
    target_rows = telemetry[
        (telemetry["unit_seq"] == target_seq) & (telemetry["station_id"] <= cutoff)
    ].sort_values("entry_ts")
    assert not target_rows.empty

    fake = FakeRedis()
    buffer = FeatureBuffer.__new__(FeatureBuffer)
    buffer.r = fake
    buffer.trigger_station = cutoff
    buffer.context = feature_context(plant)
    captured: dict[str, float] = {}
    buffer._predict = lambda _plant, _unit, features: captured.update(features) or {"features": features}

    unit_id = str(inspections.loc[inspections["unit_seq"] == target_seq, "unit_id"].iloc[0])
    variant = str(target_rows.iloc[0]["variant"])
    lot_id = str(target_rows.iloc[0]["lot_id"])
    prediction_ts = float(target_rows.loc[target_rows["station_id"] == cutoff, "exit_ts"].iloc[0])

    # Prime the causal lot baseline with only outcomes observable before prediction.
    visible_inspections = inspections[
        (inspections["lot_id"].astype(str) == lot_id)
        & (inspections["inspection_ts"] < prediction_ts)
    ].sort_values("inspection_ts")
    for row in visible_inspections.itertuples():
        buffer.add_inspection(plant, lot_id, int(row.defect_detected))

    for row in target_rows.itertuples():
        station_id = int(row.station_id)
        body = {
            "ts": datetime.fromtimestamp(float(row.entry_ts), timezone.utc).isoformat(),
            "exit_ts": datetime.fromtimestamp(float(row.exit_ts), timezone.utc).isoformat(),
            "elapsed_s": float(row.entry_ts),
            "exit_elapsed_s": float(row.exit_ts),
            "cycle_time_s": float(row.cycle_s),
            "blocked_before_s": float(row.blocked_before_s),
            "buffer_wait_s": float(row.buffer_wait_s),
            "station_state": str(row.prior_state),
            "ambient_humidity_pct": float(row.ambient_humidity_pct),
            "ambient_temperature_c": float(row.ambient_temperature_c),
            "variant": variant,
            "lot_id": lot_id,
            "shift_id": str(row.shift_id),
        }
        buffer.add_station_context(plant, unit_id, station_id, body)

        previous_rows = telemetry[
            (telemetry["station_id"] == station_id) & (telemetry["entry_ts"] < row.entry_ts)
        ].sort_values("entry_ts")
        for signal in cfg.station(station_id).sensors:
            value = getattr(row, signal)
            if pd.isna(value):
                continue
            history_key = f"pulsetwin:history:{plant}:{station_id}:{signal}"
            variant_key = f"{history_key}:variant:{variant}"
            for previous in previous_rows[signal].dropna().tolist()[-200:]:
                fake.rpush(history_key, previous)
            for previous in previous_rows.loc[
                previous_rows["variant"].astype(str) == variant, signal
            ].dropna().tolist()[-50:]:
                fake.rpush(variant_key, previous)
            buffer.add_reading(plant, unit_id, station_id, signal, float(value), variant)

    target_checklists = checklists[
        (checklists["unit_seq"] == target_seq) & (checklists["station_id"] <= cutoff)
    ].copy()
    if not target_checklists.empty:
        target_checklists["operator_bucket"] = (
            pd.util.hash_pandas_object(target_checklists["operator_id"], index=False)
            % cfg.operators.operator_bucket_count
        ).astype(int)
        for row in target_checklists.itertuples():
            buffer.add_checklist(plant, unit_id, int(row.station_id), {
                "checklist_time_s": float(row.checklist_time_s),
                "checklist_pass": int(row.checklist_pass),
                "notes_code": str(row.notes_code),
                "operator_bucket": int(row.operator_bucket),
            })

    result = buffer.maybe_score(plant, unit_id, cutoff)
    assert result is not None
    scorer = Scorer(ROOT / "artifacts" / f"model_{plant}.npz")
    expected = scorer.vector_from(offline.loc[target_seq].to_dict())
    actual = scorer.vector_from(captured)
    nan_mismatches = [
        scorer.feature_names[index]
        for index in np.where(np.isnan(actual) != np.isnan(expected))[0]
    ]
    assert not nan_mismatches, f"missing/premature live features: {nan_mismatches}"
    np.testing.assert_allclose(actual, expected, rtol=3e-5, atol=3e-5, equal_nan=True)
    expected_p = float(scorer.predict(expected.reshape(1, -1))[0])
    actual_p = float(scorer.predict(actual.reshape(1, -1))[0])
    assert actual_p == pytest.approx(expected_p, rel=1e-6, abs=1e-6)
