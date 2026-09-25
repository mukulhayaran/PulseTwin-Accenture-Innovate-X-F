"""The proactive watch: deterministic risk detection on the live event stream (ADR-051).

Fed every event the live-simulation producer emits. When a detector crosses its
config-driven threshold it returns a `Firing`; the caller starts the matching agent, which
may PROPOSE an action a human then approves. The watch itself decides nothing and changes
nothing -- it is the "something is wrong here" signal, measured, with its evidence.

Three detectors, one per use case, each keyed on what the stream actually carries
(thresholds measured against the replay stream and documented in configs/plant_*.yaml):

  starvation  a station's rolling share of `down`/`blocked` events with its mean cycle
              time above takt -- the next station will draw its buffer dry.
              ALSO (ADR-057) a station that has emitted NO event for `silence_s`: a
              deadlocked line goes quiet, and a window over events never sees that.
              Silence is judged on every event from ANY station, and on `tick()` for a
              driver whose clock runs when no event arrives at all.
  paint_env   paint-zone humidity in EXCESS of ambient, above the Mode A threshold --
              moisture the ambient does not explain (the zone climate is not holding).
  tool_wear   torque / weld current drifting from the station's per-variant baseline
              (baked offline from the replay stream) by more than the plant's threshold.

Every detector latches while its condition holds (one firing per episode, not one per
event), re-arms only once the condition clears, and respects a per-key cooldown in
SIMULATED seconds. `mark_applied` clears a key's window so recovery is judged only on
events that arrived after a mitigation.

Import wall: stdlib only.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

STARVATION = "starvation"
PAINT_ENV = "paint_env"
TOOL_WEAR = "tool_wear"
DETECTORS = (STARVATION, PAINT_ENV, TOOL_WEAR)

STALLED_STATES = frozenset({"down", "blocked"})
PAINT_KEY = 0  # paint_env is zone-wide, not per station
LINE_KEY = 0   # silence is line-wide: one stoppage, one firing


@dataclass(frozen=True)
class Firing:
    detector: str
    station_id: int
    ts: str
    evidence: dict[str, Any]


@dataclass
class _Key:
    window: deque = field(default_factory=deque)
    active: bool = False          # latched: fired, condition still holds
    last_fired: float = float("-inf")


def load_rules(artifacts: Path, plant_id: str) -> dict[str, Any] | None:
    path = artifacts / f"watch_rules_{plant_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # no rules built: the watch is simply off, never a crash


def _mean(values: Any) -> float:
    values = list(values)
    return sum(values) / len(values)


def _epoch(ts: str) -> float:
    return datetime.fromisoformat(ts).timestamp()


class LineWatch:
    def __init__(self, rules: dict[str, Any]):
        self.rules = rules
        self.n = int(rules["window_events"])
        self.cooldown = float(rules["cooldown_s"])
        self.stations: dict[int, dict[str, Any]] = {
            int(k): v for k, v in rules["stations"].items()
        }
        self.paint_stations = {int(s) for s in rules["paint_env"]["stations"]}
        self._keys: dict[tuple[str, int, str], _Key] = {}
        # 0 = off: rules baked before ADR-057 carry no silence threshold.
        self.silence_s = float(rules["starvation"].get("silence_s") or 0.0)
        self._last_seen: dict[int, tuple[float, Any]] = {}  # sid -> (epoch, last state)

    # ------------------------------------------------------------------ public

    def observe(self, event: dict[str, Any]) -> list[Firing]:
        """Feed one event; return the detectors that fired on it (usually none)."""
        if "station_id" not in event or "ts" not in event:
            return []
        sid = int(event["station_id"])
        now = _epoch(event["ts"])
        self._last_seen[sid] = (now, event.get("station_state"))
        fired: list[Firing] = []
        for firing in (self._starvation(sid, event, now),
                       self._paint(sid, event, now),
                       *self._tool_wear(sid, event, now),
                       self._silence(now, event["ts"])):
            if firing is not None:
                fired.append(firing)
        return fired

    def tick(self, now: float) -> list[Firing]:
        """Advance the clock with no event (epoch seconds). A line that has stopped
        entirely emits nothing, so only a driver with its own clock can see it."""
        firing = self._silence(now, datetime.fromtimestamp(now).isoformat())
        return [firing] if firing else []

    def is_clear(self, detector: str, station_id: int) -> bool:
        """True once a FULL post-mitigation window shows the condition gone."""
        keys = [k for k in self._keys
                if k[0] == detector and k[1] == (PAINT_KEY if detector == PAINT_ENV
                                                 else station_id)]
        return bool(keys) and all(
            len(self._keys[k].window) >= self.n and not self._keys[k].active for k in keys)

    def mark_applied(self, detector: str, station_id: int) -> None:
        """A mitigation was applied: judge recovery only on events that arrive after it."""
        sid = PAINT_KEY if detector == PAINT_ENV else station_id
        for k, state in self._keys.items():
            if k[0] == detector and k[1] == sid:
                state.window.clear()

    # --------------------------------------------------------------- detectors

    def _key(self, detector: str, sid: int, signal: str = "") -> _Key:
        k = (detector, sid, signal)
        if k not in self._keys:
            self._keys[k] = _Key(window=deque(maxlen=self.n))
        return self._keys[k]

    def _latch(self, key: _Key, condition: bool, now: float) -> bool:
        """Edge-trigger with cooldown. Returns True on a NEW firing only."""
        if not condition:
            key.active = False
            return False
        if key.active or now - key.last_fired < self.cooldown:
            return False
        key.active = True
        key.last_fired = now
        return True

    def _starvation(self, sid: int, event: dict[str, Any], now: float) -> Firing | None:
        cycle = event.get("cycle_time_s")
        if cycle is None:
            return None
        key = self._key(STARVATION, sid)
        key.window.append((event.get("station_state") in STALLED_STATES, float(cycle)))
        if len(key.window) < self.n:
            return None
        share = sum(1 for stalled, _ in key.window if stalled) / len(key.window)
        mean_cycle = _mean(c for _, c in key.window)
        takt = float(self.rules["takt_s"])
        condition = (share >= float(self.rules["starvation"]["stalled_share"])
                     and mean_cycle > takt)
        if not self._latch(key, condition, now):
            return None
        meta = self.stations.get(sid, {})
        return Firing(STARVATION, sid, event["ts"], {
            "station_id": sid,
            "station_name": meta.get("name"),
            "stalled_share": round(share, 3),
            "stalled_state": event.get("station_state"),
            "window_events": len(key.window),
            "mean_cycle_time_s": round(mean_cycle, 2),
            "takt_s": takt,
            "downstream_station": meta.get("next_station"),
            "buffer_capacity_after": meta.get("buffer_capacity_after"),
        })

    def _silence(self, now: float, ts: str) -> Firing | None:
        """One firing per line stoppage (a line-level key, like paint_env's zone key)."""
        if not self.silence_s or not self._last_seen:
            return None
        silent = {s: now - t for s, (t, _) in self._last_seen.items()}
        worst = max(silent.values())
        key = self._key(STARVATION, LINE_KEY, "silence")
        if not self._latch(key, worst >= self.silence_s, now):
            return None
        # ponytail: localisation heuristic. A blockage freezes everything upstream at
        # once, so the stalled point is the DOWNSTREAM end of the longest-silent run
        # (ties within one takt). Measured in docs/AGENT_EVAL.md, not assumed right.
        takt = float(self.rules["takt_s"])
        sid = max(s for s, gap in silent.items() if gap >= worst - takt)
        meta = self.stations.get(sid, {})
        return Firing(STARVATION, sid, ts, {
            "station_id": sid,
            "station_name": meta.get("name"),
            "signal": "silence",
            "silent_s": round(worst, 1),
            "silence_threshold_s": self.silence_s,
            "silent_stations": sum(gap >= self.silence_s for gap in silent.values()),
            "stalled_share": 1.0,
            "stalled_state": self._last_seen[sid][1],
            "takt_s": takt,
            "downstream_station": meta.get("next_station"),
            "buffer_capacity_after": meta.get("buffer_capacity_after"),
        })

    def _paint(self, sid: int, event: dict[str, Any], now: float) -> Firing | None:
        if sid not in self.paint_stations:
            return None
        zone, ambient = event.get("humidity_pct"), event.get("ambient_humidity_pct")
        if zone is None or ambient is None:
            return None
        key = self._key(PAINT_ENV, PAINT_KEY)
        key.window.append((float(zone), float(zone) - float(ambient)))
        if len(key.window) < self.n:
            return None
        zone_mean = _mean(z for z, _ in key.window)
        excess = _mean(e for _, e in key.window)
        rules = self.rules["paint_env"]
        condition = (excess >= float(rules["humidity_excess_pct"])
                     and zone_mean > float(rules["humidity_threshold_pct"]))
        if not self._latch(key, condition, now):
            return None
        return Firing(PAINT_ENV, sid, event["ts"], {
            "station_id": sid,
            "station_name": self.stations.get(sid, {}).get("name"),
            "zone_humidity_mean_pct": round(zone_mean, 2),
            "humidity_excess_over_ambient_pct": round(excess, 2),
            "humidity_threshold_pct": float(rules["humidity_threshold_pct"]),
            "excess_threshold_pct": float(rules["humidity_excess_pct"]),
            "window_events": len(key.window),
        })

    def _tool_wear(self, sid: int, event: dict[str, Any], now: float) -> list[Firing | None]:
        out: list[Firing | None] = []
        rules = self.rules["tool_wear"]
        variant = str(event.get("variant") or "")
        for signal in rules["signals"]:
            value = event.get(signal)
            if value is None:
                continue
            # Per-VARIANT baseline: torque targets differ by variant, and a single
            # baseline read a variant-mix change as a 24% "drift" on a normal run.
            ref = rules["baselines"].get(f"{sid}|{signal}|{variant}")
            if not ref:
                continue
            key = self._key(TOOL_WEAR, sid, signal)
            key.window.append((float(value) - ref) / ref * 100.0)
            if len(key.window) < self.n:
                continue
            dev = _mean(key.window)
            if not self._latch(key, abs(dev) >= float(rules["deviation_pct"]), now):
                continue
            out.append(Firing(TOOL_WEAR, sid, event["ts"], {
                "station_id": sid,
                "station_name": self.stations.get(sid, {}).get("name"),
                "signal": signal,
                "deviation_pct": round(dev, 2),
                "deviation_threshold_pct": float(rules["deviation_pct"]),
                "latest_value": round(float(value), 3),
                "baseline_mean": round(ref, 3),
                "variant": variant,
                "window_events": len(key.window),
            }))
        return out
