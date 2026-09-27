"""The four deterministic decision-intelligence endpoints.

These are the evidence the GenAI tier reasons over, so they are the layer that has to be
trustworthy: the verifier can only check a claim against a tool result, which means the
tool result itself is never checked by anything except these tests.

Run against the real committed artifact, both plants, no mocks.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("fastapi")

if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from serve.main import app  # noqa: E402
from serve.routers import intelligence as I  # noqa: E402

PLANTS = ["plant_a", "plant_b"]
SUPERVISOR = {"X-PulseTwin-Role": "supervisor"}
MANAGER = {"X-PulseTwin-Role": "manager"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _a_unit(client, plant):
    rows = client.get(f"/api/units?plant_id={plant}&alerts_only=true&limit=1").json()
    assert rows, f"{plant} has no alerting units"
    return rows[0]["unit_id"]


# ------------------------------------------------------------------------ alert triage
@pytest.mark.parametrize("plant", PLANTS)
def test_triage_classifies_every_candidate(client, plant) -> None:
    body = client.get(f"/api/alerts/triage?plant_id={plant}&limit=100",
                      headers=SUPERVISOR).json()
    assert body["count"] > 0
    for row in body["alerts"]:
        assert row["triage_class"] in I.TRIAGE_CLASSES
        # A classification with no reason is a silent default, which is the thing this
        # endpoint exists to not be.
        assert row["reasons"], f"{row['unit_id']} classified with no reason"
        assert row["factors"]


@pytest.mark.parametrize("plant", PLANTS)
def test_the_candidate_set_is_wider_than_the_alerts_that_fired(client, plant) -> None:
    """`alert_fired = 1` implies risk >= budget_threshold BY CONSTRUCTION, so triaging
    only fired alerts makes `severity >= 1.0` a tautology and every row lands CRITICAL.
    The population is everything the COST model says is worth inspecting."""
    body = client.get(f"/api/alerts/triage?plant_id={plant}&limit=500",
                      headers=SUPERVISOR).json()
    assert body["already_fired"] < body["count"], (
        "triage is only seeing alerts that already fired; severity cannot discriminate")


@pytest.mark.parametrize("plant", PLANTS)
def test_severity_actually_separates(client, plant) -> None:
    body = client.get(f"/api/alerts/triage?plant_id={plant}&limit=500",
                      headers=SUPERVISOR).json()
    classes = {r["triage_class"] for r in body["alerts"]}
    assert len(classes) > 1, f"every candidate landed in one class: {classes}"


def test_a_degenerate_dimension_is_reported_not_hidden(client) -> None:
    """plant_a's calibration plateaus: all 113 fired alerts share ONE calibrated risk. A
    ranking built on a constant is arbitrary, and saying so is the same posture as the
    published negative results in ADR-030/031."""
    body = client.get("/api/alerts/triage?plant_id=plant_a&limit=113",
                      headers=SUPERVISOR).json()
    d = body["diagnostics"]
    assert "severity_discriminates" in d
    assert isinstance(d["distinct_risk_values"], int)
    if not d["severity_discriminates"]:
        assert d["notes"], "a non-discriminating dimension must carry an explanation"


def test_the_confidence_interval_is_flagged_as_derived(client) -> None:
    """Measured across both plants, every distinct calibrated_risk maps to exactly one
    interval width -- so the interval carries no information the point estimate does not.
    Ranking on it would be double-counting one number as two pieces of evidence."""
    body = client.get("/api/alerts/triage?plant_id=plant_a&limit=5",
                      headers=SUPERVISOR).json()
    assert body["diagnostics"]["confidence_interval_is_derived"] is True
    ci = next(f for f in body["alerts"][0]["factors"]
              if f["name"] == "confidence_interval_width")
    assert ci["informative"] is False


def test_low_confidence_never_swallows_a_critical(client) -> None:
    """A severe alert must not be talked down by a sensor problem: a sensor problem is a
    reason to look harder, not less."""
    body = client.get("/api/alerts/triage?plant_id=plant_a&limit=500",
                      headers=SUPERVISOR).json()
    for row in body["alerts"]:
        if row["triage_class"] == I.SUPPRESS:
            assert row["severity_x_threshold"] < I.SEVERITY_CRITICAL


def test_suppressed_rows_are_returned_not_dropped(client) -> None:
    """"Do not silently suppress important alerts" -- LOW_CONFIDENCE is a RANK."""
    body = client.get("/api/alerts/triage?plant_id=plant_a&limit=500",
                      headers=SUPERVISOR).json()
    assert len(body["alerts"]) == body["count"]
    assert sum(body["counts_by_class"].values()) == body["count"]


def test_triage_keys_on_the_implicated_station_not_the_last_one(client) -> None:
    """`current_station` is MAX(genealogy.station_id): for a finished unit that is simply
    the end of the line, identically for every row. The station the top-ranked factor
    names is the one the risk is actually about."""
    body = client.get("/api/alerts/triage?plant_id=plant_a&limit=200",
                      headers=SUPERVISOR).json()
    implicated = {r["implicated_station"] for r in body["alerts"]}
    assert len(implicated) > 1, "every row blamed the same station"


def test_triage_is_supervisor_gated(client) -> None:
    assert client.get("/api/alerts/triage?plant_id=plant_a").status_code in (200, 400)
    r = client.get("/api/alerts/triage?plant_id=plant_a",
                   headers={"X-PulseTwin-Role": "nobody"})
    assert r.status_code == 400


def test_unknown_plant_is_404(client) -> None:
    assert client.get("/api/alerts/triage?plant_id=plant_zz",
                      headers=SUPERVISOR).status_code == 404


# ------------------------------------------------------------------------ data quality
def test_valid_ranges_mirror_the_ingestion_path() -> None:
    """serve/ cannot import ingestion/ (the import wall), so the ranges are duplicated.
    A drift between them means the live path and the reported score disagree about what
    a valid reading is."""
    from ingestion.data_quality import VALID_RANGES as LIVE

    assert I.VALID_RANGES == LIVE


@pytest.mark.parametrize("plant", PLANTS)
def test_the_simulator_output_is_inside_those_ranges(client, plant) -> None:
    """The bound that caught a real bug: weld current was capped at 2,000 A while
    offline/simulator/physics.py generates ~8,200-8,900 A, so every weld reading in the
    live ingestion path was being scored invalid and dropped from feature computation."""
    body = client.get(f"/api/system/data-quality?plant_id={plant}",
                      headers=SUPERVISOR).json()
    for station in body["stations"]:
        assert station["validity"] == 1.0, (
            f"S{station['station_id']} reports invalid readings against VALID_RANGES; "
            f"the range is probably wrong, not the simulator")


@pytest.mark.parametrize("plant", PLANTS)
def test_timeliness_is_reported_unavailable_not_invented(client, plant) -> None:
    """The artifact stores event timestamps but no ARRIVAL timestamps, so timeliness
    cannot be computed. Filling it with a plausible number is the failure mode."""
    body = client.get(f"/api/system/data-quality?plant_id={plant}",
                      headers=SUPERVISOR).json()
    assert body["components_unavailable"] == ["timeliness"]
    assert all(s["timeliness"] is None for s in body["stations"])
    assert "completeness" in body["components_used"]


def test_a_perfect_score_says_what_it_actually_means(client) -> None:
    """Scored over clean simulator output the answer is ~1.0 and should be. A perfect
    score means "no faults were injected", not "the line is healthy"."""
    body = client.get("/api/system/data-quality?plant_id=plant_a",
                      headers=SUPERVISOR).json()
    if body["plant_score"] and body["plant_score"] > 0.999:
        assert body["notes"], "a near-perfect score must carry its caveat"


# ------------------------------------------------------------------------ shift summary
@pytest.mark.parametrize("plant", PLANTS)
def test_shift_summary_reports_one_window(client, plant) -> None:
    body = client.get(f"/api/shift/summary?plant_id={plant}", headers=MANAGER).json()
    shift = body["shift"]
    assert shift["window_start"] < shift["window_end"]
    assert body["production"]["units"] > 0
    assert isinstance(body["bottleneck"]["shift_events"], int)


def test_the_shift_window_is_the_alert_budget_window(client) -> None:
    """A handover that counted alerts over a different window than the supervisor's
    meter would be impossible to reconcile on stage."""
    from serve.main import _shift_window
    from serve.shift import shift_window

    assert _shift_window is shift_window
    body = client.get("/api/shift/summary?plant_id=plant_a", headers=MANAGER).json()
    anchor = body["shift"]["anchor_ts"]
    assert shift_window(anchor) == (body["shift"]["window_start"],
                                    body["shift"]["window_end"])


def test_bottleneck_agreement_is_never_a_perfect_one(client) -> None:
    """CLAUDE.md / ADR-029: detection accuracy reading 1.0 means ground truth has
    collapsed back onto the detector, because they use deliberately different windows."""
    for plant in PLANTS:
        body = client.get(f"/api/shift/summary?plant_id={plant}", headers=MANAGER).json()
        agreement = body["bottleneck"]["detector_agreement"]
        if agreement is not None:
            assert agreement < 1.0, f"{plant} detector agreement is {agreement}"


def test_shift_summary_is_manager_gated(client) -> None:
    assert client.get("/api/shift/summary?plant_id=plant_a",
                      headers=SUPERVISOR).status_code == 403


def test_a_malformed_anchor_is_422_not_500(client) -> None:
    r = client.get("/api/shift/summary?plant_id=plant_a&at_ts=not-a-time",
                   headers=MANAGER)
    assert r.status_code == 422


# ----------------------------------------------------------------------- evidence graph
@pytest.mark.parametrize("plant", PLANTS)
def test_the_graph_names_only_implicated_stations(client, plant) -> None:
    """Substring-matching the signal name instead of parsing the feature's `s<N>_` prefix
    produced a 70-node graph naming all 30 stations. A graph that indicts everything
    explains nothing."""
    unit = _a_unit(client, plant)
    g = client.get(f"/api/trace/evidence-graph/{unit}?plant_id={plant}",
                   headers=SUPERVISOR).json()
    stations = [n for n in g["nodes"] if n["kind"] == "station"]
    total = client.get(f"/api/config/stations?plant_id={plant}").json()["stations"]
    assert 0 < len(stations) < len(total) / 2


@pytest.mark.parametrize("plant", PLANTS)
def test_every_edge_carries_its_provenance(client, plant) -> None:
    unit = _a_unit(client, plant)
    g = client.get(f"/api/trace/evidence-graph/{unit}?plant_id={plant}",
                   headers=SUPERVISOR).json()
    assert g["edges"]
    for e in g["edges"]:
        assert e["provenance"] in ("measured", "inferred")
        assert e["evidence_source"]
        assert {"from", "to", "relation"} <= set(e)


@pytest.mark.parametrize("plant", PLANTS)
def test_the_graph_refuses_to_claim_causation(client, plant) -> None:
    """The most expensive sentence this demo could contain, for an industrial audience."""
    unit = _a_unit(client, plant)
    g = client.get(f"/api/trace/evidence-graph/{unit}?plant_id={plant}",
                   headers=SUPERVISOR).json()
    assert g["causality"] == "association"
    assert g["causality_statement"] == I.ASSOCIATION_DISCLAIMER
    assert "causal relationship is not established" in g["causality_statement"]
    assert not any("cause" == e["relation"] for e in g["edges"])


@pytest.mark.parametrize("plant", PLANTS)
def test_every_edge_connects_two_real_nodes(client, plant) -> None:
    unit = _a_unit(client, plant)
    g = client.get(f"/api/trace/evidence-graph/{unit}?plant_id={plant}",
                   headers=SUPERVISOR).json()
    ids = {n["id"] for n in g["nodes"]}
    for e in g["edges"]:
        assert e["from"] in ids and e["to"] in ids, f"dangling edge {e}"


@pytest.mark.parametrize("plant", PLANTS)
def test_the_graph_never_surfaces_an_operator(client, plant) -> None:
    """CLAUDE.md §3, checked on the whole payload rather than the fields we expect.

    The oracle is `genai/verifier.py`'s own operator patterns, not a hand-written string
    match: that regex set IS this project's definition of the contract, and a test with
    a second, looser definition would either miss a real leak or fail on something legal.

    Note what this deliberately does NOT forbid: a feature named `s15_checklist_time_s`.
    That is a station-level timing already surfaced by `/api/units/{id}` and rendered in
    the supervisor's factor bar. The contract bans operator IDENTITY, and the evidence
    graph never reads the `checklists` table where `operator_group` actually lives.
    """
    import json as _json

    from genai.verifier import _contains_operator_identity

    unit = _a_unit(client, plant)
    payload = client.get(f"/api/trace/evidence-graph/{unit}?plant_id={plant}",
                         headers=SUPERVISOR).json()
    blob = _json.dumps(payload)
    assert not _contains_operator_identity(blob), "operator identity in the graph"
    # `operator_group` may only ever appear in its opaque bucket form.
    assert "operator_group" not in blob or "operator_group_" in blob


def test_an_unknown_unit_is_404(client) -> None:
    assert client.get("/api/trace/evidence-graph/NOPE?plant_id=plant_a",
                      headers=SUPERVISOR).status_code == 404


# ---------------------------------------------------------------- the metrics blob fix
@pytest.mark.parametrize("plant", PLANTS)
def test_metrics_serves_the_blobs_it_stores(client, plant) -> None:
    """`serve/main.py::metrics` splats all of metrics_json into MetricsResponse and
    pydantic's default extra="ignore" silently dropped six keys -- including the
    bottleneck forecast detail and the masked-station R^2 that ADR-031 rests on."""
    body = client.get(f"/api/metrics?plant_id={plant}").json()
    assert body["bottleneck_report"].get("forecast_skill") is not None
    assert body["soft_sensor_validation"].get("mean_r2") is not None
    assert body["station_value"]


def test_a_shift_without_a_bottleneck_move_reports_no_agreement(client) -> None:
    for plant in PLANTS:
        b = client.get(f"/api/shift/summary?plant_id={plant}", headers=MANAGER).json()["bottleneck"]
        if b["shift_events"] == 0 and b["windows_in_shift"]:
            assert b["detector_agreement"] is None and b["detector_agreement_note"]
        else:
            assert b["detector_agreement_note"] is None
