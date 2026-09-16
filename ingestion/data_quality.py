"""Per-station, per-sensor data quality scoring for the real-time ingestion path.

Three components, weighted into one overall score, computed over a rolling time window:

- completeness: fraction of the readings a station *should* emit (given its nominal cycle
  time) that actually arrived
- timeliness: fraction of arrivals within `LATE_FACTOR x nominal_cycle_s` of their event
  timestamp (a message can arrive validly late -- ingestion/mqtt_publisher.py injects this
  deliberately -- but too late and it is effectively useless for a live prediction)
- validity: fraction of readings inside the signal's physically plausible range

Sensor DROPOUT is tracked separately, at (plant, station, signal) granularity, because "S8
vibration is dark" is a different and more actionable fact than "S8's overall quality score
dipped" -- it is what SensorDropoutAlert.tsx and /api/system/health render.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

WINDOW_SECONDS = 300.0
LATE_FACTOR = 2.0          # arrival more than this many nominal-cycles after its event ts
DROPOUT_FACTOR = 3.0       # no reading for this many nominal-cycles => dropout

# Physically plausible ranges. Anything outside is flagged invalid and excluded from
# feature computation, matching offline/simulator/physics.py's own signal set.
VALID_RANGES: dict[str, tuple[float, float]] = {
    "torque_nm": (0.0, 200.0),
    "vibration_mm_s": (0.0, 50.0),
    "temperature_c": (-10.0, 250.0),
    "weld_current_a": (0.0, 2000.0),
    "humidity_pct": (0.0, 100.0),
    "film_thickness_um": (0.0, 500.0),
}


def is_valid_reading(signal: str, value: float | None) -> bool:
    if value is None or value != value:  # NaN
        return False
    lo, hi = VALID_RANGES.get(signal, (float("-inf"), float("inf")))
    return lo <= value <= hi


@dataclass
class _StationWindow:
    events: deque = field(default_factory=deque)  # (arrival_ts, is_valid, is_on_time)
    nominal_cycle_s: float = 60.0


@dataclass
class _SensorLastSeen:
    arrival_ts: float
    nominal_cycle_s: float


class DataQualityTracker:
    """One instance per ingestion process. Single-threaded (one asyncio loop) by design."""

    def __init__(self, window_s: float = WINDOW_SECONDS):
        self.window_s = window_s
        self._windows: dict[tuple[str, int], _StationWindow] = {}
        self._last_seen: dict[tuple[str, int, str], _SensorLastSeen] = {}
        self._known_sensors: set[tuple[str, int, str]] = set()

    def record(self, plant_id: str, station_id: int, signal: str, value: float | None,
               event_ts: float, arrival_ts: float, nominal_cycle_s: float) -> None:
        key = (plant_id, station_id)
        w = self._windows.setdefault(key, _StationWindow(nominal_cycle_s=nominal_cycle_s))
        w.nominal_cycle_s = nominal_cycle_s

        valid = is_valid_reading(signal, value)
        on_time = (arrival_ts - event_ts) <= max(nominal_cycle_s * LATE_FACTOR, 5.0)
        w.events.append((arrival_ts, valid, on_time))
        self._evict(w, arrival_ts)

        sk = (plant_id, station_id, signal)
        self._known_sensors.add(sk)
        self._last_seen[sk] = _SensorLastSeen(arrival_ts, nominal_cycle_s)

    def _evict(self, w: _StationWindow, now: float) -> None:
        while w.events and now - w.events[0][0] > self.window_s:
            w.events.popleft()

    def score(self, plant_id: str, station_id: int) -> dict:
        w = self._windows.get((plant_id, station_id))
        if w is None or not w.events:
            return {"completeness": None, "timeliness": None, "validity": None,
                    "overall": None, "n": 0}
        n = len(w.events)
        valid = sum(1 for _, v, _ in w.events if v)
        on_time = sum(1 for _, _, t in w.events if t)
        # Expected count over the SPAN actually observed so far, not over a hypothetical
        # full window_s -- a station that just started reporting (or the window hasn't
        # filled yet) would otherwise be scored as incomplete for no real reason. Once the
        # window is fully populated in steady state, events[-1]-events[0] naturally
        # approaches window_s and this converges to the original intent.
        span = w.events[-1][0] - w.events[0][0] if n > 1 else 0.0
        expected = max(1.0, span / max(w.nominal_cycle_s, 1e-6) + 1)
        completeness = min(1.0, n / expected)
        timeliness = on_time / n
        validity = valid / n
        overall = round(0.4 * completeness + 0.3 * timeliness + 0.3 * validity, 4)
        return {"completeness": round(completeness, 4), "timeliness": round(timeliness, 4),
                "validity": round(validity, 4), "overall": overall, "n": n}

    def overall_score(self) -> float | None:
        """Weighted-by-volume score across every station seen -- the single number
        /api/system/health reports as `ingestion.data_quality_score`."""
        total_n, total_weighted = 0, 0.0
        for (plant_id, station_id) in list(self._windows):
            s = self.score(plant_id, station_id)
            if s["overall"] is not None:
                total_weighted += s["overall"] * s["n"]
                total_n += s["n"]
        return round(total_weighted / total_n, 4) if total_n else None

    def check_dropouts(self, now: float, factor: float = DROPOUT_FACTOR) -> list[dict]:
        """Sensors silent for > factor x their own nominal cycle time."""
        out = []
        for (plant_id, station_id, signal) in self._known_sensors:
            last = self._last_seen.get((plant_id, station_id, signal))
            if last is None:
                continue
            threshold = max(last.nominal_cycle_s * factor, 10.0)
            gap = now - last.arrival_ts
            if gap > threshold:
                out.append({
                    "plant_id": plant_id, "station_id": station_id, "signal": signal,
                    "last_reading_at": last.arrival_ts, "dropout_duration_s": round(gap, 1),
                })
        return out
