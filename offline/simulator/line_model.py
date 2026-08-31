"""Event-driven discrete-event simulation of a serial assembly line.

ADR-005: this is an EVENT-DRIVEN scheduler, not a 1-second tick loop. The original spec
prescribed ticking every simulated second, which for 21 days x 30 stations is 54M station
updates in pure Python and a multi-gigabyte per-second state dump. Here the clock jumps to
the next real event -- a work completion, a breakdown, a repair -- and station state is
accumulated into 15-minute buckets rather than stored per second.

The line is serial: S1 -> S2 -> ... -> SN. Station i pulls from station (i-1)'s output
buffer; S1 pulls from an unlimited raw-material source; SN pushes to an unlimited sink.

WHAT THIS MODULE DOES NOT DO: it produces no sensor values. Timing only. Signals are
generated afterwards, vectorised, in physics.py -- which is both far faster and the only
sane way to give signals real temporal autocorrelation.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from offline.config_schema import PlantConfig, Station

# ---------------------------------------------------------------------------- states

IDLE = "idle"
WORKING = "working"
BLOCKED = "blocked"
STARVED = "starved"
DOWN = "down"

ALL_STATES = (IDLE, WORKING, BLOCKED, STARVED, DOWN)

# ---------------------------------------------------------------------------- events

EV_WORK_DONE = 0
EV_FAILURE = 1
EV_REPAIR_DONE = 2

# The line is anchored to a Monday so `humidity_high_days: [1, 2]` really is Tue/Wed.
SIM_START = datetime(2026, 1, 5, 6, 0, 0)  # Monday 06:00, start of shift A

# Tool wear creeps up but does not run away: real lines compensate wear through routine
# tool changes and offset adjustments, not only through breakdown repairs. Capping at 6%
# keeps the slowest station under takt while still letting the constraint drift, which is
# what makes shifting-bottleneck detection a real problem rather than a lookup.
MAX_WEAR_FRACTION = 0.06

STATE_BUCKET_S = 900.0  # 15 minutes -- the resolution bottleneck detection needs


@dataclass(slots=True)
class Unit:
    """One vehicle body moving down the line."""

    seq: int
    unit_id: str
    variant: str
    lot_id: str
    released_at: float


@dataclass(slots=True)
class StationRuntime:
    """Mutable per-station state. One instance per station for the whole run."""

    cfg: Station
    index: int
    capacity: int

    state: str = IDLE
    state_since: float = 0.0
    epoch: int = 0

    unit: Unit | None = None            # currently being worked
    held: Unit | None = None            # finished but blocked, waiting for buffer room
    out_buf: list[tuple[Unit, float]] = field(default_factory=list)

    work_started_at: float = 0.0
    work_ends_at: float = 0.0
    remaining_work_s: float = 0.0       # preserved across a breakdown

    wear_reset_at: float = 0.0
    wear_rate_per_hour: float = 0.0

    blocked_since: float | None = None
    last_block_duration_s: float = 0.0

    # context captured at work start, emitted with the visit record at work end
    pending: tuple = (IDLE, 0.0, 0.0, float("nan"))

    def bump(self) -> int:
        """Invalidate any event already queued for this station."""
        self.epoch += 1
        return self.epoch

    def wear_multiplier(self, now: float) -> float:
        hours = (now - self.wear_reset_at) / 3600.0
        return 1.0 + min(self.wear_rate_per_hour * hours, MAX_WEAR_FRACTION)


@dataclass
class SimResult:
    """Everything the downstream pipeline needs from the DES. Timing only."""

    plant_id: str
    units: list[Unit]
    # (unit_seq, station_id, entry_ts, exit_ts, cycle_s, prior_state, blocked_before_s,
    #  buffer_wait_s, checklist_s|nan, operator_id, shift_id)
    visits: list[tuple]
    # station_id -> bucket_index -> {state: seconds}
    state_seconds: dict[int, np.ndarray]
    bucket_count: int
    horizon_s: float
    completed_units: int
    warmup_units: int

    def achieved_uph(self) -> float:
        """Throughput over the post-warmup horizon."""
        if self.horizon_s <= 0:
            return 0.0
        return self.completed_units / (self.horizon_s / 3600.0)


class LineSimulator:
    """Event-driven simulation of one plant configuration.

    Usage:
        sim = LineSimulator(cfg, operator_pool)
        result = sim.run()
    """

    def __init__(self, cfg: PlantConfig, operator_pool, rng: np.random.Generator) -> None:
        self.cfg = cfg
        self.pool = operator_pool
        self.rng = rng

        self.stations: list[StationRuntime] = []
        for i, st in enumerate(cfg.stations):
            rt = StationRuntime(
                cfg=st,
                index=i,
                capacity=st.buffer_capacity_after,
                wear_rate_per_hour=float(
                    rng.uniform(*cfg.simulation.tool_wear_pct_per_hour) / 100.0
                ),
            )
            self.stations.append(rt)

        self.n = len(self.stations)
        self.last_index = self.n - 1

        self.horizon_s = cfg.simulation.duration_days * 86400.0
        self.bucket_count = int(np.ceil(self.horizon_s / STATE_BUCKET_S)) + 1
        self.state_seconds: dict[int, np.ndarray] = {
            st.cfg.id: np.zeros((self.bucket_count, len(ALL_STATES)), dtype=np.float64)
            for st in self.stations
        }
        self._state_idx = {s: i for i, s in enumerate(ALL_STATES)}

        self.queue: list[tuple[float, int, int, int, int]] = []
        self._seq = 0
        self.now = 0.0

        self.units: list[Unit] = []
        self.visits: list[tuple] = []
        self._next_unit_seq = 0
        self._completed = 0

        self._variant_ids = cfg.variant_ids
        self._weekday_cache: dict[int, int] = {}

    # ------------------------------------------------------------------ scheduling

    def _push(self, when: float, kind: int, station_idx: int, epoch: int) -> None:
        self._seq += 1
        heapq.heappush(self.queue, (when, self._seq, kind, station_idx, epoch))

    def _set_state(self, rt: StationRuntime, new_state: str, now: float) -> None:
        """Close the current state interval into 15-minute buckets, then transition."""
        if new_state == rt.state:
            return
        self._accumulate(rt, rt.state, rt.state_since, now)

        if rt.state == BLOCKED and rt.blocked_since is not None:
            rt.last_block_duration_s = now - rt.blocked_since
            rt.blocked_since = None
        if new_state == BLOCKED:
            rt.blocked_since = now

        rt.state = new_state
        rt.state_since = now

    def _accumulate(self, rt: StationRuntime, state: str, start: float, end: float) -> None:
        """Spread a state interval across the 15-minute buckets it touches."""
        if end <= start:
            return
        col = self._state_idx[state]
        row = self.state_seconds[rt.cfg.id]
        b0 = int(start // STATE_BUCKET_S)
        b1 = int(end // STATE_BUCKET_S)
        if b0 >= self.bucket_count:
            return
        if b0 == b1:
            row[b0, col] += end - start
            return
        row[b0, col] += (b0 + 1) * STATE_BUCKET_S - start
        for b in range(b0 + 1, min(b1, self.bucket_count - 1)):
            row[b, col] += STATE_BUCKET_S
        if b1 < self.bucket_count:
            row[b1, col] += end - b1 * STATE_BUCKET_S

    # --------------------------------------------------------------------- variants

    def _weekday(self, t: float) -> int:
        day = int(t // 86400)
        wd = self._weekday_cache.get(day)
        if wd is None:
            wd = (SIM_START + timedelta(days=day)).weekday()
            self._weekday_cache[day] = wd
        return wd

    def _is_peak_day(self, t: float) -> bool:
        """The variant-mix trigger: EV-heavy (or truck-heavy) production days."""
        return self._weekday(t) in set(self.cfg.ambient.humidity_high_days)

    def _draw_variant(self, t: float) -> str:
        mix = self.cfg.mix_pct(tuesday=self._is_peak_day(t))
        p = np.array([mix[v] for v in self._variant_ids], dtype=np.float64)
        p /= p.sum()
        return str(self.rng.choice(self._variant_ids, p=p))

    def _cycle_time(self, rt: StationRuntime, unit: Unit, now: float) -> tuple[float, float]:
        """Effective cycle time for this unit at this station.

        Returns (cycle_s, checklist_s). checklist_s is NaN for instrumented stations --
        manual stations are paced by the operator's checklist, which is where Mode B lives.
        """
        st = rt.cfg
        base = st.nominal_cycle_s * st.variant_multipliers.get(unit.variant, 1.0)
        base *= rt.wear_multiplier(now)

        if st.sensor_tier == "manual":
            checklist_s = self.pool.checklist_time(st, unit, now, self.rng)
            # A manual station runs at its configured nominal cycle; what VARIES is the
            # checklist portion. Model the cycle as nominal shifted by how far the
            # operator's checklist deviated from its own nominal, so a rush genuinely
            # makes the station faster -- which is the entire point of Mode B, and why
            # the operator does it.
            delta = checklist_s - float(st.checklist_nominal_s or checklist_s)
            noise = float(self.rng.normal(0.0, st.cycle_cv * base * 0.5))
            return max(5.0, base + delta + noise), checklist_s

        noise = float(self.rng.normal(0.0, st.cycle_cv * base))
        return max(5.0, base + noise), float("nan")

    # ------------------------------------------------------------------- mechanics

    def _upstream_take(self, idx: int, now: float) -> Unit | None:
        """Pull a unit for station `idx`, freeing upstream buffer space if we do."""
        if idx == 0:
            seq = self._next_unit_seq
            self._next_unit_seq += 1
            variant = self._draw_variant(now)
            lot = seq // self.cfg.simulation.lot_change_interval_units
            unit = Unit(
                seq=seq,
                unit_id=f"U{seq:06d}",
                variant=variant,
                lot_id=f"L{lot:04d}",
                released_at=now,
            )
            self.units.append(unit)
            return unit

        up = self.stations[idx - 1]
        if not up.out_buf:
            return None
        unit, _pushed_at = up.out_buf.pop(0)
        self._on_buffer_freed(up, now)
        return unit

    def _buffer_entry_time(self, idx: int) -> float:
        """When the unit at the head of station idx-1's buffer was pushed there."""
        if idx == 0:
            return self.now
        up = self.stations[idx - 1]
        return up.out_buf[0][1] if up.out_buf else self.now

    def _try_start(self, rt: StationRuntime, now: float) -> None:
        """Attempt to begin work. Sets STARVED if there is nothing upstream to take."""
        if rt.state in (WORKING, DOWN, BLOCKED) or rt.unit is not None:
            return

        pushed_at = self._buffer_entry_time(rt.index)
        unit = self._upstream_take(rt.index, now)
        if unit is None:
            self._set_state(rt, STARVED, now)
            return

        prior_state = rt.state
        blocked_before = rt.last_block_duration_s
        rt.last_block_duration_s = 0.0

        cycle_s, checklist_s = self._cycle_time(rt, unit, now)
        rt.unit = unit
        rt.work_started_at = now
        rt.work_ends_at = now + cycle_s
        rt.remaining_work_s = cycle_s
        rt.pending = (prior_state, blocked_before, max(0.0, now - pushed_at), checklist_s)

        self._set_state(rt, WORKING, now)
        self._push(rt.work_ends_at, EV_WORK_DONE, rt.index, rt.bump())

    def _release_held(self, rt: StationRuntime, now: float) -> bool:
        """Push rt's finished-but-held unit into its output buffer, if there is room.

        Returns True if the unit moved. Callers must have already established that rt is
        allowed to act (i.e. it is not DOWN).
        """
        if rt.held is None or len(rt.out_buf) >= rt.capacity:
            return False

        rt.out_buf.append((rt.held, now))
        rt.held = None
        self._set_state(rt, IDLE, now)

        # Tell the operator pool: a sustained block has just cleared. THIS is Mode B's
        # trigger (ADR-026) -- the burst of queued work after release, not the block.
        self.pool.on_unblock(rt.cfg, rt.last_block_duration_s, now, self.rng)

        self._try_start(rt, now)
        if rt.index < self.last_index:
            self._wake(self.stations[rt.index + 1], now)
        return True

    def _on_buffer_freed(self, rt: StationRuntime, now: float) -> None:
        """A slot opened in rt's output buffer -- release it if it was blocked.

        A DOWN station cannot act on this. That is not a lost wake-up: _on_repair_done
        retries the release, which is what stops the line deadlocking when a station
        breaks down while blocked and downstream then drains the buffer completely.
        """
        if rt.state != BLOCKED:
            return
        self._release_held(rt, now)

    def _wake(self, rt: StationRuntime, now: float) -> None:
        if rt.state == STARVED:
            self._try_start(rt, now)

    def _on_work_done(self, rt: StationRuntime, now: float) -> None:
        unit = rt.unit
        assert unit is not None
        prior_state, blocked_before, buffer_wait, checklist_s = rt.pending

        operator_id, shift_id = self.pool.assignment(rt.cfg, now)
        self.visits.append(
            (
                unit.seq,
                rt.cfg.id,
                rt.work_started_at,
                now,
                now - rt.work_started_at,
                prior_state,
                blocked_before,
                buffer_wait,
                checklist_s,
                operator_id,
                shift_id,
            )
        )

        rt.unit = None

        if rt.index == self.last_index:
            self._completed += 1
            self._set_state(rt, IDLE, now)
            self._try_start(rt, now)
            return

        if len(rt.out_buf) < rt.capacity:
            rt.out_buf.append((unit, now))
            self._set_state(rt, IDLE, now)
            self._try_start(rt, now)
            self._wake(self.stations[rt.index + 1], now)
        else:
            rt.held = unit
            self._set_state(rt, BLOCKED, now)

    def _on_failure(self, rt: StationRuntime, now: float) -> None:
        if rt.state == DOWN:
            return
        if rt.state == WORKING:
            rt.remaining_work_s = max(0.0, rt.work_ends_at - now)
        rt.bump()  # cancel the pending work-done event
        self._set_state(rt, DOWN, now)
        repair_s = float(self.rng.gamma(shape=2.0, scale=rt.cfg.repair_time_s / 2.0))
        self._push(now + repair_s, EV_REPAIR_DONE, rt.index, rt.epoch)

    def _on_repair_done(self, rt: StationRuntime, now: float) -> None:
        rt.wear_reset_at = now  # a repair restores the tool to nominal
        self._schedule_failure(rt, now)

        if rt.unit is not None:
            rt.work_ends_at = now + rt.remaining_work_s
            self._set_state(rt, WORKING, now)
            self._push(rt.work_ends_at, EV_WORK_DONE, rt.index, rt.bump())
        elif rt.held is not None:
            # Downstream may have drained the buffer while we were down, and
            # _on_buffer_freed could not act on a DOWN station. Retry the release here
            # or the line deadlocks permanently.
            self._set_state(rt, BLOCKED, now)
            self._release_held(rt, now)
        else:
            self._set_state(rt, IDLE, now)
            self._try_start(rt, now)

    def _schedule_failure(self, rt: StationRuntime, now: float) -> None:
        mtbf_s = rt.cfg.mtbf_hours * 3600.0
        when = now + float(self.rng.exponential(mtbf_s))
        if when < self.horizon_s:
            self._push(when, EV_FAILURE, rt.index, -1)  # epoch -1 = never stale

    # -------------------------------------------------------------------- main loop

    def run(self) -> SimResult:
        for rt in self.stations:
            rt.state = STARVED if rt.index > 0 else IDLE
            rt.state_since = 0.0
            self._schedule_failure(rt, 0.0)

        self._try_start(self.stations[0], 0.0)

        warmup = self.cfg.simulation.warmup_units
        warmup_done_at: float | None = None

        while self.queue:
            when, _seq, kind, idx, epoch = heapq.heappop(self.queue)
            if when > self.horizon_s:
                break
            rt = self.stations[idx]

            if kind == EV_FAILURE:
                self.now = when
                self._on_failure(rt, when)
                continue

            if epoch != rt.epoch:
                continue  # stale: invalidated by a breakdown

            self.now = when
            if kind == EV_WORK_DONE:
                self._on_work_done(rt, when)
                if warmup_done_at is None and self._completed >= warmup:
                    warmup_done_at = when
            elif kind == EV_REPAIR_DONE:
                self._on_repair_done(rt, when)

        # close out every open state interval at the horizon
        end = min(self.now, self.horizon_s)
        for rt in self.stations:
            self._accumulate(rt, rt.state, rt.state_since, end)

        start = warmup_done_at if warmup_done_at is not None else 0.0
        return SimResult(
            plant_id=self.cfg.plant_id,
            units=self.units,
            visits=self.visits,
            state_seconds=self.state_seconds,
            bucket_count=self.bucket_count,
            horizon_s=max(0.0, end - start),
            completed_units=max(0, self._completed - warmup),
            warmup_units=warmup,
        )


def ground_truth_bottleneck(result: SimResult, cfg: PlantConfig) -> np.ndarray:
    """Per-bucket ground-truth constraint, for scoring detection accuracy in Phase 5.

    Defined as the station with the highest ACTIVE fraction in the window, where active
    means working or down -- i.e. not waiting on anyone else. A station that is neither
    blocked nor starved is not being held back by the line; it IS the line's limit.

    This is a definition, not something the simulator inherently knows (AUDIT M-note on
    bottleneck ground truth). It is stated here and cited in MODEL_CARD.md so the
    reported detection accuracy is falsifiable.
    """
    ids = [s.id for s in cfg.stations]
    idx_working = ALL_STATES.index(WORKING)
    idx_down = ALL_STATES.index(DOWN)

    active = np.stack(
        [
            result.state_seconds[sid][:, idx_working] + result.state_seconds[sid][:, idx_down]
            for sid in ids
        ],
        axis=1,
    )
    winners = np.argmax(active, axis=1)
    truth = np.array([ids[w] for w in winners], dtype=np.int32)
    truth[active.sum(axis=1) == 0] = -1  # buckets with no activity at all
    return truth
