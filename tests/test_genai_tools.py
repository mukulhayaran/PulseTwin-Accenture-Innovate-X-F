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
}

# Unit-level and operator-bearing surfaces that must NEVER become tools.
FORBIDDEN_SUBSTRINGS = ("/api/units", "/api/trace/", "/api/audit", "/api/admin",
                        "/api/stream", "/api/feedback", "/api/alerts")


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


# ------------------------------------------------------------------ egress allowlist
def test_registry_paths_equal_the_approved_allowlist(registry) -> None:
    assert T.approved_paths(registry) == APPROVED


def test_no_tool_touches_a_forbidden_surface(registry) -> None:
    for spec in registry.values():
        for bad in FORBIDDEN_SUBSTRINGS:
            assert bad not in spec.path, f"{spec.name} reaches {spec.path}"


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
