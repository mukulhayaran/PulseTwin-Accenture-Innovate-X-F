"""The closed tool registry -- which is also the data-egress allowlist.

Two things this file decides, and the second matters more:

1. What the agent can DO. Twenty functions, nineteen of them GET. The one non-GET
   posts to a SIMULATOR, not to the line.
2. What LEAVES THE NETWORK.

ADR-038 widened this surface from ten tools to eighteen, deliberately, because the
copilot is asked "which vehicles are affected?" and a tier that cannot answer that either
stays silent or guesses -- and a verifier can only catch the guess if the true answer was
reachable. The widening admits unit-level RISK and CONTAINMENT. It does not admit
people, and it does not admit writes.

ADMITTED by ADR-038, with the argument for each:

  /api/trace/containment/{unit_id}     the affected-vehicle answer. Already emits opaque
                                       operator GROUPS by construction (ADR-017), so the
                                       privacy contract is enforced upstream of the tool.
  /api/units (alerts_only, capped)     the alert list itself. Bounded by `limit`.
  /api/alerts/triage                   deterministic class + reasons per alert.
  /api/shift/summary                   one shift's facts, for handover and shift reports.
  /api/system/data-quality             whether the inputs behind a score are trustworthy.
  /api/system/sensor-dropouts          which sensors are dark right now.
  /api/trace/evidence-graph/{unit_id}  the association chain, built in the BACKEND so the
                                       model narrates a graph it cannot fabricate.
  /api/economics/instrumentation-plan  the sensor-siting recommendation, leadership-gated.

STILL ABSENT, and still a governance decision rather than an oversight:

     /api/units/{id}
         The full per-unit genealogy and raw reading list. Containment answers "which
         units share this fingerprint" without shipping every station a vehicle visited.
         The narrower tool answers the question; the wider one just exports the line.
     anything reading `checklists`
         That table carries `operator_group`. The strongest operator-privacy defence is
         that the data never reaches the model at all (CLAUDE.md §3), and the verifier's
         C4 check is the second line, not the first.
     /api/stream
         Unbounded SSE; cannot be a tool.
     /api/audit/*
         The agent must not read the record of who did what. No triage value, and a
         meaningful scope expansion.
     /api/admin/*
         Mutating. Absent from the registry entirely. The registry contains no tool that
         can change a model, a config, or the line.

ADMITTED by ADR-051:

  /api/watch/firings   what the proactive watch saw (detector, station, measured window
                       evidence). Read-only. The PROPOSAL endpoints beside it
                       (/api/proposals/*/approve|reject) are NOT tools: an agent may
                       propose an action in its finding, and only a human can decide it.

`tests/test_genai_tools.py` asserts the registry's path set EQUALS the approved list, so
widening the egress surface requires editing a test -- the same trick
`tests/test_serve_imports.py` plays on the import wall. That friction is the feature.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote

from genai.api_client import PulseTwinAPI

ROLE_ORDER = ("supervisor", "manager", "leadership", "system")

# The human-readable activity trail the UI renders while a run is in flight, in the order
# an investigation naturally moves through them. Every ToolSpec names one.
#
# These live HERE and not in a React map for three reasons: a UI map silently
# desynchronises the moment a tool is added; it would have to be duplicated for the
# copilot page and the dock; and the deterministic path (genai/offline_triage.py) would
# get no labels at all, so a no-credential demo would show a spinner where the agent
# shows progress. The first and last two entries are written by the runner, not by a tool.
PHASE_ORDER: tuple[str, ...] = (
    "Starting investigation",
    "Checking production state",
    "Reading station performance",
    "Examining risk signals",
    "Tracing affected units",
    "Running simulation",
    "Costing the options",
    "Searching engineering knowledge",
    "Composing the finding",
    "Verifying every number",
)

PHASE_START = PHASE_ORDER[0]
PHASE_COMPOSE = PHASE_ORDER[-2]
PHASE_VERIFY = PHASE_ORDER[-1]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    phase: str                    # must be one of PHASE_ORDER; asserted by tests
    method: str
    path: str
    min_role: str | None          # None => no role gate on the endpoint
    parameters: dict[str, Any]
    build: Callable[[dict[str, Any]], tuple[dict[str, Any] | None, dict[str, Any] | None]]
    # Path-parameter tools (/api/trace/containment/{unit_id}) name their placeholders
    # here. `dispatch` pops them out of the args and formats them into the path, so a
    # value can never smuggle a second path segment past the egress allowlist.
    path_params: tuple[str, ...] = ()


def phase_for(registry: dict[str, ToolSpec], name: str) -> str:
    """The activity label for a tool call, for the progress surface.

    Falls back to the generic first phase for an unknown name -- a progress label must
    never be the thing that raises.
    """
    spec = registry.get(name)
    return spec.phase if spec is not None else PHASE_START


def phase_index(phase: str) -> int:
    return PHASE_ORDER.index(phase) if phase in PHASE_ORDER else 0


def _q(**names: str) -> Callable[[dict[str, Any]], tuple[dict, None]]:
    """Build a query-string tool: pass the named args straight through."""

    def _build(args: dict[str, Any]) -> tuple[dict, None]:
        return {k: args[k] for k in names if k in args}, None

    return _build


def _body(args: dict[str, Any]) -> tuple[None, dict[str, Any]]:
    return None, dict(args)


def _q_except(*path_names: str) -> Callable[[dict[str, Any]], tuple[dict, None]]:
    """Query-string builder for a PATH-PARAM tool: everything except the path names.

    The path placeholders are consumed by `dispatch` when it formats the URL; passing
    them on as query params too would be harmless but noisy in the audit ledger.
    """

    def _build(args: dict[str, Any]) -> tuple[dict, None]:
        return {k: v for k, v in args.items() if k not in path_names}, None

    return _build


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
            "Checking production state",
            "GET", "/api/bottleneck/current", None,
            _obj(["plant_id"], {"plant_id": plant}), _q(plant_id="plant_id"),
        ),
        ToolSpec(
            "get_bottleneck_history",
            "Recent 15-minute bottleneck windows: detected station, ground-truth "
            "station, whether the detector agreed, and whether a shift event occurred. "
            "Use this to tell a genuine constraint move from detector flapping.",
            "Checking production state",
            "GET", "/api/bottleneck/history", None,
            _obj(["plant_id", "limit"], {
                "plant_id": plant,
                "limit": {"type": "integer", "minimum": 1, "maximum": 700},
            }),
            _q(plant_id="plant_id", limit="limit"),
        ),
        ToolSpec(
            "get_station_stats",
            "Per-station averages: util, blocked, starved and down are FRACTIONS from "
            "0.0 to 1.0, not percentages -- write the field's own value verbatim (e.g. "
            "0.98, not 98%); the verifier checks the literal number, and a *100 "
            "conversion is a value_mismatch. Also carries lost throughput in units/hour, "
            "ordered by lost throughput descending, and a 'provenance' field of "
            "'measured' or 'inferred' -- an inferred row means the station has no "
            "continuous sensors and the value is reconstructed.",
            "Reading station performance",
            "GET", "/api/stations/stats", None,
            _obj(["plant_id"], {"plant_id": plant}), _q(plant_id="plant_id"),
        ),
        ToolSpec(
            "get_station_config",
            "Station names, zones, sensor tier ('full' or 'manual'), nominal cycle time "
            "and buffer capacity. Use it to name stations in plain language and to know "
            "which readings are inferred rather than measured.",
            "Reading station performance",
            "GET", "/api/config/stations", None,
            _obj(["plant_id"], {"plant_id": plant}), _q(plant_id="plant_id"),
        ),
        ToolSpec(
            "get_metrics",
            "Model quality for this plant: PR-AUC with its confidence interval, the "
            "measured oracle ceiling, bottleneck detection accuracy, and the alarm "
            "budget curve. Use it to qualify how much to trust the detector.",
            "Examining risk signals",
            "GET", "/api/metrics", None,
            _obj(["plant_id"], {"plant_id": plant}), _q(plant_id="plant_id"),
        ),
        ToolSpec(
            "get_whatif_stations",
            "Which stations the precomputed what-if grid covers. Check this before "
            "calling simulate_whatif -- stations outside the grid return 404.",
            "Running simulation",
            "GET", "/api/whatif/stations", None,
            _obj(["plant_id"], {"plant_id": plant}), _q(plant_id="plant_id"),
        ),
        ToolSpec(
            "simulate_whatif",
            "Test a remedy against the precomputed simulation grid: change a station's "
            "upstream FEED buffer and/or its cycle time, and get projected throughput, "
            "WIP, the resulting bottleneck, and a sourced annual value. This is how to "
            "quantify a recommendation instead of asserting one.",
            "Running simulation",
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
            "Examining risk signals",
            "GET", "/api/system/health", "manager",
            _obj(["plant_id"], {"plant_id": plant}), _q(plant_id="plant_id"),
        ),
        ToolSpec(
            "get_model_health",
            "Whether the prediction model itself is drifting: PSI feature shift counts, "
            "calibration deviation, sensor coverage and fallback status.",
            "Examining risk signals",
            "GET", "/api/model/health", "leadership",
            _obj(["plant_id"], {"plant_id": plant}), _q(plant_id="plant_id"),
        ),
        ToolSpec(
            "get_economics_roi",
            "THE ONLY LEGAL SOURCE OF DOLLAR FIGURES. Returns ROI components, each "
            "carrying its own 'source' string naming the assumption behind it. Any "
            "currency claim you make must cite a component from here or an annual_value "
            "from simulate_whatif.",
            "Costing the options",
            "GET", "/api/economics/roi", "leadership",
            _obj(["plant_id", "scenario"], {
                "plant_id": plant,
                "scenario": {"type": "string",
                             "enum": ["pessimistic", "base", "optimistic"]},
            }),
            _q(plant_id="plant_id", scenario="scenario"),
        ),
        # ------------------------------------------------- ADR-038 additions
        ToolSpec(
            "get_recent_alerts",
            "Units the model has flagged, most recent first: calibrated risk with its "
            "confidence interval, whether an alert actually fired, lead time before the "
            "inspection gate, and the defect mode if one was confirmed. Use it to answer "
            "'which vehicles are at risk' with real unit ids rather than a count.",
            "Examining risk signals",
            "GET", "/api/units", None,
            _obj(["plant_id", "alerts_only", "limit"], {
                "plant_id": plant,
                "alerts_only": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            }),
            _q(plant_id="plant_id", alerts_only="alerts_only", limit="limit"),
        ),
        ToolSpec(
            "get_alert_triage",
            "Every alert candidate ranked CRITICAL / INVESTIGATE / MONITOR / "
            "LOW_CONFIDENCE by deterministic rules, each carrying the factors and the "
            "written reasons behind its class. Read `diagnostics` before quoting a "
            "ranking: it says when a dimension could not discriminate. You must NOT "
            "re-rank these or invent a class -- report what the rules decided.",
            "Examining risk signals",
            "GET", "/api/alerts/triage", "supervisor",
            _obj(["plant_id", "limit"], {
                "plant_id": plant,
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            }),
            _q(plant_id="plant_id", limit="limit"),
        ),
        ToolSpec(
            "get_containment",
            "Which OTHER in-flight units share a flagged unit's causal fingerprint, with "
            "a HOLD / INSPECT / MONITOR action for each. This is the affected-vehicle "
            "answer. `shared_factors` are opaque group labels by construction -- never "
            "report an operator, a crew or a person.",
            "Tracing affected units",
            "GET", "/api/trace/containment/{unit_id}", None,
            _obj(["plant_id", "unit_id"], {
                "plant_id": plant,
                "unit_id": {"type": "string",
                            "description": "A unit id from get_recent_alerts."},
            }),
            _q_except("unit_id"),
            path_params=("unit_id",),
        ),
        ToolSpec(
            "get_evidence_graph",
            "The evidence chain for one unit as nodes and edges: station -> signal -> "
            "time window -> shared fingerprint -> risk -> affected units -> inspection. "
            "Each edge names its source, window, provenance, confidence and model. The "
            "graph is an ASSOCIATION graph: it carries `causality: association` and you "
            "must not describe any edge as a cause.",
            "Tracing affected units",
            "GET", "/api/trace/evidence-graph/{unit_id}", "supervisor",
            _obj(["plant_id", "unit_id"], {
                "plant_id": plant,
                "unit_id": {"type": "string",
                            "description": "A unit id from get_recent_alerts."},
            }),
            _q_except("unit_id"),
            path_params=("unit_id",),
        ),
        ToolSpec(
            "get_shift_summary",
            "One 8-hour shift's facts: units produced, alerts fired against the budget, "
            "confirmed defects, where the constraint opened and closed and how often it "
            "moved, the worst stations, active sensor dropouts and breached drift "
            "metrics. This is the substrate for a shift report or a handover -- use it "
            "instead of assembling a shift picture from six other calls.",
            "Checking production state",
            "GET", "/api/shift/summary", "manager",
            _obj(["plant_id"], {"plant_id": plant}), _q(plant_id="plant_id"),
        ),
        ToolSpec(
            "get_data_quality",
            "Per-station data quality: completeness, validity, how many readings are "
            "measured versus reconstructed, and which sensors are dark. Read `notes` "
            "before quoting the score -- it says when a near-perfect number means 'no "
            "faults were injected' rather than 'the line is healthy'.",
            "Examining risk signals",
            "GET", "/api/system/data-quality", "supervisor",
            _obj(["plant_id"], {"plant_id": plant}), _q(plant_id="plant_id"),
        ),
        ToolSpec(
            "get_sensor_dropouts",
            "Sensors currently dark, with the station and how long they have been out. A "
            "station whose statistics suddenly look different is sometimes a sensor that "
            "stopped reporting -- check here before concluding the line changed.",
            "Examining risk signals",
            "GET", "/api/system/sensor-dropouts", "supervisor",
            _obj(["plant_id"], {"plant_id": plant}), _q(plant_id="plant_id"),
        ),
        ToolSpec(
            "get_instrumentation_plan",
            "Where to add the next sensor: per candidate station, the sensor type, its "
            "sourced cost, the measured PR-AUC uplift it would buy, annual value, payback "
            "and maintenance window. Every dollar figure here carries its own "
            "`cost_source`. Several candidates score ZERO uplift -- report that honestly "
            "rather than recommending the cheapest non-zero one by default.",
            "Costing the options",
            "GET", "/api/economics/instrumentation-plan", "leadership",
            _obj(["plant_id"], {"plant_id": plant}), _q(plant_id="plant_id"),
        ),
        # ------------------------------------------------------------------- ADR-051
        ToolSpec(
            "get_watch_firings",
            "The live watch's recent firings: which detector (starvation, paint_env, "
            "tool_wear) fired at which station, when, and the MEASURED window evidence "
            "that crossed the threshold -- e.g. stalled_share, "
            "humidity_excess_over_ambient_pct, deviation_pct. Start here for a "
            "watch-triggered investigation and cite its evidence values. Empty when "
            "nothing has fired.",
            "Checking production state",
            "GET", "/api/watch/firings", "manager",
            _obj(["plant_id"], {
                "plant_id": plant,
                "detector": {"type": "string",
                             "enum": ["starvation", "paint_env", "tool_wear"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            }),
            _q(plant_id="plant_id", detector="detector", limit="limit"),
        ),
        # ------------------------------------------------------------------- ADR-046
        ToolSpec(
            "search_engineering_knowledge",
            "Search a synthetic engineering knowledge base -- SOPs, fault-code "
            "reference, maintenance and calibration procedures -- for text relevant to "
            "your question. NOT plant-scoped: this is reference documentation, not live "
            "data, so it takes no plant_id. Each result carries doc_id, section_id and "
            "the exact chunk text. If you quote or paraphrase a result in your finding, "
            "you MUST cite it in `knowledge_citations`, not `claims` -- `quoted_text` "
            "must be a real, verbatim excerpt of the cited chunk's `text`, or the whole "
            "run is rejected. Do not state a procedure or fault-code meaning as fact "
            "without a citation to a result this tool actually returned.",
            "Searching engineering knowledge",
            "GET", "/api/knowledge/search", None,
            _obj(["query"], {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 8},
            }),
            _q(query="query", limit="limit"),
        ),
    ]
    return {s.name: s for s in specs}


def to_wire(registry: dict[str, ToolSpec], *, strict: bool = True) -> list[dict[str, Any]]:
    """OpenAI-compatible tool definitions.

    `strict` is requested by default, but `validate_args` re-checks locally regardless:
    strict support varies across OpenAI-compatible endpoints and must never be a
    correctness dependency. `genai/capabilities.py` drops it on the FIRST rung of its
    degradation ladder precisely because this docstring already says it costs nothing.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": s.name,
                "description": s.description,
                **({"strict": True} if strict else {}),
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
        elif kind == "boolean":
            # Checked explicitly rather than falling through: an unhandled type would
            # silently accept anything, and `alerts_only="yes"` must not pass.
            if not isinstance(value, bool):
                return f"{key} must be a boolean"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
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

    path = spec.path
    if spec.path_params:
        # Substitute placeholders ourselves and URL-QUOTE each value. A tool argument must
        # never be able to add a path segment: without quoting, a unit_id of "../../audit/
        # log" would walk straight out of the egress allowlist that this
        # registry exists to BE. `safe=""` quotes the slash too, which is the whole point.
        substituted: dict[str, str] = {}
        for key in spec.path_params:
            if key not in args:
                return {"error": "invalid_arguments",
                        "detail": f"missing required path argument: {key}",
                        "schema": spec.parameters}
            substituted[key] = quote(str(args[key]), safe="")
        path = spec.path.format(**substituted)

    params, body = spec.build(args)
    status, payload = (
        api.get(path, params) if spec.method == "GET" else api.post(path, body or {})
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
