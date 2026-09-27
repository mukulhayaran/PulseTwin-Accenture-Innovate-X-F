"""Coordinator -> Specialists -> Synthesizer mechanics (ADR-045).

The central claim this file exists to prove with a TEST, not merely assert in a comment:
genai/verifier.py needs ZERO changes to check a multi-agent run. It has no concept of
"which loop produced this call_id" -- as long as every specialist's tool calls land in one
merged transcript with globally-unique call_ids, a real V.verify() call resolves every
citation exactly as it does for a single-agent run.

No credential, no network beyond the in-process TestClient, no real provider -- same
scripted-stub style as tests/test_genai_loop.py and tests/test_genai_progress.py.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("fastapi")

if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from genai import config, intents  # noqa: E402
from genai import loop as L  # noqa: E402
from genai import orchestrator as O  # noqa: E402
from genai import tools as T  # noqa: E402
from genai import verifier as V  # noqa: E402
from genai.api_client import PulseTwinAPI  # noqa: E402
from genai.api_client import testclient_transport as _tc_transport  # noqa: E402
from genai.finding import Citation, Claim, Finding  # noqa: E402
from serve.main import app  # noqa: E402

PLANTS = ["plant_a", "plant_b"]


# ------------------------------------------------------------------ provider stubs
@dataclass
class _Fn:
    name: str
    arguments: str


@dataclass
class _Call:
    id: str
    function: _Fn


@dataclass
class _Msg:
    content: str | None = None
    tool_calls: list[_Call] = field(default_factory=list)


@dataclass
class _Choice:
    message: _Msg
    finish_reason: str


@dataclass
class _Usage:
    prompt_tokens: int = 100
    completion_tokens: int = 20


@dataclass
class _Resp:
    choices: list[_Choice]
    usage: _Usage = field(default_factory=_Usage)


class StubClient:
    """Replays a script of responses across EVERY run_loop call the orchestrator makes --
    one script, shared by all four stages, consumed in call order."""

    def __init__(self, script: list[_Resp]) -> None:
        self._script = list(script)
        self.seen_requests: list[dict[str, Any]] = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs: Any) -> _Resp:
        self.seen_requests.append(kwargs)
        if not self._script:
            return _Resp([_Choice(_Msg(content="{}"), "stop")])
        return self._script.pop(0)


class FailingClient:
    """Raises on every call -- for the fatal-failure abort path."""

    def __init__(self) -> None:
        self.attempts = 0
        self.chat = self
        self.completions = self

    def create(self, **kwargs: Any) -> _Resp:
        self.attempts += 1
        raise RuntimeError("provider rejected the credential")


def _tool_turn(*specs: tuple[str, dict]) -> _Resp:
    calls = [_Call(f"prov_{i}", _Fn(name, json.dumps(args)))
             for i, (name, args) in enumerate(specs)]
    return _Resp([_Choice(_Msg(tool_calls=calls), "tool_calls")])


def _final(text: str) -> _Resp:
    return _Resp([_Choice(_Msg(content=text), "stop")])


@pytest.fixture(scope="module")
def registry():
    return T.build_registry(PLANTS)


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as c:
        yield PulseTwinAPI("http://testserver", "leadership", "orchtest",
                           transport=_tc_transport(c))


def _run(client, api, registry, **kw):
    return O.run_orchestrated_incident(
        client, api, registry, plant_id="plant_a",
        question="why did the constraint move?", acting_role="leadership", **kw)


# --------------------------------------------------------------------- select_specialists
def test_incident_routes_to_all_three_specialists() -> None:
    assert O.select_specialists(intents.INCIDENT, "anything") == [
        "reliability", "production", "risk"]


@pytest.mark.parametrize("intent", [intents.ASK, intents.HANDOVER,
                                    intents.WHATIF])
def test_every_other_intent_selects_nothing(intent) -> None:
    assert O.select_specialists(intent, "anything") == []


def test_max_orchestration_specialists_truncates(monkeypatch) -> None:
    monkeypatch.setattr(config, "MAX_ORCHESTRATION_SPECIALISTS", 1)
    assert O.select_specialists(intents.INCIDENT, "q") == ["reliability"]


def test_max_orchestration_specialists_floor_is_one(monkeypatch) -> None:
    monkeypatch.setattr(config, "MAX_ORCHESTRATION_SPECIALISTS", 0)
    assert O.select_specialists(intents.INCIDENT, "q") == ["reliability"]


# ------------------------------------------------------------- registry invariant
def test_every_specialist_tool_is_in_the_real_registry(registry) -> None:
    """A typo'd tool name in a SpecialistSpec must fail loudly HERE, not as a silent
    no-op at runtime (the specialist's sub-registry dict comprehension only pulls names
    that exist in `registry`, so a typo would otherwise just quietly shrink a specialist's
    toolset with no error anywhere)."""
    for spec in O.SPECIALISTS.values():
        for name in spec.tool_names:
            assert name in registry, f"{spec.key} references unknown tool {name!r}"


def test_every_specialist_can_search_engineering_knowledge() -> None:
    """ADR-046: reference documentation is not plant- or domain-scoped, so every
    specialist -- not just one -- should be able to reach for it."""
    for spec in O.SPECIALISTS.values():
        assert "search_engineering_knowledge" in spec.tool_names, spec.key


def test_wallclock_budgets_partition_the_incident_ceiling() -> None:
    """ADR-063/064: the incident run is five sequential stages with their own ceiling
    (the single-agent 300 s could not hold a reasoning synthesis plus a simulation); the
    stages must still sum to it exactly, and it must stay inside the stale-worker verdict
    so a slow run is never mistaken for a dead one."""
    from serve.routers.triage import WORKER_STALE_S
    total = (sum(s.wallclock_cap_s for s in O.SPECIALISTS.values())
             + O.PLANNER.wallclock_cap_s + O._SYNTHESIS_WALLCLOCK_CAP_S)
    assert total == config.INCIDENT_WALLCLOCK_CAP_S
    assert config.INCIDENT_WALLCLOCK_CAP_S < WORKER_STALE_S
    assert config.RUN_WALLCLOCK_CAP_S < WORKER_STALE_S


# --------------------------------------------------------------------------- end to end
def test_happy_path_produces_a_globally_sequential_transcript_the_real_verifier_accepts(
    registry, api
) -> None:
    script = [
        # reliability
        _tool_turn(("get_system_health", {"plant_id": "plant_a"})),
        _final("{}"),
        # production
        _tool_turn(("get_bottleneck_current", {"plant_id": "plant_a"})),
        _final("{}"),
        # risk
        _tool_turn(("get_recent_alerts", {"plant_id": "plant_a", "alerts_only": True,
                                          "limit": 5})),
        _final("{}"),
        # ADR-064 simulation planner: declines to simulate (answer, evidence re-ask, answer)
        _final("{}"), _final("{}"),
        # synthesis (no tools; just the final text the orchestrator returns as .content)
        _final('{"summary": "s", "claims": [], "recommendation": "r"}'),
    ]
    out = _run(StubClient(script), api, registry)

    assert out.status == "finished"
    assert [r.call_id for r in out.records] == ["call_0001", "call_0002", "call_0003"]
    assert [r.agent for r in out.records] == ["reliability", "production", "risk"]
    assert [a["key"] for a in out.agents_used] == ["reliability", "production", "risk",
                                                   "simulation"]
    assert [a["status"] for a in out.agents_used] == ["ok", "ok", "ok", "ok"]
    assert out.content == '{"summary": "s", "claims": [], "recommendation": "r"}'

    # Build a Finding that cites ONE real number gathered by EACH specialist, and prove a
    # real verifier.verify() call resolves every citation against the merged transcript
    # with no knowledge of "agent" at all -- this is the whole claim this feature rests on.
    transcript = out.transcript
    reliability_data = transcript["call_0001"]["data"]      # get_system_health
    production_data = transcript["call_0002"]["data"]       # get_bottleneck_current
    risk_data = transcript["call_0003"]["data"]              # get_recent_alerts (a list)

    claims = [
        Claim(
            id="c1", text="0 predictions in the last hour",
            value=float(reliability_data["predictions_last_hour"]),
            unit="count", provenance="measured",
            citation=Citation(call_id="call_0001", pointer="/data/predictions_last_hour"),
        ),
        Claim(
            id="c2", text=f"the constraint is at station {production_data['current_station']}",
            value=float(production_data["current_station"]),
            unit="station_id", provenance="measured",
            citation=Citation(call_id="call_0002", pointer="/data/current_station"),
        ),
        Claim(
            id="c3", text="one alert fired",
            value=float(risk_data[0]["alert_fired"]),
            unit="count", provenance="measured",
            citation=Citation(call_id="call_0003", pointer="/data/0/alert_fired"),
        ),
    ]
    finding = Finding(question="q", summary="ok", claims=claims, recommendation="r")
    ver, served = V.verify(finding, transcript, station_ids=set())

    assert ver.status == V.VERIFIED
    assert ver.claims_total == ver.claims_verified == 3
    assert all(v.ok for v in ver.claim_verdicts)


def test_a_specialist_that_hits_its_own_iteration_cap_is_marked_partial_and_synthesis_still_runs(
    registry, api, monkeypatch
) -> None:
    """Graceful degradation: an individual specialist's cap is not a run failure."""
    monkeypatch.setitem(
        O.SPECIALISTS, "risk",
        dataclasses.replace(O.SPECIALISTS["risk"], max_iterations=1),
    )
    script = [
        # reliability finishes cleanly (answer, then again after the evidence-first re-ask)
        _final("{}"), _final("{}"),
        # production finishes cleanly
        _final("{}"), _final("{}"),
        # risk: a tool call that never gets a chance to finish within max_iterations=1
        _tool_turn(("get_recent_alerts", {"plant_id": "plant_a"})),
        # ADR-064 simulation planner declines to simulate
        _final("{}"), _final("{}"),
        # synthesis
        _final('{"summary": "s", "claims": [], "recommendation": "r"}'),
    ]
    out = _run(StubClient(script), api, registry)

    statuses = {a["key"]: a["status"] for a in out.agents_used}
    assert statuses["reliability"] == "ok"
    assert statuses["production"] == "ok"
    assert statuses["risk"] == "partial"
    # Synthesis still ran -- the orchestrator did not abort on a non-fatal cap.
    assert out.status == "finished"
    assert out.content == '{"summary": "s", "claims": [], "recommendation": "r"}'


def test_a_fatal_specialist_failure_aborts_before_the_remaining_stages(
    registry, api
) -> None:
    """A 401/403/404-shaped failure cannot be fixed by trying the next specialist or the
    synthesizer -- every later stage would fail identically. Assert they are never even
    attempted."""
    client = FailingClient()
    out = _run(client, api, registry, classify_error=lambda e: "provider_status_401")

    assert out.status == "failed"
    assert out.error == "provider_status_401"
    # Exactly ONE request: reliability's first attempt. A 401 does not retry within
    # run_loop, and the orchestrator must not proceed to production, risk, or synthesis.
    assert client.attempts == 1
    keys_attempted = [a["key"] for a in out.agents_used if a["status"] != "pending"]
    assert keys_attempted == ["reliability"]


def test_the_synthesizer_gets_no_tools(registry, api) -> None:
    """The synthesizer reconciles evidence already gathered -- it must not be able to
    reach the network for a fresh, uncited number."""
    script = [
        _final("{}"),  # reliability
        _final("{}"),  # production
        _final("{}"),  # risk
        _final('{"summary": "s", "claims": [], "recommendation": "r"}'),  # synthesis
    ]
    client = StubClient(script)
    _run(client, api, registry)

    synthesis_request = client.seen_requests[-1]
    assert synthesis_request["tools"] == []


def test_the_synthesizer_sees_every_specialist_record_even_when_evidence_is_large() -> None:
    """Truncating the evidence as ONE list kept head+tail and dropped whole records from
    the middle -- measured, two of six results vanished and the synthesizer cited their
    call_ids blind. Each record is now shrunk to an equal share instead."""
    recs = [L.ToolRecord(call_id=f"call_{i:04d}", provider_call_id="", seq=i, name="t",
                         arguments={}, result={"rows": [{"k": j, "v": "x" * 40}
                                                        for j in range(400)]},
                         duration_ms=1.0, phase="p") for i in range(6)]
    text = O._render_evidence(recs, [])
    assert all(f'"call_id": "call_{i:04d}"' in text for i in range(6))


def test_the_simulation_planner_runs_after_the_specialists_and_before_synthesis(
        registry, api) -> None:
    """ADR-064. The planner's experiment call is tagged `simulation`, sequenced after the
    specialists' calls, and visible to the synthesizer's evidence."""
    script = [
        _final("{}"), _final("{}"),          # reliability: no tool, re-asked, answers
        _final("{}"), _final("{}"),          # production
        _final("{}"), _final("{}"),          # risk
        _tool_turn(("get_similar_incidents", {"plant_id": "plant_a"})),
        _final("{}"),
        _final('{"summary": "s", "claims": [], "recommendation": "r"}'),
    ]
    out = _run(StubClient(script), api, registry)
    assert out.status == "finished"
    assert [(r.agent, r.name) for r in out.records] == [("simulation", "get_similar_incidents")]
    assert out.agents_used[-1] == {"key": "simulation", "label": "Simulation planner",
                                   "status": "ok", "tool_calls": 1}


def test_every_citable_pointer_resolves_against_the_recorded_result() -> None:
    """ADR-064 follow-up: the synthesizer copies pointers instead of guessing them."""
    from genai import verifier as V
    result = {"ok": True, "data": {"stations": [{"station_id": 12, "util": 0.93,
                                                 "provenance": "inferred", "ci_low": 0.9,
                                                 "ci_high": 0.96, "name": "Body"}],
                                   "count": 1, "flag": True, "a/b": 2.5,
                                   "_truncated": {"omitted": 4}}}
    rows = O.citable_numbers(result)
    assert rows and all(V.resolve_pointer(result, p) == v for p, v, *_ in rows)
    assert ["/data/stations/0/util", 0.93, "inferred"] in rows
    assert not any("_truncated" in p or p.endswith("/flag") for p, *_ in rows)
    assert ["/data/a~1b", 2.5] in rows
