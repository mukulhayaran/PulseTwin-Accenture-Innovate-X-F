"""The MQTT topic schema (Unified Namespace pattern -- ISA-95-ish: enterprise/site/area/
line/... collapsed here to plant/zone/station).

    pulsetwin/{plant_id}/{zone}/{station_id}/telemetry    sensor readings
    pulsetwin/{plant_id}/{zone}/{station_id}/state        working/blocked/starved/down
    pulsetwin/{plant_id}/{zone}/{station_id}/checklist    manual station entries
    pulsetwin/{plant_id}/inspection/{unit_id}             inspection results
    pulsetwin/{plant_id}/unit/{unit_id}/entered           unit entered a station
    pulsetwin/{plant_id}/unit/{unit_id}/exited            unit exited a station
    pulsetwin/{plant_id}/simulation                       publisher run lifecycle
    pulsetwin/{plant_id}/control/dropout                  demo control: force a sensor dark

Every publisher and subscriber in ingestion/ builds and parses topics through this module
so the string format lives in exactly one place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ROOT = "pulsetwin"

_TELEMETRY_RE = re.compile(rf"^{ROOT}/([^/]+)/([^/]+)/(\d+)/telemetry$")
_STATE_RE = re.compile(rf"^{ROOT}/([^/]+)/([^/]+)/(\d+)/state$")
_CHECKLIST_RE = re.compile(rf"^{ROOT}/([^/]+)/([^/]+)/(\d+)/checklist$")
_INSPECTION_RE = re.compile(rf"^{ROOT}/([^/]+)/inspection/([^/]+)$")
_UNIT_ENTERED_RE = re.compile(rf"^{ROOT}/([^/]+)/unit/([^/]+)/entered$")
_UNIT_EXITED_RE = re.compile(rf"^{ROOT}/([^/]+)/unit/([^/]+)/exited$")
_SIMULATION_RE = re.compile(rf"^{ROOT}/([^/]+)/simulation$")
_CONTROL_DROPOUT_RE = re.compile(rf"^{ROOT}/([^/]+)/control/dropout$")


def telemetry_topic(plant_id: str, zone: str, station_id: int) -> str:
    return f"{ROOT}/{plant_id}/{zone}/{station_id}/telemetry"


def state_topic(plant_id: str, zone: str, station_id: int) -> str:
    return f"{ROOT}/{plant_id}/{zone}/{station_id}/state"


def checklist_topic(plant_id: str, zone: str, station_id: int) -> str:
    return f"{ROOT}/{plant_id}/{zone}/{station_id}/checklist"


def inspection_topic(plant_id: str, unit_id: str) -> str:
    return f"{ROOT}/{plant_id}/inspection/{unit_id}"


def unit_entered_topic(plant_id: str, unit_id: str) -> str:
    return f"{ROOT}/{plant_id}/unit/{unit_id}/entered"


def unit_exited_topic(plant_id: str, unit_id: str) -> str:
    return f"{ROOT}/{plant_id}/unit/{unit_id}/exited"


def simulation_topic(plant_id: str) -> str:
    return f"{ROOT}/{plant_id}/simulation"


def control_dropout_topic(plant_id: str) -> str:
    return f"{ROOT}/{plant_id}/control/dropout"


@dataclass(frozen=True)
class ParsedTopic:
    kind: str  # telemetry, state, checklist, inspection, entered, exited, simulation, dropout
    plant_id: str
    zone: str | None = None
    station_id: int | None = None
    unit_id: str | None = None


def parse_topic(topic: str) -> ParsedTopic | None:
    if m := _TELEMETRY_RE.match(topic):
        return ParsedTopic("telemetry", m.group(1), m.group(2), int(m.group(3)))
    if m := _STATE_RE.match(topic):
        return ParsedTopic("state", m.group(1), m.group(2), int(m.group(3)))
    if m := _CHECKLIST_RE.match(topic):
        return ParsedTopic("checklist", m.group(1), m.group(2), int(m.group(3)))
    if m := _INSPECTION_RE.match(topic):
        return ParsedTopic("inspection", m.group(1), unit_id=m.group(2))
    if m := _UNIT_ENTERED_RE.match(topic):
        return ParsedTopic("entered", m.group(1), unit_id=m.group(2))
    if m := _UNIT_EXITED_RE.match(topic):
        return ParsedTopic("exited", m.group(1), unit_id=m.group(2))
    if m := _SIMULATION_RE.match(topic):
        return ParsedTopic("simulation", m.group(1))
    if m := _CONTROL_DROPOUT_RE.match(topic):
        return ParsedTopic("dropout", m.group(1))
    return None
