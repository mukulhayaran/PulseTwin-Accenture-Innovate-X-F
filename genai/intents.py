"""The eight things the copilot can be asked to do, and what each one means.

An intent is a KEY, not a sentence. "Generate Shift Handover" is a button that posts
`intent: "handover"`, never a canned English string the browser made up, for three
reasons:

  * the deterministic path can select a plan on the key with no language understanding
    at all, so a no-credential demo answers every named action;
  * `triage_runs.intent` becomes a queryable column instead of a `LIKE '%handover%'`;
  * the behaviour of a button lives in Python next to the plan it selects, rather than
    half in TypeScript and half here, drifting.

There is deliberately NO natural-language intent classifier. Typing "generate a handover"
into the composer posts `intent: "ask"` and the agent handles it as an ordinary question.
A classifier would be a second, unverifiable model call and a new failure mode, to save a
click on a button that is already on screen.

`serve/routers/triage.py` mirrors ONLY the keys and the short labels -- it cannot import
this module (the import wall), the same way it re-declares `API_KEY_ENV`. A test asserts
the two sets are equal, so drift is a failure rather than a surprise.

Pure module: no I/O, no SDK, no database.
"""

from __future__ import annotations

from dataclasses import dataclass

ASK = "ask"
INCIDENT = "incident"
HANDOVER = "handover"
SHIFT_INTEL = "shift_intel"
WHATIF = "whatif"
# ADR-051: the three watch-triggered agents. Each PROPOSES an action; a human decides.
STARVATION = "starvation"
PAINT_ENV = "paint_env"
TOOL_WEAR = "tool_wear"


@dataclass(frozen=True)
class Intent:
    key: str
    label: str                # what the button says, and what lands in `question`
    instruction: str          # the task text handed to the model, .format()-ed
    guidance: str             # appended to the user turn on the agent path
    plan: str                 # the deterministic plan name in genai/offline_triage.py
    requires_station: bool = False


# Exported (not module-private) so genai/orchestrator.py's specialist and synthesis
# guidance can reuse the identical citation-discipline boilerplate rather than drifting
# a second copy of it.
COMMON_GUIDANCE = (
    "Work only from tool results. Every number you report must be citable to a call you "
    "actually made in THIS turn."
)

# ADR-051, learned from the first live run: the model proposed sound actions but rested
# them on a claim about `stalled_state` ("down" -- a string) and on the what-if station
# LIST, so the verifier rightly dropped both; and a torque claim in "Nm" -- not a claim
# unit -- made a whole tool-wear finding unparseable. Said here, where the model reads it.
# ADR-064: the shared experiment discipline every action-proposing intent appends.
EXPERIMENT_GUIDANCE = (
    " SIMULATE BEFORE YOU PROPOSE: call get_similar_incidents (and get_action_outcomes) "
    "for what happened last time, then run_simulation_experiment ONCE with the watch "
    "firing's detector and evidence as its incident context and the catalogue "
    "interventions that bear on your hypotheses. Propose an action only if its paired "
    "change vs baseline has an interval clear of zero in the better direction; otherwise "
    "set decision to no_improvement or too_uncertain and propose nothing for it. Claim "
    "experiment figures with provenance inferred and their exact ci_low/ci_high."
)

ACTION_GUIDANCE = (
    " Every action's claim_ids must name NUMERIC claims -- a number read at the pointer, "
    "never a string such as a station state and never a list such as the what-if station "
    "list; say those in words in the rationale instead. Claim units are only uph, pct, "
    "usd, count, station_id, minutes, ratio, seconds: there is no Nm, no A and no amps, "
    "so never claim a raw torque or current value -- claim its deviation_pct as pct. A "
    "claim with any other unit makes the whole finding unreadable."
)

INTENTS: dict[str, Intent] = {
    ASK: Intent(
        key=ASK,
        label="Ask",
        instruction="{question}",
        # Empty on purpose: turn 0 of an `ask` must build the identical messages array
        # the one-shot tier built before intents existed, so the existing loop tests keep
        # covering the path the demo actually runs most often.
        guidance="",
        plan="general",
    ),
    INCIDENT: Intent(
        key=INCIDENT,
        label="Investigate this incident",
        instruction=(
            "Investigate the current incident on this line and report what you can "
            "establish. {question}"
        ),
        guidance=(
            "METHOD FOR AN INCIDENT: establish WHAT CHANGED before explaining why. Rule "
            "out infrastructure before behaviour -- a constraint that appears to have "
            "moved is sometimes a sensor that went dark, so check get_sensor_dropouts "
            "and get_data_quality before concluding the line changed. Then identify the "
            "affected units with get_containment rather than describing them in the "
            "abstract. Then TEST the flow hypotheses at the current constraint with ONE "
            "run_simulation_experiment. Keep four things apart in your answer: observed "
            "facts (measured claims), inferred relationships, simulation projections "
            "(experiment claims) and recommendations. If the evidence supports an "
            "association but not a cause, say so in those words. If a procedure or fault-code meaning would strengthen your "
            "evidence, call search_engineering_knowledge and cite anything you quote or "
            "paraphrase from it in `knowledge_citations`, never in prose alone. "
            + COMMON_GUIDANCE
        ),
        plan="incident",
    ),
    HANDOVER: Intent(
        key=HANDOVER,
        label="Generate shift handover",
        instruction=(
            "Produce a shift handover for the operator taking over this line."
        ),
        guidance=(
            "A HANDOVER IS WRITTEN FOR THE NEXT OPERATOR, not for management. Cover four "
            "things and nothing else: (1) what changed during this shift, (2) the "
            "constraint that is open right now and where it sits, (3) what the next "
            "shift must watch, with the specific station or unit, (4) what has already "
            "been tried or ruled out, so they do not repeat it. Start from "
            "get_shift_summary. Name units and stations concretely. Anything you could "
            "not determine belongs in `unanswered` -- an incomplete handover an operator "
            "can trust beats a complete one they cannot. Read get_proposals and "
            "get_action_outcomes: say which actions were applied this shift, which still "
            "await a decision, which outcomes are not yet mature (observing), and which "
            "underperformed -- the next shift must not repeat them. " + COMMON_GUIDANCE
        ),
        plan="handover",
    ),
    SHIFT_INTEL: Intent(
        key=SHIFT_INTEL,
        label="Generate shift intelligence",
        instruction=(
            "Summarise this shift's production, risks and constraint behaviour for the "
            "shift manager."
        ),
        guidance=(
            "SHIFT INTELLIGENCE IS A RANKING, not a list. Start from get_shift_summary, "
            "then rank the top three sources of loss and qualify each one: how confident "
            "the detector is, whether the underlying statistics are measured or "
            "reconstructed, and whether the model itself is drifting (get_model_health). "
            "A loss you cannot rank confidently is worth saying so about. Then cover the "
            "operational memory: open proposals, list_simulation_experiments, approved and "
            "rejected decisions (get_proposals), and expected versus observed outcomes "
            "(get_action_outcomes), naming any recommendation that failed its expected "
            "result. " + COMMON_GUIDANCE
        ),
        plan="shift_intel",
    ),
    WHATIF: Intent(
        key=WHATIF,
        label="Run a what-if",
        instruction=(
            "Evaluate a change at station {station_id} with a fresh simulation and "
            "report the trade-off. {question}"
        ),
        guidance=(
            "YOU MUST SIMULATE, NOT ASSERT. Use run_simulation_experiment as the primary "
            "method: a paired baseline plus MORE THAN ONE supported intervention where the "
            "question allows (e.g. a feed-buffer change and a cycle-time change), then "
            "compare their intervals. Report whether each result is a fresh simulation or "
            "a grid estimate: simulate_whatif is ONLY a precomputed-grid preview, used "
            "when the experiment is unavailable, and must be labelled as interpolated. If "
            "the question asks for something the catalogue does not support -- variant "
            "sequencing, taking a station offline, reordering the line -- say plainly that "
            "the simulator cannot answer it and put that in `unanswered`. Do NOT "
            "approximate an unsupported intervention with a supported one. "
            + COMMON_GUIDANCE
        ),
        plan="whatif",
        requires_station=True,
    ),
    STARVATION: Intent(
        key=STARVATION,
        label="Prevent line starvation",
        instruction=(
            "The watch reports a station at risk of starving the line (station "
            "{station_id}). Establish the risk and propose a corrective action. {question}"
        ),
        guidance=(
            "START with get_watch_firings (detector starvation) and claim the measured "
            "stall evidence from it -- stalled_share and mean_cycle_time_s against takt_s. "
            "Confirm the line context with get_bottleneck_current. Distinguish a fault AT "
            "the station (stalled_state down) from insufficient FEED from upstream. Then "
            "experiment with mitigation increase_feed_buffer, mitigation "
            "maintenance_ticket and a cycle_time_pct change at the firing station. "
            "PROPOSE maintenance_ticket and/or increase_feed_buffer (for the downstream "
            "station) ONLY where the simulated throughput change is clear of zero -- never "
            "a buffer change the simulation shows does not help." + EXPERIMENT_GUIDANCE
            + " " + COMMON_GUIDANCE + ACTION_GUIDANCE
        ),
        plan="starvation",
    ),
    PAINT_ENV: Intent(
        key=PAINT_ENV,
        label="Mitigate paint environment risk",
        instruction=(
            "The watch reports paint-zone humidity the ambient does not explain. "
            "Establish the risk to paint quality and propose a mitigation. {question}"
        ),
        guidance=(
            "START with get_watch_firings (detector paint_env) and claim "
            "zone_humidity_mean_pct and humidity_excess_over_ambient_pct from it. Call "
            "search_engineering_knowledge for the paint humidity and dwell procedure and "
            "cite what you use. PROPOSE hvac_setpoint with target_humidity_pct at or below "
            "the humidity_threshold_pct the evidence carries, and resequence_paint_queue "
            "when exposed units are on the line now (get_recent_alerts names them). "
            "Experiment with mitigation hvac_setpoint using objective quality: the twin "
            "models humidity -> film -> defect propensity, so report the expected-defects "
            "change. resequence_paint_queue CANNOT be simulated (it changes scoring "
            "priority, not physics): say so in its rationale." + EXPERIMENT_GUIDANCE
            + " " + COMMON_GUIDANCE + ACTION_GUIDANCE
        ),
        plan="paint_env",
    ),
    TOOL_WEAR: Intent(
        key=TOOL_WEAR,
        label="Assess tool degradation",
        instruction=(
            "The watch reports a torque or weld-current drift at station {station_id}. "
            "Establish whether the tool is degrading and propose an action. {question}"
        ),
        guidance=(
            "START with get_watch_firings (detector tool_wear) and claim deviation_pct "
            "and baseline_mean from it. Call search_engineering_knowledge for the torque "
            "or weld verification procedure and cite it. Use get_station_config to find "
            "an adjacent station carrying the SAME signal, and get_station_stats for its "
            "measured util. PROPOSE a maintenance_ticket for the degraded station, and "
            "shift_weld_load to the adjacent same-capability station with the lowest "
            "util. There is no electrode-force signal: do not claim tip wear as measured. "
            "FIRST check the sensor itself is healthy (get_sensor_dropouts): a dark sensor "
            "is engineering_review, not a worn tool. Then experiment with maintenance "
            "delay_min 0 (now), maintenance at a planned stop (e.g. delay_min 120) and "
            "mitigation shift_weld_load, objective quality, and state the throughput and "
            "downtime cost of each. Never claim a remaining useful life: no model here "
            "calculates one." + EXPERIMENT_GUIDANCE + " " + COMMON_GUIDANCE
            + ACTION_GUIDANCE
        ),
        plan="tool_wear",
    ),
}

KEYS = frozenset(INTENTS)
LABELS = {k: v.label for k, v in INTENTS.items()}


def get(key: str | None) -> Intent:
    """The intent for a key, falling back to `ask`. Never raises.

    A bad key must not be able to end a run: the request layer already validates it with
    a pattern, and a worker that dies on an unexpected key would leave its row at
    `queued` forever with the failure only in a log file.
    """
    return INTENTS.get(key or ASK, INTENTS[ASK])


def expand(
    key: str | None,
    *,
    plant_id: str,
    question: str | None = None,
    station_id: int | None = None,
) -> str:
    """The task text for this turn.

    `question` is the operator's own words and may be absent for a named action;
    `station_id` is always structured, never parsed out of the question text (CLAUDE.md
    §1 bans station literals, and regexing "S17" out of prose is how a copilot ends up
    simulating the wrong station with total confidence).
    """
    intent = get(key)
    return intent.instruction.format(
        question=(question or "").strip(),
        plant_id=plant_id,
        station_id=station_id if station_id is not None else "unspecified",
    ).strip()


def guidance(key: str | None) -> str:
    return get(key).guidance


def plan(key: str | None) -> str:
    return get(key).plan


__all__ = [
    "ASK", "INCIDENT", "HANDOVER", "SHIFT_INTEL", "WHATIF",
    "STARVATION", "PAINT_ENV", "TOOL_WEAR",
    "Intent", "INTENTS", "KEYS", "LABELS", "COMMON_GUIDANCE", "ACTION_GUIDANCE",
    "get", "expand", "guidance", "plan",
]
