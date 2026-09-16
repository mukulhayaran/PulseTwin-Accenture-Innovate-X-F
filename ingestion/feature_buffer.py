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

import redis

BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:7860")
TTL_SECONDS = 3600  # a unit that never reaches the trigger station expires after an hour


class FeatureBuffer:
    def __init__(self, redis_url: str, trigger_station: int):
        self.r = redis.Redis.from_url(redis_url, decode_responses=True)
        self.trigger_station = trigger_station

    def _key(self, plant_id: str, unit_id: str) -> str:
        return f"pulsetwin:buffer:{plant_id}:{unit_id}"

    def add_reading(self, plant_id: str, unit_id: str, station_id: int, signal: str,
                     value: float | None) -> None:
        if station_id > self.trigger_station or value is None or value != value:
            return
        key = self._key(plant_id, unit_id)
        field = f"s{station_id}_{signal}"
        self.r.hset(key, field, value)
        self.r.expire(key, TTL_SECONDS)

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
        features = {k: float(v) for k, v in raw.items()}
        return self._predict(plant_id, unit_id, features)

    def _predict(self, plant_id: str, unit_id: str, features: dict[str, float]) -> dict | None:
        body = json.dumps({"plant_id": plant_id, "features": features}).encode("utf-8")
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
