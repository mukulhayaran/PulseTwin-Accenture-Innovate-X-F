"""The closed tool registry -- which is also the data-egress allowlist.

Two things this file decides, and the second matters more:

1. What the agent can DO. Ten functions, nine of them GET.
2. What LEAVES THE NETWORK. Everything here is a plant-level aggregate: station
   utilisation, bottleneck windows, sourced cost constants, model metrics. Deliberately
   ABSENT, and this is a governance decision rather than an oversight:

     /api/units, /api/units/{id}, /api/trace/containment/{id}
         Unit-level defect risk, factor lists and full station journeys. Wrong
         granularity for a line-balance question, and shipping per-unit genealogy to a
         third-party API is the single worst data-governance step this feature could
         take.
     anything reading `checklists`
         That table carries `operator_group`. The strongest operator-privacy defence is
         that the data never reaches the model at all (CLAUDE.md §3).
     /api/stream
         Unbounded SSE; cannot be a tool.
     /api/audit/*
         No triage value, meaningful scope expansion.
     /api/admin/*
         Mutating. Absent from the registry entirely.

`tests/test_genai_tools.py` asserts the registry's path set EQUALS the approved list, so
widening the egress surface requires editing a test -- the same trick
`tests/test_serve_imports.py` plays on the import wall.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from genai.api_client import PulseTwinAPI

ROLE_ORDER = ("supervisor", "manager", "leadership", "system")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    method: str
    path: str
    min_role: str | None          # None => no role gate on the endpoint
    parameters: dict[str, Any]
    build: Callable[[dict[str, Any]], tuple[dict[str, Any] | None, dict[str, Any] | None]]


def _q(**names: str) -> Callable[[dict[str, Any]], tuple[dict, None]]:
    """Build a query-string tool: pass the named args straight through."""

    def _build(args: dict[str, Any]) -> tuple[dict, None]:
        return {k: args[k] for k in names if k in args}, None

    return _build


def _body(args: dict[str, Any]) -> tuple[None, dict[str, Any]]:
    return None, dict(args)


def _obj(required: list[str], props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": props,
    }


def build_registry(plant_ids: list[str]) -> dict[str, ToolSpec]:
    """Build the registry, with plant ids taken from the running API.

    NOT hardcoded: CLAUDE.md §1 bans station and plant literals outside configs/*.yaml,
    and plant_b must work without editing this file.
    """
    plant = {"type": "string", "enum": list(plant_ids)}

    specs = [
        ToolSpec(
            "get_bottleneck_current",
            "The line's current constraint station for a plant, the station it moved "
            "from, when it shifted, detector confidence, and the detector's measured "
            "accuracy against a 2-hour sustained-truth definition.",
            "GET", "/api/bottleneck/current", None,
            _obj(["plant_id"], {"plant_id": plant}), _q(plant_id="plant_id"),
        ),
        ToolSpec(
            "get_bottleneck_history",
            "Recent 15-minute bottleneck windows: detected station, ground-truth "
            "station, whether the detector agreed, and whether a shift event occurred. "
            "Use this to tell a genuine constraint move from detector flapping.",
            "GET", "/api/bottleneck/history", None,
            _obj(["plant_id", "limit"], {
                "plant_id": plant,
                "limit": {"type": "integer", "minimum": 1, "maximum": 700},
            }),
            _q(plant_id="plant_id", limit="limit"),
        ),
        ToolSpec(
            "get_station_stats",
            "Per-station averages: utilisation, blocked %, starved %, down %, and lost "
            "throughput in units/hour, ordered by lost throughput descending. Each row "
            "carries a 'provenance' field of 'measured' or 'inferred' -- an inferred row "
            "means the station has no continuous sensors and the value is reconstructed.",
            "GET", "/api/stations/stats", None,
            _obj(["plant_id"], {"plant_id": plant}), _q(plant_id="plant_id"),
        ),
        ToolSpec(
            "get_station_config",
            "Station names, zones, sensor tier ('full' or 'manual'), nominal cycle time "
            "and buffer capacity. Use it to name stations in plain language and to know "
            "which readings are inferred rather than measured.",
            "GET", "/api/config/stations", None,
            _obj(["plant_id"], {"plant_id": plant}), _q(plant_id="plant_id"),
        ),
        ToolSpec(
            "get_metrics",
            "Model quality for this plant: PR-AUC with its confidence interval, the "
            "measured oracle ceiling, bottleneck detection accuracy, and the alarm "
            "budget curve. Use it to qualify how much to trust the detector.",
            "GET", "/api/metrics", None,
            _obj(["plant_id"], {"plant_id": plant}), _q(plant_id="plant_id"),
        ),
        ToolSpec(
            "get_whatif_stations",
            "Which stations the precomputed what-if grid covers. Check this before "
            "calling simulate_whatif -- stations outside the grid return 404.",
            "GET", "/api/whatif/stations", None,
            _obj(["plant_id"], {"plant_id": plant}), _q(plant_id="plant_id"),
        ),
        ToolSpec(
            "simulate_whatif",
            "Test a remedy against the precomputed simulation grid: change a station's "
            "upstream FEED buffer and/or its cycle time, and get projected throughput, "
            "WIP, the resulting bottleneck, and a sourced annual value. This is how to "
            "quantify a recommendation instead of asserting one.",
            "POST", "/api/whatif", "manager",
            _obj(["plant_id", "station_id", "buffer_delta", "cycle_delta_pct"], {
                "plant_id": plant,
                "station_id": {
                    "type": "integer",
                    "description": "Must appear in get_whatif_stations.",
                },
                "buffer_delta": {"type": "integer", "minimum": -4, "maximum": 8},
                "cycle_delta_pct": {"type": "number", "minimum": -20.0, "maximum": 20.0},
            }),
            _body,
        ),
        ToolSpec(
            "get_system_health",
            "Infrastructure state: active sensor dropouts, database connectivity and "
            "predictions in the last hour. A constraint that 'moved' is sometimes a "
            "sensor that went dark -- check here before concluding otherwise.",
            "GET", "/api/system/health", "manager",
            _obj(["plant_id"], {"plant_id": plant}), _q(plant_id="plant_id"),
        ),
        ToolSpec(
            "get_model_health",
            "Whether the prediction model itself is drifting: PSI feature shift counts, "
            "calibration deviation, sensor coverage and fallback status.",
            "GET", "/api/model/health", "leadership",
            _obj(["plant_id"], {"plant_id": plant}), _q(plant_id="plant_id"),
        ),
        ToolSpec(
            "get_economics_roi",
            "THE ONLY LEGAL SOURCE OF DOLLAR FIGURES. Returns ROI components, each "
            "carrying its own 'source' string naming the assumption behind it. Any "
            "currency claim you make must cite a component from here or an annual_value "
            "from simulate_whatif.",
            "GET", "/api/economics/roi", "leadership",
            _obj(["plant_id", "scenario"], {
                "plant_id": plant,
                "scenario": {"type": "string",
                             "enum": ["pessimistic", "base", "optimistic"]},
            }),
            _q(plant_id="plant_id", scenario="scenario"),
        ),
    ]
    return {s.name: s for s in specs}


def to_wire(registry: dict[str, ToolSpec]) -> list[dict[str, Any]]:
    """OpenAI-compatible tool definitions.

    `strict` is requested, but `validate_args` re-checks locally regardless: strict
    support varies across OpenAI-compatible endpoints and must never be a correctness
    dependency.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": s.name,
                "description": s.description,
                "strict": True,
                "parameters": s.parameters,
            },
        }
        for s in registry.values()
    ]


def validate_args(spec: ToolSpec, args: dict[str, Any]) -> str | None:
    """A ~40-line stdlib schema walker. Returns an error string, or None if valid."""
    schema = spec.parameters
    props: dict[str, Any] = schema["properties"]

    for key in schema["required"]:
        if key not in args:
            return f"missing required argument: {key}"
    for key in args:
        if key not in props:
            return f"unexpected argument: {key}"

    for key, value in args.items():
        rule = props[key]
        kind = rule["type"]
        if kind == "string":
            if not isinstance(value, str):
                return f"{key} must be a string"
            if "enum" in rule and value not in rule["enum"]:
                return f"{key} must be one of {rule['enum']}"
        elif kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                return f"{key} must be an integer"
        elif kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return f"{key} must be a number"
        if "minimum" in rule and value < rule["minimum"]:
            return f"{key} must be >= {rule['minimum']}"
        if "maximum" in rule and value > rule["maximum"]:
            return f"{key} must be <= {rule['maximum']}"
    return None


def dispatch(
    registry: dict[str, ToolSpec],
    api: PulseTwinAPI,
    name: str,
    raw_arguments: str,
) -> dict[str, Any]:
    """Execute one tool call. NEVER raises -- every failure is a structured result the
    model can read and correct itself from. A 403 is a real, citable fact.
    """
    spec = registry.get(name)
    if spec is None:
        return {"error": "unknown_tool", "available": sorted(registry)}

    try:
        args = json.loads(raw_arguments) if raw_arguments else {}
    except json.JSONDecodeError as exc:
        return {"error": "invalid_arguments_json", "detail": str(exc)}
    if not isinstance(args, dict):
        return {"error": "invalid_arguments_json", "detail": "arguments is not an object"}

    problem = validate_args(spec, args)
    if problem:
        return {"error": "invalid_arguments", "detail": problem,
                "schema": spec.parameters}

    params, body = spec.build(args)
    status, payload = (
        api.get(spec.path, params) if spec.method == "GET" else api.post(spec.path, body or {})
    )

    if status == 403:
        return {
            "error": "forbidden",
            "detail": f"{spec.path} requires role '{spec.min_role}'; this run is "
                      f"acting as '{api.role}'.",
            "guidance": "Report this in `unanswered` rather than estimating the value.",
        }
    if status == 404:
        return {"error": "not_found", "detail": payload}
    if status == 0:
        return {"error": "unreachable", "detail": payload}
    if status >= 400:
        return {"error": f"http_{status}", "detail": payload}
    return {"ok": True, "data": payload}


def approved_paths(registry: dict[str, ToolSpec]) -> set[tuple[str, str]]:
    return {(s.method, s.path) for s in registry.values()}
