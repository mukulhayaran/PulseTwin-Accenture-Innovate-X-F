"""Bake each plant's proactive-watch rules into artifacts/watch_rules_<plant>.json (ADR-051).

serve/ cannot parse YAML (the import wall, CLAUDE.md §2), so -- exactly like
`offline/build_knowledge_index.py` -- the reviewable source stays in configs/*.yaml and
this script writes the thin JSON that `serve/watch.py` reads at runtime. Everything is
derived from the config, with no station or zone literals: which stations report humidity
comes from `zones[].zone_sensors`, which report torque or weld current from
`stations[].sensors`.

Tool-wear BASELINES are also baked here, from the plant's committed replay stream: the
per-(station, signal, variant) mean of normal operation. A baseline learned at runtime
from the run's own first events cannot see a degradation that was already present when
the run started -- it learns the degraded value as "normal" and never fires.

    python -m offline.build_watch_rules
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from offline.config_schema import PlantConfig, load_plant_config

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "configs"
ARTIFACTS = ROOT / "artifacts"

HUMIDITY_SIGNAL = "humidity_pct"


def tool_wear_baselines(stream: Path, signals: list[str]) -> dict[str, float]:
    """Mean of each signal per "station|signal|variant" over the replay stream."""
    sums: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
    with stream.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            for sig in signals:
                if r.get(sig) is not None:
                    acc = sums[f"{r['station_id']}|{sig}|{r.get('variant') or ''}"]
                    acc[0] += float(r[sig])
                    acc[1] += 1
    return {k: round(total / n, 4) for k, (total, n) in sorted(sums.items()) if n}


def build_rules(cfg: PlantConfig, source: str, stream: Path) -> dict[str, Any]:
    w = cfg.watch
    ids = cfg.station_ids
    humid_zones = {z.id for z in cfg.zones if HUMIDITY_SIGNAL in z.zone_sensors}
    return {
        "plant_id": cfg.plant_id,
        "source": source,
        "takt_s": round(cfg.takt_s, 3),
        "window_events": w.window_events,
        "cooldown_s": w.cooldown_s,
        "max_open_investigations": w.max_open_investigations,
        "starvation": {"stalled_share": w.starvation.stalled_share},
        "paint_env": {
            "humidity_excess_pct": w.paint_env.humidity_excess_pct,
            "humidity_threshold_pct": cfg.defect_physics.mode_a.humidity_threshold_pct,
            "stations": [s.id for s in cfg.stations if s.zone in humid_zones],
        },
        "tool_wear": {
            "signals": list(w.tool_wear.signals),
            "deviation_pct": w.tool_wear.deviation_pct,
            "baselines": tool_wear_baselines(stream, list(w.tool_wear.signals)),
        },
        "stations": {
            str(s.id): {
                "name": s.name,
                "zone": s.zone,
                "sensors": list(s.sensors),
                "buffer_capacity_after": s.buffer_capacity_after,
                "next_station": ids[i + 1] if i + 1 < len(ids) else None,
            }
            for i, s in enumerate(cfg.stations)
        },
    }


def main() -> int:
    ARTIFACTS.mkdir(exist_ok=True)
    for path in sorted(CONFIGS.glob("plant_*.yaml")):
        cfg = load_plant_config(path)
        rules = build_rules(cfg, f"configs/{path.name}",
                            ARTIFACTS / f"stream_{cfg.plant_id}.jsonl")
        out = ARTIFACTS / f"watch_rules_{cfg.plant_id}.json"
        out.write_text(json.dumps(rules, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out.relative_to(ROOT)} "
              f"({len(rules['paint_env']['stations'])} humidity stations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
