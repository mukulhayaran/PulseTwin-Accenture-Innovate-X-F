"""Minimal, dependency-light config reads for ingestion/.

ingestion/ is a separate deployable from offline/ and serve/ (its Dockerfile copies only
ingestion/, configs/ and artifacts/ -- see ingestion/Dockerfile) and does not carry
offline.config_schema's pydantic validation. It reads the same YAML with plain
`yaml.safe_load` for the handful of fields it actually needs: station zones (for MQTT
topic routing) and the prediction cutoff station (for real-time feature assembly).

The cutoff formula below MUST match offline/config_schema.py's
`PlantConfig.prediction_cutoff_station` exactly, or the real-time pipeline would trigger a
prediction at a different point in the line than the offline-trained model expects.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_stations(plant_id: str) -> list[dict]:
    cfg = yaml.safe_load((ROOT / "configs" / f"{plant_id}.yaml").read_text(encoding="utf-8"))
    return cfg["stations"]


def station_zones(plant_id: str) -> dict[int, str]:
    return {int(s["id"]): str(s["zone"]) for s in load_stations(plant_id)}


def prediction_cutoff_station(plant_id: str) -> int:
    """Mirrors offline/config_schema.py's PlantConfig.prediction_cutoff_station: two
    thirds along the line, by station count, not a hardcoded ID (CLAUDE.md SS1)."""
    stations = load_stations(plant_id)
    ids = sorted(int(s["id"]) for s in stations)
    return ids[int(round(0.667 * len(ids))) - 1]
