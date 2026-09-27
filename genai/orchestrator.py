"""Coordinator -> Specialists -> Synthesizer, for the `incident` intent only (ADR-045).

Reuses genai/loop.py::run_loop UNCHANGED for every stage, including synthesis. The only
new mechanism this module depends on is run_loop's `initial_seq` threading: every
specialist starts its call_id numbering where the previous stage left off, so the merged
transcript handed to genai/verifier.py has no collisions and the verifier needs no changes
at all -- it has never had a concept of "which loop produced this call_id" and doesn't
need one now.

THE COORDINATOR IS DETERMINISTIC, NOT A MODEL CALL. Same reasoning genai/intents.py
already applies to intent selection ("a classifier would be a second, unverifiable model
call and a new failure mode, to save a click on a button that is already on screen"):
`INTENTS[INCIDENT].guidance` already describes all three specialist concerns as
unconditional ("establish... Then... rather than describing them in the abstract"), so
there is no real per-question judgment to spend a request on yet. See ADR-045 for the
LLM-coordinator alternative and why it is a deferred fast-follow, not built here.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from genai import config, intents
from genai import loop as L
from genai import tools as T
from genai.api_client import PulseTwinAPI

# A credential/permission-shaped failure cannot be fixed by trying the next specialist or
# the synthesizer -- every later stage would fail the identical way. Matches the codes
# genai/capabilities.py already treats as non-degradable (a bad credential fails on the
# first attempt rather than walking the ladder).
_FATAL_CODES = frozenset({
    "provider_status_401", "provider_status_403", "provider_status_404",
})

# ADR-046: every specialist may also call search_engineering_knowledge. Shared text
# rather than repeated three times, same reasoning as intents.COMMON_GUIDANCE existing.
_KNOWLEDGE_GUIDANCE = (
    "If a procedure, threshold or fault-code meaning would strengthen your evidence, "
    "call search_engineering_knowledge. Cite anything you quote or paraphrase from it in "
    "`knowledge_citations`, never in prose alone -- an uncited procedure claim is removed "
    "the same way an uncited number is."
)


# ---------------------------------------------------------------- specialist definitions
@dataclass(frozen=True)
class SpecialistSpec:
    key: str
    label: str                      # UI-facing, and the heartbeat's agent_label
    tool_names: tuple[str, ...]     # subset of a full registry's keys
    guidance: str                   # replaces intent_guidance for this specialist's turn
    max_iterations: int
    max_tool_calls: int
    wallclock_cap_s: float


RELIABILITY_GUIDANCE = (
    "You are the RELIABILITY specialist on an incident-investigation team. Your only job: "
    "establish whether an infrastructure problem -- a dark sensor, untrustworthy data, a "
    "drifting model -- could explain what looks like a production change, before a "
    "teammate concludes the line itself changed. Call get_system_health and "
    "get_sensor_dropouts; call get_data_quality and get_model_health if either of those, "
    "or the question, puts data trust in doubt. 'No dropouts are active' is a complete "
    "answer, not a non-answer. You are not asked to explain the bottleneck or name "
    "affected units -- other specialists cover each of those. " + _KNOWLEDGE_GUIDANCE
    + " " + intents.COMMON_GUIDANCE
)

PRODUCTION_GUIDANCE = (
    "You are the PRODUCTION specialist on an incident-investigation team. Your job: "
    "establish WHAT CHANGED -- the current constraint, whether it moved, and the measured "
    "cost in lost throughput. Start with get_bottleneck_current, then "
    "get_bottleneck_history to tell a genuine shift from detector flapping, then "
    "get_station_stats for evidence. If a remedy is worth naming, TEST it with "
    "simulate_whatif (check get_whatif_stations first) rather than asserting it. You are "
    "not asked to assess sensor health or name affected units -- other specialists cover "
    "each of those. " + _KNOWLEDGE_GUIDANCE + " " + intents.COMMON_GUIDANCE
)

RISK_GUIDANCE = (
    "You are the RISK specialist on an incident-investigation team. Your job: identify "
    "which units are actually at risk and who else is affected. Use get_alert_triage "
    "and/or get_recent_alerts to find the highest-priority candidate, then get_containment "
    "to trace which OTHER in-flight units share its fingerprint. If the evidence supports "
    "an association but not a cause, say so in those words -- get_evidence_graph is "
    "available if you need to show the chain. You are not asked to diagnose "
    "infrastructure or explain the bottleneck -- other specialists cover each of those. "
    + _KNOWLEDGE_GUIDANCE + " " + intents.COMMON_GUIDANCE
)

PLANNING_GUIDANCE = (
    "You are the SIMULATION PLANNER on an incident-investigation team (ADR-064). The "
    "specialists' evidence is below. You choose experiments; the deterministic simulator "
    "calculates them -- you never compute plant physics. Read get_similar_incidents and "
    "get_action_outcomes first: what happened the last times this station or incident "
    "type came up? Then form at most 3 bounded hypotheses about the constraint or quality "
    "risk and test the strongest with ONE run_simulation_experiment (catalogue "
    "interventions only, at the station the evidence implicates). Revise at most once if "
    "the first result is too uncertain. If nothing is worth simulating -- no implicated "
    "station, or only unsupported interventions -- say so and stop. "
    + intents.COMMON_GUIDANCE
)

SYNTHESIS_GUIDANCE = (
    "You are the LEAD investigator reconciling three specialists' findings into ONE report "
    "for a shift engineer. You have NO tools -- every fact you can cite is in the evidence "
    "below, each tagged with the call_id and specialist that gathered it. Cross-check for "
    "contradictions before writing: if RELIABILITY found active dropouts touching a "
    "station PRODUCTION also flagged, say so explicitly and prefer the infrastructure "
    "explanation, per the incident method. If a specialist's status below is PARTIAL, "
    "treat gaps in its evidence as unanswered rather than filling them in. Cite ONLY "
    "call_ids shown below -- you cannot call a tool to check anything further. If a "
    "specialist's evidence includes a search_engineering_knowledge result you quote or "
    "paraphrase, cite it in `knowledge_citations` with the exact call_id shown below, not "
    "as free prose. CITE BY COPYING: each evidence entry carries `citable`, a list of "
    "[pointer, value, provenance] for every number in it. A claim's citation.pointer and "
    "value MUST be copied from the `citable` list of the call_id you cite -- a pointer "
    "that is not listed there does not resolve and the claim is rejected. When an entry "
    "says inferred, the claim is inferred and carries the ci_low/ci_high listed beside it. "
    "Keep four things apart: OBSERVED facts (measured claims), INFERRED "
    "relationships, SIMULATION PROJECTIONS (claims citing run_simulation_experiment, "
    "provenance inferred with their exact ci_low/ci_high) and your RECOMMENDATION. Set "
    "`decision` from the planner's experiment: recommend only a change whose paired "
    "interval is clear of zero; otherwise no_improvement or too_uncertain. "
    + intents.COMMON_GUIDANCE
)

SPECIALISTS: dict[str, SpecialistSpec] = {
    # Every specialist also gets search_engineering_knowledge (ADR-046) -- reference
    # documentation, not live plant data, so it fits any investigative role. Each
    # specialist's max_tool_calls is bumped by exactly 1 over its pre-ADR-046 budget so an
    # optional knowledge lookup never comes at the cost of the domain evidence the
    # specialist was already sized to gather.
    "reliability": SpecialistSpec(
        "reliability", "Reliability",
        ("get_system_health", "get_sensor_dropouts", "get_data_quality",
         "get_model_health", "search_engineering_knowledge"),
        RELIABILITY_GUIDANCE, max_iterations=5, max_tool_calls=4, wallclock_cap_s=60.0),
    "production": SpecialistSpec(
        "production", "Production",
        ("get_bottleneck_current", "get_bottleneck_history", "get_station_stats",
         "get_station_config", "get_whatif_stations", "simulate_whatif",
         "get_shift_summary", "search_engineering_knowledge"),
        PRODUCTION_GUIDANCE, max_iterations=6, max_tool_calls=5, wallclock_cap_s=90.0),
    "risk": SpecialistSpec(
        "risk", "Risk",
        ("get_recent_alerts", "get_alert_triage", "get_containment",
         "get_evidence_graph", "search_engineering_knowledge"),
        RISK_GUIDANCE, max_iterations=6, max_tool_calls=5, wallclock_cap_s=90.0),
}

# ADR-064: the simulation-planning stage. Separate from SPECIALISTS -- it reads THEIR
# evidence, so it always runs after them -- and it only runs when the registry holds the
# experiment tool (a keyless or narrowed registry skips it).
PLANNER = SpecialistSpec(
    "simulation", "Simulation planner",
    ("run_simulation_experiment", "compare_simulation_interventions",
     "get_similar_incidents", "get_action_outcomes"),
    PLANNING_GUIDANCE, max_iterations=5, max_tool_calls=3, wallclock_cap_s=120.0)

# reliability(60) + production(90) + risk(90) + planner(120) + synthesis(150) == 510 s ==
# config.INCIDENT_WALLCLOCK_CAP_S exactly: a bounded ceiling partitioned across five
# stages. The planner's slice includes one smoke experiment (~40 s of simulation).
_SYNTHESIS_WALLCLOCK_CAP_S = 150.0
assert (
    sum(s.wallclock_cap_s for s in SPECIALISTS.values()) + PLANNER.wallclock_cap_s
    + _SYNTHESIS_WALLCLOCK_CAP_S == config.INCIDENT_WALLCLOCK_CAP_S
)


def select_specialists(intent: str, question: str) -> list[str]:
    """The coordinator. Deterministic -- see the module docstring and ADR-045.

    `question` is accepted and currently ignored for `incident`: every clause of
    INTENTS[INCIDENT].guidance is unconditional, so there is no real per-question
    selection to make yet. The parameter stays so a later intent (or a later, genuinely
    conditional incident policy) has a stable seam rather than a signature change.
    """
    if intent != intents.INCIDENT:
        return []
    picked = ["reliability", "production", "risk"]
    if config.MAX_ORCHESTRATION_SPECIALISTS is not None:
        picked = picked[: max(1, config.MAX_ORCHESTRATION_SPECIALISTS)]
    return picked


@dataclass(frozen=True)
class AgentProgressEvent:
    agent: str                       # specialist key, "coordinator", or "synthesizer"
    agent_label: str
    event: L.ProgressEvent | None    # the wrapped single-agent event, or None for a tick
    agents: list[dict[str, Any]]     # snapshot: [{key,label,status,tool_calls}, ...]
    # Every ToolRecord recorded so far, across ALL specialists (completed ones plus the
    # active one's in-flight records) -- not just the current stage's. Without this the
    # live heartbeat's tool trail would lose earlier specialists' calls the moment the
    # next specialist starts, even though they are already safely in `all_records` and
    # will be persisted. `event.records` alone (the wrapped single-agent event) only ever
    # covers the CURRENT run_loop invocation.
    records: list[L.ToolRecord]


def _sum_opt(a: float | None, b: float | None) -> float | None:
    if a is None and b is None:
        return None
    return (a or 0.0) + (b or 0.0)


CITABLE_PER_RECORD = 60


def citable_numbers(result: Any, limit: int = CITABLE_PER_RECORD) -> list[list[Any]]:
    """Every numeric leaf of a RECORDED tool result as [pointer, value, provenance?].

    The synthesizer has no tools, so it used to GUESS paths into nested evidence --
    measured live: `/data/nodes/6/plain_language`, `/data/at_risk_units` (a list),
    `/data/alerts/0/factors/8` (past the end), each rejected by the gate. Built from the
    same recorded result the verifier resolves against, so every listed pointer resolves
    by construction; copying one is not a guess. `_truncated` bookkeeping is skipped."""
    out: list[list[Any]] = []

    def walk(node: Any, path: str, prov: str | None) -> None:
        if len(out) >= limit:
            return
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            out.append([path, node] + ([prov] if prov else []))
            return
        if isinstance(node, dict):
            here = node.get("provenance") if isinstance(node.get("provenance"), str) else prov
            for k, v in node.items():
                if str(k).startswith("_"):
                    continue
                walk(v, f"{path}/{str(k).replace('~', '~0').replace('/', '~1')}", here)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}/{i}", prov)

    walk(result, "", None)
    return out


def _render_evidence(records: list[L.ToolRecord], agent_statuses: list[dict]) -> str:
    # Each record gets an EQUAL SHARE of the budget. Truncating the bundle as one list
    # kept its head and tail and dropped whole records from the middle: measured, two of
    # six specialists' results vanished and the synthesizer cited their call_ids blind,
    # inventing pointers (`/data/nodes/6/plain_language`) the gate then rejected.
    share = config.SYNTHESIS_EVIDENCE_BUDGET // max(1, len(records))
    bundle = [
        {"call_id": r.call_id, "tool_name": r.name, "specialist": r.agent,
         "phase": r.phase, "result": L.truncate(r.result, budget=share),
         "citable": citable_numbers(r.result)}
        for r in records
    ]
    # Shrinking can only HIDE a data point the synthesizer might have cited -- it can never
    # let it cite a number the verifier cannot resolve, because verification checks each
    # specialist's OWN recorded ToolRecord.result, never this re-rendered view.
    status_lines = "\n".join(
        f"- {a['label']}: {a['status']} ({a['tool_calls']} tool call(s))"
        for a in agent_statuses
    )
    return (
        "SPECIALIST STATUS\n" + status_lines + "\n\n"
        "EVIDENCE GATHERED (cite these call_ids; you have no tools of your own):\n"
        + json.dumps(bundle, default=str)
    )


def run_orchestrated_incident(
    client: Any,
    api: PulseTwinAPI,
    registry: dict[str, T.ToolSpec],
    *,
    plant_id: str,
    question: str,
    acting_role: str,
    thread_context: str = "",
    classify_error: Callable[[BaseException], str | None] | None = None,
    retry_after: Callable[[BaseException], float | None] | None = None,
    now: Callable[[], float] = time.monotonic,
    on_progress: Callable[[AgentProgressEvent], None] | None = None,
) -> L.LoopResult:
    """Run the `incident` intent as Coordinator -> Specialists -> Synthesizer.

    Returns a plain genai.loop.LoopResult -- the same shape a single-agent run_loop call
    returns -- so genai/triage.py::_persist() needs no new type to consume. `agents_used`
    on the result is what tells the caller this run was orchestrated at all.
    """
    run_started = now()
    specialist_keys = select_specialists(intents.INCIDENT, question)
    all_records: list[L.ToolRecord] = []
    agent_statuses: list[dict[str, Any]] = [
        {"key": k, "label": SPECIALISTS[k].label, "status": "pending", "tool_calls": 0}
        for k in specialist_keys
    ]

    def emit(agent: str, label: str, event: L.ProgressEvent | None = None) -> None:
        if on_progress:
            records = all_records + (list(event.records) if event else [])
            on_progress(AgentProgressEvent(agent=agent, agent_label=label, event=event,
                                           agents=list(agent_statuses), records=records))

    seq = 0
    prompt_tokens = completion_tokens = 0
    total_iterations = 0
    provider_cost_usd: float | None = None
    degradations: list[str] = []
    capability_profile = ""

    emit("coordinator", "Coordinator")

    for i, key in enumerate(specialist_keys):
        spec = SPECIALISTS[key]
        agent_statuses[i]["status"] = "running"
        emit(spec.key, spec.label)

        sub_registry = {n: registry[n] for n in spec.tool_names if n in registry}

        def _on_sub_progress(ev: L.ProgressEvent, k: str = spec.key,
                             lbl: str = spec.label) -> None:
            emit(k, lbl, ev)

        sub = L.run_loop(
            client, api, sub_registry,
            plant_id=plant_id, question=question, acting_role=acting_role,
            thread_context=thread_context, intent_guidance=spec.guidance,
            on_progress=_on_sub_progress,
            classify_error=classify_error, retry_after=retry_after, now=now,
            initial_seq=seq, max_iterations=spec.max_iterations,
            max_tool_calls=spec.max_tool_calls, wallclock_cap_s=spec.wallclock_cap_s,
        )
        for r in sub.records:
            r.agent = spec.key
        all_records.extend(sub.records)
        seq = sub.final_seq
        prompt_tokens += sub.prompt_tokens
        completion_tokens += sub.completion_tokens
        total_iterations += sub.iterations
        provider_cost_usd = _sum_opt(provider_cost_usd, sub.provider_cost_usd)
        degradations.extend(f"{spec.key}:{d}" for d in sub.degradations)
        capability_profile = sub.capability_profile or capability_profile

        agent_statuses[i]["tool_calls"] = len(sub.records)
        agent_statuses[i]["status"] = "ok" if sub.status == "finished" else "partial"
        emit(spec.key, spec.label)

        if sub.status == "failed" and sub.error in _FATAL_CODES:
            # Credential/permission-shaped failure: abort rather than burn the remaining
            # specialists and a synthesis call that cannot succeed either.
            result = L.LoopResult(
                status="failed", content="", records=all_records,
                iterations=total_iterations, prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens, error=sub.error,
                capability_profile=capability_profile, degradations=degradations,
                provider_cost_usd=provider_cost_usd,
            )
            result.final_seq = seq
            result.agents_used = agent_statuses
            return result

    # ---- ADR-064 simulation planning: a bounded agent that CHOOSES experiments
    if PLANNER.tool_names[0] in registry:
        plan_registry = {n: registry[n] for n in PLANNER.tool_names if n in registry}
        agent_statuses.append({"key": PLANNER.key, "label": PLANNER.label,
                               "status": "running", "tool_calls": 0})
        emit(PLANNER.key, PLANNER.label)

        def _on_plan_progress(ev: L.ProgressEvent) -> None:
            emit(PLANNER.key, PLANNER.label, ev)

        plan = L.run_loop(
            client, api, plan_registry,
            plant_id=plant_id, question=question, acting_role=acting_role,
            thread_context=thread_context,
            intent_guidance=PLANNER.guidance + "\n\n"
            + _render_evidence(all_records, agent_statuses),
            on_progress=_on_plan_progress,
            classify_error=classify_error, retry_after=retry_after, now=now,
            initial_seq=seq, max_iterations=PLANNER.max_iterations,
            max_tool_calls=PLANNER.max_tool_calls, wallclock_cap_s=PLANNER.wallclock_cap_s,
        )
        for r in plan.records:
            r.agent = PLANNER.key
        all_records.extend(plan.records)
        seq = plan.final_seq
        prompt_tokens += plan.prompt_tokens
        completion_tokens += plan.completion_tokens
        total_iterations += plan.iterations
        provider_cost_usd = _sum_opt(provider_cost_usd, plan.provider_cost_usd)
        degradations.extend(f"{PLANNER.key}:{d}" for d in plan.degradations)
        agent_statuses[-1]["tool_calls"] = len(plan.records)
        agent_statuses[-1]["status"] = "ok" if plan.status == "finished" else "partial"
        emit(PLANNER.key, PLANNER.label)

    # ---- synthesis: same run_loop, an empty registry, no new mechanism
    evidence = _render_evidence(all_records, agent_statuses)

    def _on_synth_progress(ev: L.ProgressEvent) -> None:
        emit("synthesizer", "Synthesizer", ev)

    synth = L.run_loop(
        client, api, {},
        plant_id=plant_id, question=question, acting_role=acting_role,
        thread_context=thread_context,
        intent_guidance=SYNTHESIS_GUIDANCE + "\n\n" + evidence,
        on_progress=_on_synth_progress,
        classify_error=classify_error, retry_after=retry_after, now=now,
        initial_seq=seq, max_iterations=2, max_tool_calls=0,
        # What the specialists left unspent, never below the 60 s floor: the run's 300 s
        # ceiling holds, but a fast investigation no longer strands a reasoning model at
        # 60 s for its one large composing call (measured: a 14-tool incident timed out).
        wallclock_cap_s=max(_SYNTHESIS_WALLCLOCK_CAP_S,
                            config.INCIDENT_WALLCLOCK_CAP_S - (now() - run_started)),
    )
    all_records.extend(synth.records)   # empty in practice; harmless if a model misbehaves
    prompt_tokens += synth.prompt_tokens
    completion_tokens += synth.completion_tokens
    total_iterations += synth.iterations
    provider_cost_usd = _sum_opt(provider_cost_usd, synth.provider_cost_usd)
    degradations.extend(f"synthesizer:{d}" for d in synth.degradations)
    capability_profile = synth.capability_profile or capability_profile

    result = L.LoopResult(
        status=synth.status, content=synth.content, records=all_records,
        iterations=total_iterations, prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens, error=synth.error,
        capability_profile=capability_profile, degradations=degradations,
        provider_cost_usd=provider_cost_usd,
    )
    result.final_seq = synth.final_seq
    result.agents_used = agent_statuses
    return result


__all__ = [
    "SpecialistSpec", "SPECIALISTS", "select_specialists",
    "AgentProgressEvent", "run_orchestrated_incident",
]
