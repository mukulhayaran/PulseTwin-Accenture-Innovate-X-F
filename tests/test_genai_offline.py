"""The no-LLM triage path, end to end against the real artifact.

This is the integration test that proves the verifier works on REAL data rather than
only on handwritten fixtures: the deterministic path assembles its finding in code from
live tool results, and then goes through the identical gate the agent's output does.

If this ever fails, either the API shape drifted or the verifier became too strict --
both are things worth failing a build over.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("fastapi")

if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from genai import offline_triage, tools as T, verifier as V  # noqa: E402
from genai.api_client import PulseTwinAPI  # noqa: E402
from genai.api_client import testclient_transport as _tc_transport  # noqa: E402
from serve.main import app  # noqa: E402

PLANTS = ["plant_a", "plant_b"]


@pytest.fixture(scope="module")
def ctx():
    with TestClient(app) as c:
        registry = T.build_registry(PLANTS)
        api = PulseTwinAPI("http://testserver", "leadership", "offline-test",
                           transport=_tc_transport(c))
        yield registry, api, c


def _station_ids(client, plant: str) -> set[int]:
    r = client.get(f"/api/config/stations?plant_id={plant}")
    return {s["station_id"] for s in r.json()["stations"]}


@pytest.mark.parametrize("plant", PLANTS)
def test_offline_triage_passes_the_verifier(ctx, plant) -> None:
    registry, api, client = ctx
    loop, finding = offline_triage.run(
        registry, api, plant_id=plant,
        question="The constraint moved. Investigate and recommend.")

    ver, served = V.verify(finding, loop.transcript,
                           station_ids=_station_ids(client, plant))

    assert ver.status in (V.VERIFIED, V.VERIFIED_WITH_REDACTIONS), (
        f"{ver.reject_reason} :: "
        f"{[(v.claim_id, v.code, v.detail) for v in ver.claim_verdicts if not v.ok]}"
    )
    assert ver.claims_verified >= 4
    assert served.claims


@pytest.mark.parametrize("plant", PLANTS)
def test_offline_triage_is_labelled_as_such(ctx, plant) -> None:
    """Labelled as emphatically as the SPC fallback -- never a pretence."""
    registry, api, _ = ctx
    _, finding = offline_triage.run(registry, api, plant_id=plant, question="q")
    assert offline_triage.BANNER in finding.summary


@pytest.mark.parametrize("plant", PLANTS)
def test_every_claim_cites_a_real_call(ctx, plant) -> None:
    registry, api, _ = ctx
    loop, finding = offline_triage.run(registry, api, plant_id=plant, question="q")
    for claim in finding.claims:
        assert claim.citation.call_id in loop.transcript
        V.resolve_pointer(loop.transcript[claim.citation.call_id],
                          claim.citation.pointer)


def test_offline_triage_is_deterministic(ctx) -> None:
    """Same inputs, byte-identical finding. The model is not reproducible; this is."""
    registry, api, _ = ctx
    first = None
    for _ in range(3):
        _, finding = offline_triage.run(registry, api, plant_id="plant_a", question="q")
        blob = json.dumps(finding.to_dict(), sort_keys=True, default=str)
        first = first or blob
        assert blob == first


def test_provenance_is_carried_from_the_row_not_assumed(ctx) -> None:
    """An inferred station row must produce an inferred claim carrying its interval."""
    registry, api, _ = ctx
    loop, finding = offline_triage.run(registry, api, plant_id="plant_a", question="q")
    for claim in finding.claims:
        row = V._parent_of(loop.transcript[claim.citation.call_id],
                           claim.citation.pointer)
        if isinstance(row, dict) and isinstance(row.get("provenance"), str):
            assert claim.provenance == row["provenance"]
            if claim.provenance == "inferred":
                assert claim.ci_low is not None and claim.ci_high is not None


def test_a_restricted_role_reports_rather_than_invents(ctx) -> None:
    """The demo's best moment: the agent declines instead of estimating.

    Run the deterministic path as `supervisor`, which cannot read /api/system/health.
    The refusal must land in `unanswered`, and no claim may cite that call.
    """
    registry, _, client = ctx
    api = PulseTwinAPI("http://testserver", "supervisor", "restricted",
                       transport=_tc_transport(client))
    loop, finding = offline_triage.run(registry, api, plant_id="plant_a", question="q")

    assert any("get_system_health" in u for u in finding.unanswered), finding.unanswered
    ver, _ = V.verify(finding, loop.transcript,
                      station_ids=_station_ids(client, "plant_a"))
    assert ver.status in (V.VERIFIED, V.VERIFIED_WITH_REDACTIONS), ver.reject_reason


# --------------------------------------------------------------- named-action parity
#
# Every named action the copilot offers must answer with NO credential. A button that
# works only when a provider is reachable is a button that breaks on stage -- and the
# whole argument for this module is that the demo survives an outage with its guarantees
# intact, not with a weaker version of them.

PLANS = ["general", "handover", "whatif", "incident",
         "starvation", "paint_env", "tool_wear"]


def _grid_station(client, plant: str):
    rows = client.get(f"/api/whatif/stations?plant_id={plant}").json()
    return rows[0]["station_id"] if rows else None


@pytest.mark.parametrize("plant", PLANTS)
@pytest.mark.parametrize("plan", PLANS)
def test_every_plan_passes_the_same_verifier(ctx, plant, plan) -> None:
    registry, api, client = ctx
    loop, finding = offline_triage.run_plan(
        plan, registry, api, plant_id=plant, question=f"[{plan}] parity",
        station_id=_grid_station(client, plant))
    ver, served = V.verify(finding, loop.transcript,
                           station_ids=_station_ids(client, plant))

    assert ver.status != V.REJECTED, f"{plan}/{plant}: {ver.reject_reason}"
    assert ver.claims_total > 0
    assert served.summary and served.recommendation


@pytest.mark.parametrize("plan", PLANS)
def test_every_plan_labels_itself_as_deterministic(ctx, plan) -> None:
    """Labelled as emphatically as the SPC fallback: an honest lesser thing, never a
    pretence that a model was involved."""
    registry, api, client = ctx
    _loop, finding = offline_triage.run_plan(
        plan, registry, api, plant_id="plant_a", question="label check",
        station_id=_grid_station(client, "plant_a"))
    assert offline_triage.BANNER in finding.summary or \
        offline_triage.BANNER in finding.recommendation


@pytest.mark.parametrize("plan", PLANS)
def test_every_plan_cites_only_calls_it_actually_made(ctx, plan) -> None:
    registry, api, client = ctx
    loop, finding = offline_triage.run_plan(
        plan, registry, api, plant_id="plant_a", question="citation check",
        station_id=_grid_station(client, "plant_a"))
    made = {r.call_id for r in loop.records}
    for claim in finding.claims:
        assert claim.citation.call_id in made, f"{plan} cited {claim.citation.call_id}"


def test_an_uncovered_station_refuses_and_still_serves_something(ctx) -> None:
    """A refusal needs evidence too. The verifier rejects a finding with no claims, and a
    rejected run serves NOTHING -- so an un-evidenced "simulation unavailable" would
    reach the operator as a blank panel, which is worse than the refusal it was trying to
    deliver."""
    registry, api, client = ctx
    loop, finding = offline_triage.run_plan(
        "whatif", registry, api, plant_id="plant_a", question="what if?",
        station_id=999_999)
    ver, served = V.verify(finding, loop.transcript,
                           station_ids=_station_ids(client, "plant_a"))

    assert ver.status != V.REJECTED
    assert "SIMULATION UNAVAILABLE" in served.recommendation
    assert any("not in the precomputed what-if grid" in u for u in finding.unanswered)


def test_the_whatif_plan_never_substitutes_a_different_station(ctx) -> None:
    """"Do not invent simulation results" -- borrowing a covered station's response for
    an uncovered one would be a plausible-looking lie."""
    registry, api, _client = ctx
    _loop, finding = offline_triage.run_plan(
        "whatif", registry, api, plant_id="plant_a", question="what if?",
        station_id=999_999)
    assert not any(c.unit == "uph" for c in finding.claims)


def test_intents_and_plans_are_in_sync() -> None:
    """Every intent names a plan that exists."""
    from genai import intents

    named = {i.plan for i in intents.INTENTS.values()}
    missing = named - set(offline_triage.PLANS)
    assert not missing, f"intents name missing plans: {missing}"


def test_a_conversation_turn_answers_from_line_data_and_the_knowledge_base(ctx) -> None:
    """The copilot's `ask` turn is RAG plus live data: retrieved passages are quoted
    verbatim beside the line claims, and every quote passes the substring check."""
    registry, api, client = ctx
    loop, finding = offline_triage.run_plan(
        "general", registry, api, plant_id="plant_a",
        question="torque specification fastening verification")
    ver, served = V.verify(finding, loop.transcript,
                           station_ids=_station_ids(client, "plant_a"))
    assert ver.status != V.REJECTED, ver.reject_reason
    assert finding.claims and finding.knowledge_citations
    assert ver.knowledge_citations_verified == len(finding.knowledge_citations)
    assert served.knowledge_citations


def test_the_whatif_plan_runs_a_fresh_experiment_not_the_grid(ctx, monkeypatch) -> None:
    """ADR-064. Measured live: the plan passed a `profile` argument the tool schema had
    dropped, every experiment call failed `invalid_arguments`, and the plan fell back to
    the grid -- while a grid-only assertion kept passing. Pin the PRIMARY path."""
    registry, _shared_api, client = ctx
    # Its own run: the module's shared client has already spent its per-run experiment
    # budget on earlier plans (the server refuses a 4th -- correctly). And other tests'
    # real experiments may still hold the concurrency slots; this test is about the path
    # taken when both allow it.
    import secrets
    from serve.routers import experiments as E
    monkeypatch.setitem(E.LIMITS, "max_concurrent", 50)
    api = PulseTwinAPI("http://testserver", "leadership", f"fresh-{secrets.token_hex(4)}",
                       transport=_tc_transport(client))
    station = client.get("/api/bottleneck/current?plant_id=plant_a").json()["current_station"]
    loop, finding = offline_triage.run_plan(
        "whatif", registry, api, plant_id="plant_a", question="what if?", station_id=station)
    names = [r.name for r in loop.records]
    assert "run_simulation_experiment" in names and "simulate_whatif" not in names,         finding.unanswered
    assert not any("unavailable" in u or "invalid_arguments" in u for u in finding.unanswered)
    assert finding.decision in ("recommend", "too_uncertain", "no_improvement")
    assert "FRESH SIMULATION" in finding.summary
    projected = [c for c in finding.claims if c.provenance == "inferred"]
    assert projected and all(c.ci_low is not None for c in projected)
    ver, _ = V.verify(finding, loop.transcript, station_ids=_station_ids(client, "plant_a"))
    assert ver.status != V.REJECTED, ver.reject_reason
