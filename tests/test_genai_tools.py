"""The tool surface, exercised against the real artifact. No API key needed.

Two things under test, and the second is the governance one:

  1. every tool works, and its schema accepts a valid call and rejects an invalid one;
  2. the registry's path set EQUALS the approved data-egress allowlist -- so widening
     what leaves the network to a third-party API requires editing this file, the same
     trick tests/test_serve_imports.py plays on the import wall.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("fastapi")

if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from genai import tools as T  # noqa: E402
# Aliased: pytest's default `test*` glob would otherwise collect the helper itself.
from genai.api_client import PulseTwinAPI  # noqa: E402
from genai.api_client import testclient_transport as _tc_transport  # noqa: E402
from serve.main import app  # noqa: E402

PLANTS = ["plant_a", "plant_b"]

# The egress allowlist, written out longhand. If a tool is added, this fails first.
#
# Widened from 10 paths to 18 by ADR-038. The eight additions are listed second, with
# the argument in genai/tools.py's module docstring. The friction is the feature: this
# list is the only place the egress surface is stated in full, and growing it is a
# deliberate edit to a test rather than a side effect of adding a tool.
APPROVED = {
    ("GET", "/api/bottleneck/current"),
    ("GET", "/api/bottleneck/history"),
    ("GET", "/api/stations/stats"),
    ("GET", "/api/config/stations"),
    ("GET", "/api/metrics"),
    ("GET", "/api/whatif/stations"),
    ("POST", "/api/whatif"),
    ("GET", "/api/system/health"),
    ("GET", "/api/model/health"),
    ("GET", "/api/economics/roi"),
    # --- ADR-038
    ("GET", "/api/units"),
    ("GET", "/api/alerts/triage"),
    ("GET", "/api/trace/containment/{unit_id}"),
    ("GET", "/api/trace/evidence-graph/{unit_id}"),
    ("GET", "/api/shift/summary"),
    ("GET", "/api/system/data-quality"),
    ("GET", "/api/system/sensor-dropouts"),
    ("GET", "/api/economics/instrumentation-plan"),
}

# Surfaces that must NEVER become tools, whatever else is admitted.
#
# ADR-038 admitted unit-level RISK and CONTAINMENT, so "/api/units" and "/api/trace/"
# are no longer blanket-forbidden -- the specific paths are pinned in APPROVED above
# instead, which is strictly tighter than a prefix ban. What remains here is what no
# argument should reopen: the full per-unit journey, the audit record of who did what,
# anything mutating, and the unbounded stream.
FORBIDDEN_SUBSTRINGS = ("/api/units/{", "/api/audit", "/api/admin",
                        "/api/stream", "/api/feedback")

# Checklists carry `operator_group` (CLAUDE.md §3). No tool may reach a surface that
# reads them -- the strongest privacy defence is that the data never leaves at all.
FORBIDDEN_TABLES = ("checklist",)


@pytest.fixture(scope="module")
def registry():
    return T.build_registry(PLANTS)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _api(client, role="leadership"):
    return PulseTwinAPI("http://testserver", role, "testrun",
                        transport=_tc_transport(client))


def _an_alerting_unit(client, plant: str) -> str:
    rows = client.get(f"/api/units?plant_id={plant}&alerts_only=true&limit=1").json()
    assert rows, f"{plant} has no alerting units in the committed artifact"
    return rows[0]["unit_id"]


# ------------------------------------------------------------------ egress allowlist
def test_registry_paths_equal_the_approved_allowlist(registry) -> None:
    assert T.approved_paths(registry) == APPROVED


def test_no_tool_touches_a_forbidden_surface(registry) -> None:
    for spec in registry.values():
        for bad in FORBIDDEN_SUBSTRINGS:
            assert bad not in spec.path, f"{spec.name} reaches {spec.path}"


def test_no_tool_reads_an_operator_bearing_surface(registry) -> None:
    """CLAUDE.md §3. The verifier's C4 check is the second line of defence, not the first."""
    for spec in registry.values():
        blob = f"{spec.name} {spec.path} {spec.description}".lower()
        for bad in FORBIDDEN_TABLES:
            assert bad not in blob, f"{spec.name} may reach operator-bearing data"


def test_every_spec_declares_a_known_phase(registry) -> None:
    """The progress trail is driven by ToolSpec.phase; an unlabelled tool would render a
    blank step in the UI while the run appears to stall."""
    for spec in registry.values():
        assert spec.phase in T.PHASE_ORDER, f"{spec.name} has phase {spec.phase!r}"


def test_path_param_tools_declare_their_placeholders(registry) -> None:
    """A `{placeholder}` with no matching path_params entry would be sent to the API
    literally, and the 404 would look like a missing unit rather than a broken tool."""
    for spec in registry.values():
        placeholders = {
            part.split("}")[0] for part in spec.path.split("{")[1:]
        }
        assert placeholders == set(spec.path_params), (
            f"{spec.name}: path has {placeholders}, declares {spec.path_params}"
        )


def test_only_one_non_get_tool_exists(registry) -> None:
    """simulate_whatif is the sole POST, and it mutates no plant or model state."""
    posts = [s for s in registry.values() if s.method != "GET"]
    assert [s.name for s in posts] == ["simulate_whatif"]


def test_plant_ids_are_not_hardcoded(registry) -> None:
    """CLAUDE.md §1 -- swapping plant_a for plant_b must need no code edit."""
    other = T.build_registry(["plant_zz"])
    enum = other["get_station_stats"].parameters["properties"]["plant_id"]["enum"]
    assert enum == ["plant_zz"]


# ------------------------------------------------------------------------- execution
@pytest.mark.parametrize("plant", PLANTS)
def test_every_read_tool_returns_data(registry, client, plant) -> None:
    api = _api(client)
    for name, spec in registry.items():
        if spec.method != "GET":
            continue
        args = {"plant_id": plant}
        if name == "get_bottleneck_history":
            args["limit"] = 50
        if name == "get_economics_roi":
            args["scenario"] = "base"
        if name == "get_alert_triage":
            args["limit"] = 10
        if name == "get_recent_alerts":
            args.update({"alerts_only": True, "limit": 5})
        if spec.path_params:
            # Path-param tools need a real id, so take one from the plant's own alerts
            # rather than inventing one -- a made-up unit id would make this assert a
            # 404 handler instead of the tool.
            args["unit_id"] = _an_alerting_unit(client, plant)
        out = T.dispatch(registry, api, name, __import__("json").dumps(args))
        assert out.get("ok") is True, f"{name} -> {out}"


def test_simulate_whatif_returns_a_projection(registry, client) -> None:
    api = _api(client, role="manager")
    stations = T.dispatch(registry, api, "get_whatif_stations",
                          '{"plant_id": "plant_a"}')
    sid = stations["data"][0]["station_id"]
    out = T.dispatch(
        registry, api, "simulate_whatif",
        '{"plant_id": "plant_a", "station_id": %d, "buffer_delta": 2, '
        '"cycle_delta_pct": 0}' % sid,
    )
    assert out.get("ok") is True, out
    assert "projected" in out["data"]


# ------------------------------------------------------------------ schema validation
def test_missing_required_argument_is_a_structured_error(registry, client) -> None:
    out = T.dispatch(registry, _api(client), "get_station_stats", "{}")
    assert out["error"] == "invalid_arguments"
    assert "plant_id" in out["detail"]


def test_out_of_range_argument_is_rejected_locally(registry, client) -> None:
    out = T.dispatch(
        registry, _api(client), "simulate_whatif",
        '{"plant_id":"plant_a","station_id":12,"buffer_delta":99,"cycle_delta_pct":0}',
    )
    assert out["error"] == "invalid_arguments"
    assert "buffer_delta" in out["detail"]


def test_wrong_enum_value_is_rejected(registry, client) -> None:
    out = T.dispatch(registry, _api(client), "get_economics_roi",
                     '{"plant_id":"plant_a","scenario":"wishful"}')
    assert out["error"] == "invalid_arguments"


def test_unknown_tool_is_a_structured_error(registry, client) -> None:
    out = T.dispatch(registry, _api(client), "drop_database", "{}")
    assert out["error"] == "unknown_tool"


def test_malformed_arguments_json_does_not_raise(registry, client) -> None:
    out = T.dispatch(registry, _api(client), "get_station_stats", "{not json")
    assert out["error"] == "invalid_arguments_json"


# ------------------------------------------------------------------------------ RBAC
def test_role_gate_surfaces_as_a_readable_tool_result(registry, client) -> None:
    """A 403 must be a fact the agent can report, not an exception.

    This is the behaviour the /ops "Viewing as" selector exists to demonstrate: drop to
    supervisor and the agent says "I could not read economics at this role" instead of
    estimating a dollar figure.
    """
    out = T.dispatch(registry, _api(client, role="supervisor"), "get_economics_roi",
                     '{"plant_id":"plant_a","scenario":"base"}')
    assert out["error"] == "forbidden"
    assert "leadership" in out["detail"]
    assert "unanswered" in out["guidance"]


def test_actor_header_is_sent_on_every_call(client) -> None:
    """Agent actions must stay distinguishable from human ones in the audit trail."""
    seen: dict[str, str] = {}

    def spy(method, url, headers, body):
        seen.update(headers)
        return 200, {"ok": True}

    PulseTwinAPI("http://x", "manager", "run123", transport=spy).get("/api/health")
    assert seen["X-PulseTwin-Actor"] == "genai_triage/run123"
    assert seen["X-PulseTwin-Role"] == "manager"
