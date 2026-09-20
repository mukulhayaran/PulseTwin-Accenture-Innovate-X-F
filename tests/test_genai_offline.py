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
