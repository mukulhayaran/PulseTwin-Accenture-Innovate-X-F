"""Physical incident injection for the live twin.

THE RULE THIS MODULE EXISTS TO ENFORCE. An incident changes a PHYSICAL CONDITION of the
line and nothing else. It never touches a score, a probability, an alert flag or a
threshold. Whether risk rises is then a question for the trained model, answered through
the ordinary feature path -- and sometimes the answer is "it didn't", which is a real
result about the model rather than a bug in the demo.

Contrast `serve/live_simulation.py::_raise_risk`, which does
`calibrated_risk += 0.5 * severity`. That is kept, under the honest name "scenario
injection", because a scripted demo is a legitimate thing to want. It is not this.

Each incident below names the physical quantity it moves and the path by which the model
could notice:

    torque_degradation   tool health at a station decays
                         -> achieved torque falls, vibration rises (shared cause)
                         -> s<N>_torque_nm, _dev_median, _z, _roc move
    high_humidity        ambient humidity rises in the paint zone
                         -> humidity_pct rises AND film_thickness_um thins (spray
                            transfer), which is Mode A's co-trigger
    station_breakdown    the station is forced DOWN through the simulator's own failure
                         path -> downstream starves, upstream blocks, the constraint moves
    buffer_congestion    the station's output buffer capacity is physically reduced
                         -> blocking propagates upstream, dwell times rise
    sensor_dropout       named sensors stop reporting -> the value is ABSENT, and the
                         soft-sensor/inference path takes over
    rushed_checklist     the manual station's checklist standard is shortened
                         -> the operator model produces genuinely faster checklists,
                            which is Mode B's driver
    bad_material_lot     the unobservable incoming-lot quality latent is degraded for one
                         lot -> torque and vibration both shift, and the defect hazard
                            rises, with NO directly observable cause. This is the honest
                            hard case: a real confounder the model cannot see.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The scenario vocabulary is shared with serve/live_simulation.py's SCENARIOS so the two
# modes offer the same menu. `normal` means no incident.
SCENARIOS = (
    "normal",
    "torque_degradation",
    "bad_material_lot",
    "high_humidity",
    "rushed_checklist",
    "sensor_dropout",
    "station_breakdown",
    "buffer_congestion",
)

# Signals a dropout can take away. Deliberately not every signal: a station's whole
# sensor set vanishing at once is a network outage, not a sensor fault, and the
# degradation benchmark covers coverage loss systematically.
DROPPABLE = ("torque_nm", "vibration_mm_s", "temperature_c", "humidity_pct",
             "film_thickness_um", "weld_current_a")

# Full-severity effect sizes. Each is the physical magnitude at severity == 1.0 and
# scales linearly below it.
MAX_TOOL_DEFICIT = 0.22        # a tool delivering 78% of setpoint is badly worn
MAX_HUMIDITY_ADD_PCT = 22.0    # a paint shop 22 points over its normal RH
MAX_LOT_QUALITY_DROP = 2.2     # standard deviations of the lot-quality latent
MAX_CHECKLIST_CUT = 0.55       # a checklist standard cut to 45% of nominal
TOOL_WEAR_RAMP_S = 1800.0      # a tool degrades over ~30 simulated minutes, not instantly


@dataclass
class Incident:
    """One injected condition. Immutable once created except for `ended_at`."""

    scenario: str
    severity: float
    station_id: int | None = None
    lot_id: str | None = None
    start_s: float = 0.0
    ended_at: float | None = None

    def active_at(self, now: float) -> bool:
        return now >= self.start_s and (self.ended_at is None or now < self.ended_at)

    def ramp(self, now: float) -> float:
        """Fraction of full effect in force at `now`.

        Tool wear ramps; everything else is a step change, because a buffer does not
        shrink gradually and a sensor does not half-fail.
        """
        if not self.active_at(now):
            return 0.0
        if self.scenario != "torque_degradation":
            return 1.0
        return min(1.0, (now - self.start_s) / TOOL_WEAR_RAMP_S)


@dataclass
class IncidentState:
    """The set of incidents in force, and the physical handles they expose.

    The engine asks this object for physical quantities and never for a risk adjustment.
    There is deliberately no method here that returns anything resembling a score.
    """

    incidents: list[Incident] = field(default_factory=list)

    def inject(self, incident: Incident) -> None:
        self.incidents.append(incident)

    def clear(self) -> None:
        self.incidents.clear()

    def active(self, now: float) -> list[Incident]:
        return [i for i in self.incidents if i.active_at(now)]

    def _matching(self, scenario: str, station_id: int | None, now: float) -> list[Incident]:
        return [
            i for i in self.incidents
            if i.scenario == scenario
            and i.active_at(now)
            and (i.station_id is None or station_id is None or i.station_id == station_id)
        ]

    # ------------------------------------------------------------ physical handles

    def tool_health(self, station_id: int, now: float) -> float:
        """Multiplier in (0, 1] on achieved torque. 1.0 is a healthy tool."""
        deficit = 0.0
        for incident in self._matching("torque_degradation", station_id, now):
            deficit = max(deficit, MAX_TOOL_DEFICIT * incident.severity * incident.ramp(now))
        return max(0.05, 1.0 - deficit)

    def humidity_add_pct(self, station_id: int, now: float) -> float:
        """Percentage points added to ambient relative humidity."""
        add = 0.0
        for incident in self._matching("high_humidity", station_id, now):
            add = max(add, MAX_HUMIDITY_ADD_PCT * incident.severity)
        return add

    def dropped_signals(self, station_id: int, now: float) -> frozenset[str]:
        """Signals whose sensor is currently failed at this station.

        Severity selects how many of the station's droppable signals are lost, so a
        low-severity dropout is one dead channel rather than a blind station.
        """
        dropped: set[str] = set()
        for incident in self._matching("sensor_dropout", station_id, now):
            count = max(1, round(incident.severity * len(DROPPABLE)))
            dropped.update(DROPPABLE[:count])
        return frozenset(dropped)

    def buffer_capacity(self, station_id: int, base_capacity: int, now: float) -> int:
        """Effective output-buffer capacity after any congestion incident.

        Floored at zero, not at one: a completely blocked buffer is a real condition and
        the simulator handles it (the station goes BLOCKED holding its finished unit).
        """
        capacity = base_capacity
        for incident in self._matching("buffer_congestion", station_id, now):
            capacity = min(capacity, int(round(base_capacity * (1.0 - incident.severity))))
        return max(0, capacity)

    def forces_breakdown(self, station_id: int, now: float) -> bool:
        return bool(self._matching("station_breakdown", station_id, now))

    def breakdown_repair_scale(self, station_id: int, now: float) -> float:
        """How much longer repairs take while a breakdown incident is in force."""
        scale = 1.0
        for incident in self._matching("station_breakdown", station_id, now):
            scale = max(scale, 1.0 + 3.0 * incident.severity)
        return scale

    def checklist_scale(self, station_id: int, now: float) -> float:
        """Multiplier on a manual station's checklist STANDARD (not on its output).

        Shortening the standard is the cause; the operator model then produces genuinely
        faster checklists through its own formula, which is what Mode B keys on.
        """
        scale = 1.0
        for incident in self._matching("rushed_checklist", station_id, now):
            scale = min(scale, 1.0 - MAX_CHECKLIST_CUT * incident.severity)
        return max(0.2, scale)

    def lot_quality_delta(self, lot_id: str | None, now: float) -> float:
        """Shift applied to the UNOBSERVABLE incoming-lot quality latent.

        Negative means poorer material. Nothing in the feature matrix carries this; it is
        visible only through its physical consequences (torque down, vibration up) and
        through the defect hazard. That is deliberate -- it is the confounder that puts a
        ceiling on achievable PR-AUC.
        """
        delta = 0.0
        for incident in self.incidents:
            if incident.scenario != "bad_material_lot" or not incident.active_at(now):
                continue
            if incident.lot_id is not None and incident.lot_id != lot_id:
                continue
            delta = min(delta, -MAX_LOT_QUALITY_DROP * incident.severity)
        return delta

    # ------------------------------------------------------------------ reporting

    def describe(self, now: float) -> list[dict]:
        """What is in force, for the API. Physical quantities only."""
        out = []
        for incident in self.active(now):
            entry = {
                "scenario": incident.scenario,
                "severity": incident.severity,
                "station_id": incident.station_id,
                "lot_id": incident.lot_id,
                "started_at_s": incident.start_s,
                "ramp": incident.ramp(now),
            }
            if incident.scenario == "torque_degradation" and incident.station_id:
                entry["tool_health"] = self.tool_health(incident.station_id, now)
            elif incident.scenario == "high_humidity" and incident.station_id:
                entry["humidity_add_pct"] = self.humidity_add_pct(incident.station_id, now)
            elif incident.scenario == "sensor_dropout" and incident.station_id:
                entry["dropped_signals"] = sorted(
                    self.dropped_signals(incident.station_id, now)
                )
            out.append(entry)
        return out
