"""Operator pools, shift patterns, and the Mode B rush behaviour.

ADR-026 is the important thing here. The rush that causes Defect Mode B fires on the
UNBLOCK TRANSITION, never on the blocked state itself. An operator at a blocked station
gains nothing by working faster -- the downstream buffer is full, so the unit simply waits
longer. What actually happens on a real line is the opposite: the block clears, the buffer
drains in a burst, a visible queue of waiting work appears, and shift-quota pressure
compresses the checklist for the next few units.

PRIVACY (CLAUDE.md §3): operator_id exists inside the simulator and the genealogy table
because containment tracing genuinely needs the linkage. It never reaches an API response
or a UI element -- featurisation hashes it into opaque buckets and the containment endpoint
emits group labels only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np

from offline.config_schema import PlantConfig, Station
from offline.simulator.line_model import SIM_START, Unit

NOTE_CODES = ("OK", "MINOR_ADJ", "TOOL_SWAP", "REWORK_FLAG", "MATERIAL_VAR")


@dataclass(slots=True)
class Operator:
    operator_id: str
    shift_id: str
    skill: str
    torque_bias_pct: float
    checklist_multiplier: float


@dataclass
class ShiftWindow:
    shift_id: str
    name: str
    start_h: float
    end_h: float
    operators: list[Operator] = field(default_factory=list)

    def contains(self, hour_of_day: float) -> bool:
        if self.start_h <= self.end_h:
            return self.start_h <= hour_of_day < self.end_h
        return hour_of_day >= self.start_h or hour_of_day < self.end_h  # wraps midnight


def _parse_hhmm(value: str) -> float:
    h, m = value.split(":")
    return int(h) + int(m) / 60.0


class OperatorPool:
    """Owns shift rosters, station assignment, checklist timing, and rush state."""

    def __init__(self, cfg: PlantConfig, rng: np.random.Generator) -> None:
        self.cfg = cfg
        self.shifts: list[ShiftWindow] = []

        for sh in cfg.shifts:
            window = ShiftWindow(
                shift_id=sh.id,
                name=sh.name,
                start_h=_parse_hhmm(sh.start),
                end_h=_parse_hhmm(sh.end),
            )
            skills = list(sh.skill_distribution.keys())
            probs = np.array([sh.skill_distribution[s] for s in skills], dtype=np.float64)
            probs /= probs.sum()
            for n in range(sh.operator_count):
                skill = str(rng.choice(skills, p=probs))
                window.operators.append(
                    Operator(
                        operator_id=f"{sh.id}_OP{n:02d}",
                        shift_id=sh.id,
                        skill=skill,
                        torque_bias_pct=cfg.operators.skill_torque_bias_pct[skill],
                        checklist_multiplier=cfg.operators.skill_checklist_multiplier[skill],
                    )
                )
            self.shifts.append(window)

        self._by_id: dict[str, Operator] = {
            op.operator_id: op for w in self.shifts for op in w.operators
        }

        # Rotation period in seconds: N units at takt.
        self._rotation_s = cfg.operators.rotation_interval_units * cfg.takt_s
        self._station_index = {s.id: i for i, s in enumerate(cfg.stations)}

        mb = cfg.defect_physics.mode_b
        self._rush_station_id = mb.contributing_station
        self._rush_min_blocked_s = mb.unblock_min_blocked_s
        self._rush_span = tuple(mb.rush_units_after_unblock)
        self._rush_threshold_s = mb.checklist_rush_threshold_s
        self._rush_nominal_s = mb.checklist_nominal_s

        self._rush_remaining: dict[int, int] = {}
        self.checklists: list[tuple] = []

    # ------------------------------------------------------------------ shift logic

    def shift_at(self, t: float) -> ShiftWindow:
        hour = ((SIM_START + timedelta(seconds=t)).hour) + (
            (SIM_START + timedelta(seconds=t)).minute / 60.0
        )
        for w in self.shifts:
            if w.contains(hour):
                return w
        return self.shifts[0]

    def assignment(self, station: Station, t: float) -> tuple[str, str]:
        """Which operator is on this station right now.

        Deterministic given (station, time): operators rotate through the stations of
        their zone on a fixed cadence, so a unit's operator is reproducible on replay.
        """
        window = self.shift_at(t)
        block = int(t // self._rotation_s)
        idx = (self._station_index[station.id] + block) % len(window.operators)
        op = window.operators[idx]
        return op.operator_id, window.shift_id

    def operator(self, operator_id: str) -> Operator:
        return self._by_id[operator_id]

    # -------------------------------------------------------------------- mode B

    def on_unblock(
        self, station: Station, blocked_duration_s: float, now: float, rng: np.random.Generator
    ) -> None:
        """A sustained block just cleared. ADR-026: this is Mode B's trigger.

        The queue that built up behind the block is now visible to the operator, and
        shift-quota pressure compresses the checklist for the next few units.
        """
        if station.id != self._rush_station_id:
            return
        if blocked_duration_s < self._rush_min_blocked_s:
            return
        lo, hi = self._rush_span
        self._rush_remaining[station.id] = int(rng.integers(lo, hi + 1))

    def checklist_time(
        self, station: Station, unit: Unit, now: float, rng: np.random.Generator
    ) -> float:
        """Checklist duration for a manual station. Records the checklist row."""
        operator_id, _shift_id = self.assignment(station, now)
        op = self._by_id[operator_id]

        nominal = float(station.checklist_nominal_s or self._rush_nominal_s)
        rushing = False

        if station.id == self._rush_station_id and self._rush_remaining.get(station.id, 0) > 0:
            rushing = True
            self._rush_remaining[station.id] -= 1
            # Compressed well below the rush threshold, so Mode B actually fires.
            centre = self._rush_threshold_s * 0.88
            duration = float(rng.normal(centre, 2.2))
            duration = float(np.clip(duration, 12.0, self._rush_threshold_s - 0.5))
        else:
            duration = float(rng.normal(nominal * op.checklist_multiplier, nominal * 0.11))
            duration = max(8.0, duration)

        # A rushed checklist still records a PASS -- that is precisely why the defect
        # escapes to S30 rather than being caught here.
        pass_prob = 0.93 if op.skill == "junior" else 0.97
        checklist_pass = int(rng.random() < pass_prob)

        if rushing:
            note = "OK" if rng.random() < 0.85 else "MINOR_ADJ"
        elif not checklist_pass:
            note = str(rng.choice(("REWORK_FLAG", "MATERIAL_VAR", "MINOR_ADJ")))
        else:
            note = str(rng.choice(NOTE_CODES, p=[0.72, 0.12, 0.06, 0.05, 0.05]))

        self.checklists.append(
            (unit.seq, station.id, now, operator_id, checklist_pass, duration, note, rushing)
        )
        return duration

    # ------------------------------------------------------------------- accessors

    def torque_bias_lookup(self) -> dict[str, float]:
        return {op_id: op.torque_bias_pct for op_id, op in self._by_id.items()}

    def skill_lookup(self) -> dict[str, str]:
        return {op_id: op.skill for op_id, op in self._by_id.items()}
