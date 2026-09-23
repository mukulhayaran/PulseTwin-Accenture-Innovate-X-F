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


def load_config(plant_id: str) -> dict:
    return yaml.safe_load((ROOT / "configs" / f"{plant_id}.yaml").read_text(encoding="utf-8"))


def load_stations(plant_id: str) -> list[dict]:
    return load_config(plant_id)["stations"]


def feature_context(plant_id: str) -> dict:
    cfg = load_config(plant_id)
    stations = cfg["stations"]
    zones = {
        str(zone["id"]): min(int(station_id) for station_id in zone["stations"])
        for zone in cfg["zones"]
    }
    station_zones_map = {int(station["id"]): str(station["zone"]) for station in stations}
    manual_nominals = {
        int(station["id"]): float(station.get("checklist_nominal_s") or 1.0)
        for station in stations if station.get("checklist_nominal_s") is not None
    }
    mode_a = cfg["defect_physics"]["mode_a"]
    return {
        "variants": [str(value["id"]) for value in cfg["variants"]],
        "shifts": [str(value["id"]) for value in cfg["shifts"]],
        "zone_first_station": zones,
        "station_zones": station_zones_map,
        "checklist_nominals": manual_nominals,
        "dwell_window": mode_a["dwell_window"],
        "dwell_nominal_s": float(mode_a["dwell_nominal_s"]),
    }


def station_zones(plant_id: str) -> dict[int, str]:
    return {int(s["id"]): str(s["zone"]) for s in load_stations(plant_id)}


def prediction_cutoff_station(plant_id: str) -> int:
    """Mirrors offline/config_schema.py's PlantConfig.prediction_cutoff_station: two
    thirds along the line, by station count, not a hardcoded ID (CLAUDE.md SS1)."""
    stations = load_stations(plant_id)
    ids = sorted(int(s["id"]) for s in stations)
    return ids[int(round(0.667 * len(ids))) - 1]
