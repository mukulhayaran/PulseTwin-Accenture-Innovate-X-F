"""The action catalogue's contract with the twin (ADR-051, ADR-069).

The ADR-051 proposal inbox and its endpoints were retired (ADR-073): watch firings now
open Incident Commander incidents. What survives here is still live -- the genai action
catalogue must match the twin's mitigations, the verifier must drop an action whose
evidence failed, and an approved action changes only the simulated twin.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"

if not (ARTIFACTS / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from genai import verifier as V  # noqa: E402
from genai.finding import ACTION_TYPES, Citation, Claim, Finding, ProposedAction  # noqa: E402
from serve.live_simulation import MITIGATIONS, LiveSimulationManager, _mitigated  # noqa: E402

PLANT = "plant_a"
RUN = f"TEST-{secrets.token_hex(4)}"


def _transformed(scenario: str, station: int | None, limit: int, control_extra=None):
    fake = SimpleNamespace(plants={PLANT: {"budget_threshold": 1.0}},
                           _raise_risk=lambda event, sev: None)
    control = {"scenario": scenario, "severity": 0.7, "affected_station": station or 0,
               "affected_lot": None, "incident_start_s": 0.0, "first_event_ts": None,
               **(control_extra or {})}
    with open(ARTIFACTS / f"stream_{PLANT}.jsonl", encoding="utf-8") as f:
        for seq, line in enumerate(f, start=1):
            if seq > limit:
                return
            yield LiveSimulationManager._transform(
                fake, json.loads(line), RUN, PLANT, 42, seq, control)


# ------------------------------------------------------------------- the contract

def test_every_action_type_has_a_twin_mitigation() -> None:
    """genai/ and serve/ cannot import each other; this keeps the catalogue in step."""
    assert set(MITIGATIONS) == set(ACTION_TYPES)


def test_an_action_resting_on_an_unverified_claim_is_dropped() -> None:
    transcript = {"call_0001": {"ok": True, "data": {"x": 5.0, "y": 6.0, "z": 7.0}}}
    good = [Claim(f"c{i}", f"{k} holds", v, "count", "measured",
                  Citation("call_0001", f"/data/{k}"))
            for i, (k, v) in ((1, ("x", 5.0)), (3, ("y", 6.0)), (4, ("z", 7.0)))]
    # c2 cites x but states nine: it fails C2 and is redacted -- one in four stays
    # under the redaction ratio, so the RUN survives and only the action is at stake.
    bad = Claim("c2", "x is nine", 9.0, "count", "measured", Citation("call_0001", "/data/x"))
    finding = Finding(
        question="q", summary="s", claims=[*good, bad], recommendation="r",
        proposed_actions=[
            ProposedAction("a1", "maintenance_ticket", {"station_id": 3}, "fix", ["c1"]),
            ProposedAction("a2", "maintenance_ticket", {"station_id": 3}, "fix", ["c2"]),
            ProposedAction("a3", "stop_the_line", {}, "why not", ["c1"]),
            ProposedAction("a4", "hvac_setpoint", {}, "missing param", ["c1"]),
            ProposedAction("a5", "resequence_paint_queue", {}, "no evidence", []),
        ])
    ver, served = V.verify(finding, transcript)
    assert [a.id for a in served.proposed_actions] == ["a1"]
    assert ver.actions_total == 5 and ver.actions_served == 1
    assert len(ver.action_issues) == 4


# ------------------------------------------------------------------ the twin applies

def test_an_approved_hvac_action_removes_the_humidity_excess_in_the_twin() -> None:
    hvac = [{**MITIGATIONS["hvac_setpoint"], "station": 0}]
    humid = {e["unit_id"] + str(e["station_id"]): e.get("humidity_pct")
             for e in _transformed("high_humidity", None, 300)}
    fixed = {e["unit_id"] + str(e["station_id"]): e.get("humidity_pct")
             for e in _transformed("high_humidity", None, 300, {"mitigations": hvac})}
    normal = {e["unit_id"] + str(e["station_id"]): e.get("humidity_pct")
              for e in _transformed("normal", None, 300)}
    assert fixed == normal and humid != normal


def test_a_station_mitigation_applies_only_at_its_own_station() -> None:
    control = {"mitigations": [{**MITIGATIONS["maintenance_ticket"], "station": 8}]}
    assert _mitigated(control, "torque_degradation", 8, "signal") == 1.0
    assert _mitigated(control, "torque_degradation", 9, "signal") == 0.0
    assert _mitigated(control, "high_humidity", 8, "signal") == 0.0
    risk_only = {"mitigations": [{**MITIGATIONS["resequence_paint_queue"], "station": 0}]}
    assert _mitigated(risk_only, "high_humidity", 16, "signal") == 0.0
    assert _mitigated(risk_only, "high_humidity", 16, "risk") == 1.0
