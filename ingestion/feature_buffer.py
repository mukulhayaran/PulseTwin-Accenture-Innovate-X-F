"""Real-time feature assembly: buffer a unit's raw readings as it moves through the line,
and once it reaches the SAME prediction cutoff station the offline model was trained
against (ingestion/config_lite.prediction_cutoff_station -- must match
offline/config_schema.py's PlantConfig.prediction_cutoff_station exactly), assemble the
partial feature dict and score it.

No model code is duplicated into ingestion/: this calls the running backend's own
POST /api/predict/defect over HTTP, the same endpoint a human operator's manual "what if"
probe would use. That keeps the import wall (CLAUDE.md SS2) entirely serve/'s problem and
makes ingestion/ a genuinely separate deployable, as its Dockerfile implies.

Backed by Redis (docker-compose.prod.yml gives ingestion its own REDIS_URL) so buffered
readings for units still in flight survive an ingestion-service restart.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime

import redis

from feature_contract import NOTE_CODES, signal_features

BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:7860")
TTL_SECONDS = 3600  # a unit that never reaches the trigger station expires after an hour


class FeatureBuffer:
    def __init__(self, redis_url: str, trigger_station: int, context: dict | None = None):
        self.r = redis.Redis.from_url(redis_url, decode_responses=True)
        self.trigger_station = trigger_station
        self.context = context or {}

    def _key(self, plant_id: str, unit_id: str) -> str:
        return f"pulsetwin:buffer:{plant_id}:{unit_id}"

    def add_reading(self, plant_id: str, unit_id: str, station_id: int, signal: str,
                     value: float | None, variant: str | None = None) -> None:
        if station_id > self.trigger_station or value is None or value != value:
            return
        key = self._key(plant_id, unit_id)
        prefix = f"s{station_id}_{signal}"
        history_key = f"pulsetwin:history:{plant_id}:{station_id}:{signal}"
        variant_key = f"{history_key}:variant:{variant or 'unknown'}"
        previous = [float(item) for item in self.r.lrange(history_key, 0, -1)]
        same_variant = [float(item) for item in self.r.lrange(variant_key, 0, -1)]
        self.r.hset(key, mapping=signal_features(prefix, float(value), previous, same_variant))
        self.r.rpush(history_key, value)
        self.r.ltrim(history_key, -200, -1)
        self.r.rpush(variant_key, value)
        self.r.ltrim(variant_key, -50, -1)
        self.r.expire(key, TTL_SECONDS)
        self.r.expire(history_key, TTL_SECONDS * 24)
        self.r.expire(variant_key, TTL_SECONDS * 24)

    def add_station_context(self, plant_id: str, unit_id: str, station_id: int,
                            body: dict) -> None:
        """Accumulate the flow/context features available at this station event."""
        if station_id > self.trigger_station:
            return
        key = self._key(plant_id, unit_id)
        cycle = float(body.get("cycle_time_s") or 0)
        blocked = float(body.get("blocked_before_s") or 0)
        wait = float(body.get("buffer_wait_s") or 0)
        self.r.hincrbyfloat(key, "__cycle_sum", cycle)
        self.r.hincrby(key, "__cycle_count", 1)
        self.r.hincrbyfloat(key, "total_blocked_s", blocked)
        self.r.hincrbyfloat(key, "total_buffer_wait_s", wait)
        if self.r.hget(key, "stations_blocked_count") is None:
            self.r.hset(key, "stations_blocked_count", 0)
        if self.r.hget(key, "stations_starved_count") is None:
            self.r.hset(key, "stations_starved_count", 0)
        current_max = float(self.r.hget(key, "max_station_wait_s") or 0)
        self.r.hset(key, "max_station_wait_s", max(current_max, wait))
        if body.get("station_state") == "blocked":
            self.r.hincrby(key, "stations_blocked_count", 1)
        if body.get("station_state") == "starved":
            self.r.hincrby(key, "stations_starved_count", 1)
        current_cycle_max = float(self.r.hget(key, "max_cycle_s") or 0)
        self.r.hset(key, "max_cycle_s", max(current_cycle_max, cycle))

        elapsed = body.get("elapsed_s")
        if elapsed is not None and self.r.hget(key, "__first_entry") is None:
            elapsed_f = float(elapsed)
            first_values = {"__first_entry": elapsed_f}
            if body.get("ts"):
                first_values["__first_entry_epoch"] = datetime.fromisoformat(
                    str(body["ts"])
                ).timestamp()
            self.r.hset(key, mapping=first_values)
        if body.get("lot_id") is not None:
            self.r.hset(key, "__lot_id", str(body["lot_id"]))
        exit_ts = body.get("exit_ts")
        if exit_ts:
            self.r.hset(key, "__last_exit", datetime.fromisoformat(str(exit_ts)).timestamp())
        if station_id == self.trigger_station and body.get("exit_elapsed_s") is not None:
            prediction_elapsed = float(body["exit_elapsed_s"])
            self.r.hset(key, mapping={
                "hour_of_day": (prediction_elapsed / 3600.0) % 24.0,
                "day_of_week": int(prediction_elapsed // 86400) % 7,
            })

        variant = body.get("variant")
        for value in self.context.get("variants", []):
            self.r.hset(key, f"variant_{value}", int(variant == value))
        shift_id = body.get("shift_id")
        for value in self.context.get("shifts", []):
            self.r.hset(key, f"shift_{value}", int(shift_id == value))

        zone = self.context.get("station_zones", {}).get(station_id)
        if zone and self.context.get("zone_first_station", {}).get(zone) == station_id:
            humidity = body.get("ambient_humidity_pct")
            temperature = body.get("ambient_temperature_c")
            mapping = {}
            if humidity is not None:
                mapping[f"{zone}_entry_humidity_pct"] = humidity
            if temperature is not None:
                mapping[f"{zone}_entry_temperature_c"] = temperature
            if mapping:
                self.r.hset(key, mapping=mapping)

        dwell = self.context.get("dwell_window", {})
        if station_id == int(dwell.get("from_station", -1)) and exit_ts:
            self.r.hset(key, "__dwell_from", datetime.fromisoformat(str(exit_ts)).timestamp())
        if station_id == int(dwell.get("to_station", -1)) and body.get("ts"):
            start = self.r.hget(key, "__dwell_from")
            if start is not None:
                seconds = datetime.fromisoformat(str(body["ts"])).timestamp() - float(start)
                self.r.hset(key, mapping={
                    "paint_buffer_dwell_s": seconds,
                    "paint_buffer_dwell_ratio": seconds / float(self.context["dwell_nominal_s"]),
                })
        self.r.expire(key, TTL_SECONDS)

    def add_checklist(self, plant_id: str, unit_id: str, station_id: int, body: dict) -> None:
        if station_id > self.trigger_station:
            return
        key = self._key(plant_id, unit_id)
        prefix = f"s{station_id}"
        seconds = float(body.get("checklist_time_s") or 0)
        nominal = float(self.context.get("checklist_nominals", {}).get(station_id, 1.0))
        mapping = {
            f"{prefix}_checklist_time_s": seconds,
            f"{prefix}_checklist_pass": int(body.get("checklist_pass") or 0),
            f"{prefix}_checklist_ratio": seconds / nominal,
        }
        if body.get("operator_bucket") is not None:
            mapping[f"{prefix}_operator_bucket"] = int(body["operator_bucket"])
        note = str(body.get("notes_code") or "")
        for code in NOTE_CODES:
            mapping[f"{prefix}_note_{code.lower()}"] = int(note == code)
        self.r.hset(key, mapping=mapping)
        self.r.expire(key, TTL_SECONDS)

    def add_inspection(self, plant_id: str, lot_id: str, outcome: int) -> None:
        """Advance the causal lot baseline only when an inspection becomes observable.

        The stored snapshot is calculated before appending the current result, matching
        the offline shifted rolling mean and preventing the current label leaking into
        features for a unit scored at the same instant.
        """
        history_key = f"pulsetwin:lot_outcomes:{plant_id}:{lot_id}"
        rate_key = f"pulsetwin:lot_rates:{plant_id}"
        previous = [float(item) for item in self.r.lrange(history_key, 0, -1)]
        if len(previous) >= 10:
            trailing = previous[-100:]
            self.r.hset(rate_key, lot_id, sum(trailing) / len(trailing))
        self.r.rpush(history_key, int(bool(outcome)))
        self.r.ltrim(history_key, -100, -1)
        self.r.expire(history_key, TTL_SECONDS * 24)
        self.r.expire(rate_key, TTL_SECONDS * 24)

    def maybe_score(self, plant_id: str, unit_id: str, station_id: int) -> dict | None:
        """Call on every station-exit event. Fires exactly once per unit, when it exits
        the trigger station -- mirroring the offline pipeline's own cutoff."""
        if station_id != self.trigger_station:
            return None
        key = self._key(plant_id, unit_id)
        raw = self.r.hgetall(key)
        self.r.delete(key)
        if not raw:
            return None
        first_entry = raw.get("__first_entry")
        last_exit = raw.get("__last_exit")
        if first_entry is not None and last_exit is not None:
            # __last_exit is epoch time while elapsed_s is relative to the synthetic
            # origin, so total dwell is derived from event timestamps when both exist.
            # The publisher's ts is retained below for that purpose.
            first_epoch = raw.get("__first_entry_epoch")
            if first_epoch is not None:
                raw["total_dwell_s"] = str(float(last_exit) - float(first_epoch))
        count = int(float(raw.get("__cycle_count") or 0))
        if count:
            raw["mean_cycle_s"] = str(float(raw.get("__cycle_sum") or 0) / count)
        lot_id = raw.get("__lot_id")
        if lot_id is not None:
            lot_rate = self.r.hget(f"pulsetwin:lot_rates:{plant_id}", lot_id)
            if lot_rate is not None:
                raw["lot_trailing_defect_rate"] = str(lot_rate)
        features = {k: float(v) for k, v in raw.items() if not k.startswith("__")}
        return self._predict(plant_id, unit_id, features)

    def _predict(self, plant_id: str, unit_id: str, features: dict[str, float]) -> dict | None:
        body = json.dumps({
            "plant_id": plant_id,
            "features": features,
            "source": "live_synthetic",
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{BACKEND_URL}/api/predict/defect", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"[feature_buffer] prediction call failed for {unit_id}: {e}")
            return None
        result["unit_id"] = unit_id
        result["plant_id"] = plant_id
        result["n_features_observed"] = len(features)
        return result
