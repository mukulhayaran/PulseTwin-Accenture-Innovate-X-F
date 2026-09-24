"""Bottleneck detectors, benchmarked against throughput-sensitivity ground truth.

Each detector reads ONLY what a real monitoring system could read from the line -- station
state durations, buffer occupancy, active-period lengths -- and returns a score per
station where HIGHER MEANS MORE BOTTLENECK-LIKE. The benchmark converts those scores into
a ranking and scores the ranking against the independently measured constraint
(`bottleneck_ground_truth.py`).

NAMING DISCIPLINE. Three of these are faithful implementations of a published criterion
and are named for it. Three are OPERATIONALIZED ADAPTATIONS: the underlying idea is
published, but the published algorithm consumes a data structure this simulator does not
emit (per-machine event logs with setup/repair sub-states, for instance), so what is
implemented here is an adaptation to bucketed state durations. Those carry `_adapted` in
their key and say so in `.provenance`. We do not claim to reproduce a published algorithm
that we have adapted -- an adapted detector that scores well is still a real result, and
mislabelling it would make every other number in this repo suspect.

NONE of these detectors is tuned against the ground truth. They are textbook criteria
implemented once and scored as they fell.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from offline.config_schema import PlantConfig
from offline.simulator.line_model import (
    ALL_STATES,
    BLOCKED,
    DOWN,
    STARVED,
    STATE_BUCKET_S,
    WORKING,
    SimResult,
)

_IW = ALL_STATES.index(WORKING)
_IB = ALL_STATES.index(BLOCKED)
_IS = ALL_STATES.index(STARVED)
_ID = ALL_STATES.index(DOWN)


@dataclass
class LineObservation:
    """Everything a detector is allowed to see, derived once per simulated run.

    Matrices are (station, bucket). Station order matches `station_ids`, which is the
    physical order along the line -- `index_of` exists so a detector can reason about
    adjacency without assuming ids are 1..N.
    """

    plant_id: str
    station_ids: list[int]
    working: np.ndarray
    blocked: np.ndarray
    starved: np.ndarray
    down: np.ndarray
    observed: np.ndarray            # total seconds accounted for in the bucket
    queue_len: np.ndarray           # time-weighted mean occupancy of the station's OUT buffer
    active_period_count: np.ndarray  # (station,) number of uninterrupted active spans
    active_period_total: np.ndarray  # (station,) seconds across those spans

    @property
    def active(self) -> np.ndarray:
        """Roser's active criterion: working or down, i.e. not waiting on anyone else."""
        return self.working + self.down

    def index_of(self, station_id: int) -> int:
        return self.station_ids.index(station_id)

    @property
    def input_queue_len(self) -> np.ndarray:
        """Occupancy of the buffer feeding each station.

        Station i's input buffer IS station (i-1)'s output buffer. The first station
        pulls from an unlimited raw-material source, which never queues and must not be
        allowed to win by default -- it gets zero.
        """
        out = np.zeros_like(self.queue_len)
        out[1:, :] = self.queue_len[:-1, :]
        return out


def observe(result: SimResult, cfg: PlantConfig) -> LineObservation:
    """Build the detector-visible view of one simulated run.

    `result` must come from a simulator constructed with instrument=True, otherwise the
    queue-length and active-period detectors have nothing to read. Raising here beats
    silently scoring two detectors against zeros.
    """
    if result.buffer_unit_seconds is None or result.active_periods is None:
        raise ValueError(
            "run the simulator with instrument=True; queue-length and active-period "
            "detectors have no data otherwise"
        )
    ids = [s.id for s in cfg.stations]
    stack = lambda col: np.stack([result.state_seconds[i][:, col] for i in ids], axis=0)
    working, blocked, starved, down = stack(_IW), stack(_IB), stack(_IS), stack(_ID)
    idle = np.stack(
        [result.state_seconds[i].sum(axis=1) for i in ids], axis=0
    )
    queue = np.stack([result.buffer_unit_seconds[i] / STATE_BUCKET_S for i in ids], axis=0)
    periods = np.array([result.active_periods[i] for i in ids], dtype=np.float64)
    return LineObservation(
        plant_id=cfg.plant_id,
        station_ids=ids,
        working=working,
        blocked=blocked,
        starved=starved,
        down=down,
        observed=idle,
        queue_len=queue,
        active_period_count=periods[:, 0],
        active_period_total=periods[:, 1],
    )


def _as_scores(obs: LineObservation, values: np.ndarray) -> dict[int, float]:
    return {sid: float(v) for sid, v in zip(obs.station_ids, values)}


def _safe_ratio(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    return np.divide(num, np.maximum(den, 1e-9))


# --------------------------------------------------------------------------- detectors


def active_share(obs: LineObservation) -> dict[int, float]:
    """PulseTwin's shipped detector. Roser's active-period CRITERION applied to totals.

    The station spending the most seconds active (working or under repair) over the
    window is called the constraint. This is what `offline/ml/bottleneck.py` implements
    and what every bottleneck number currently in the artifact comes from.
    """
    return _as_scores(obs, _safe_ratio(obs.active.sum(axis=1), obs.observed.sum(axis=1).sum()))


def utilization(obs: LineObservation) -> dict[int, float]:
    """Classic utilisation: fraction of observed time spent actually working.

    Differs from active share by excluding repair time. A station that is frequently
    broken is active under Roser's criterion but not utilised, so the two disagree
    whenever downtime rather than cycle time is what limits the line.
    """
    return _as_scores(obs, _safe_ratio(obs.working.sum(axis=1), obs.observed.sum(axis=1)))


def busy_ratio(obs: LineObservation) -> dict[int, float]:
    """Fraction of time the station is OCCUPIED by a unit: working, down, or blocked.

    Blocked time counts as busy here because the station is holding a finished unit it
    cannot release. This deliberately rewards stations immediately upstream of a
    constraint, which is exactly the failure mode the benchmark should expose.
    """
    occupied = obs.working + obs.down + obs.blocked
    return _as_scores(obs, _safe_ratio(occupied.sum(axis=1), obs.observed.sum(axis=1)))


def active_period(obs: LineObservation) -> dict[int, float]:
    """Roser's active-period METHOD: rank by the MEAN length of uninterrupted active spans.

    This is the statistic Roser's method actually uses, and it is not the same as active
    share. A station active 50% of the time in hundreds of short bursts is being paced by
    its neighbours; a station active 50% of the time in a few long spans is pacing them.
    Measured directly in the simulator (`LineSimulator.active_periods`) rather than
    inferred from buckets.
    """
    mean_len = _safe_ratio(obs.active_period_total, obs.active_period_count)
    return _as_scores(obs, mean_len)


def average_queue_length(obs: LineObservation) -> dict[int, float]:
    """Queueing criterion: the constraint is the station with the longest INPUT queue.

    Work piles up in front of a bottleneck. Uses the time-weighted mean occupancy of the
    buffer feeding each station. Its known weakness is that a bottleneck starved by an
    upstream problem has no queue at all, and a station whose input buffer is simply
    large will accumulate more -- both visible in the results.
    """
    return _as_scores(obs, obs.input_queue_len.mean(axis=1))


def turning_point_adapted(obs: LineObservation) -> dict[int, float]:
    """ADAPTED. Momentary-bottleneck vote rather than a total.

    Roser's turning-point method finds shifting bottlenecks by locating where one
    station's active period ends and the next station's begins. The published algorithm
    walks an event-level trace of active periods; this implementation adapts the idea to
    the 15-minute buckets the line actually reports, scoring each station by the FRACTION
    OF BUCKETS in which it is the momentary (per-bucket argmax) constraint.

    That makes it a vote rather than a sum, which is a genuinely different estimator from
    `active_share`: one long stall cannot carry a station that is rarely the momentary
    constraint, and a station that narrowly leads in most buckets wins even if some other
    station has more total active time.
    """
    active = obs.active
    live = active.sum(axis=0) > 0
    votes = np.zeros(len(obs.station_ids))
    if live.any():
        winners = np.argmax(active[:, live], axis=0)
        counts = np.bincount(winners, minlength=len(obs.station_ids))
        votes = counts / counts.sum()
    return _as_scores(obs, votes)


def arrow_blocked_starved_adapted(obs: LineObservation) -> dict[int, float]:
    """ADAPTED. Blockage/starvation direction arrows, after the data-driven "arrow" family.

    For each adjacent pair (i, i+1) compare how long i spends BLOCKED against how long
    i+1 spends STARVED:

        blocked(i) > starved(i+1)  -- i cannot hand off; the constraint is DOWNSTREAM,
                                      so the arrow points i -> i+1
        otherwise                  -- i+1 is waiting for work; the constraint is
                                      UPSTREAM, so the arrow points i+1 -> i

    The bottleneck is where arrows converge: a station with an arrow pointing in from
    both neighbours is being blamed from both directions. Score is the number of incoming
    arrows (0, 1 or 2) plus a normalised magnitude tie-break, since on a 30-station line
    several stations can hold a single incoming arrow.

    ADAPTED because the published direction methods key off per-machine blockage and
    starvation durations tagged at event level, and this consumes the bucketed totals the
    simulator reports. The decision rule is the published one; the inputs are coarser.
    """
    n = len(obs.station_ids)
    blocked = obs.blocked.sum(axis=1)
    starved = obs.starved.sum(axis=1)
    incoming = np.zeros(n)
    magnitude = np.zeros(n)
    for i in range(n - 1):
        gap = blocked[i] - starved[i + 1]
        if gap > 0:
            incoming[i + 1] += 1.0
            magnitude[i + 1] += gap
        else:
            incoming[i] += 1.0
            magnitude[i] += -gap
    scale = magnitude.max()
    tiebreak = magnitude / scale if scale > 0 else magnitude
    return _as_scores(obs, incoming + 0.999 * tiebreak / max(n, 1))


@dataclass(frozen=True)
class Detector:
    key: str
    label: str
    fn: Callable[[LineObservation], dict[int, float]]
    provenance: str
    is_current: bool = False


DETECTORS: tuple[Detector, ...] = (
    Detector(
        key="active_share",
        label="Active Share",
        fn=active_share,
        provenance="faithful; this is PulseTwin's shipped detector (offline/ml/bottleneck.py)",
        is_current=True,
    ),
    Detector(
        key="utilization",
        label="Utilization",
        fn=utilization,
        provenance="faithful; textbook utilisation",
    ),
    Detector(
        key="busy_ratio",
        label="Busy Ratio",
        fn=busy_ratio,
        provenance="faithful; occupancy including blocked time",
    ),
    Detector(
        key="active_period",
        label="Active Period (mean span)",
        fn=active_period,
        provenance="faithful; Roser active-period method, measured at event level",
    ),
    Detector(
        key="average_queue_length",
        label="Average Queue Length",
        fn=average_queue_length,
        provenance="faithful; time-weighted mean input-buffer occupancy",
    ),
    Detector(
        key="turning_point_adapted",
        label="Turning Point (adapted)",
        fn=turning_point_adapted,
        provenance="ADAPTED to 15-minute buckets; not a reproduction of the published algorithm",
    ),
    Detector(
        key="arrow_blocked_starved_adapted",
        label="Arrow / blocked-starved direction (adapted)",
        fn=arrow_blocked_starved_adapted,
        provenance="ADAPTED to bucketed durations; decision rule is the published one",
    ),
)


def ranking(scores: dict[int, float]) -> list[int]:
    """Station ids ordered most-bottleneck-like first.

    Ties broken by station id so a detector that cannot discriminate produces a stable,
    reproducible ranking instead of a dict-order accident.
    """
    return [sid for sid, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]
