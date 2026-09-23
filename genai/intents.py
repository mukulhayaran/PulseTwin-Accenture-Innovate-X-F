"""The five things the copilot can be asked to do, and what each one means.

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
            "abstract. If the evidence supports an association but not a cause, say so "
            "in those words. If a procedure or fault-code meaning would strengthen your "
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
            "can trust beats a complete one they cannot. " + COMMON_GUIDANCE
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
            "A loss you cannot rank confidently is worth saying so about. " + COMMON_GUIDANCE
        ),
        plan="shift_intel",
    ),
    WHATIF: Intent(
        key=WHATIF,
        label="Run a what-if",
        instruction=(
            "Evaluate a change at station {station_id} against the simulation grid and "
            "report the trade-off. {question}"
        ),
        guidance=(
            "YOU MUST SIMULATE, NOT ASSERT. Call get_whatif_stations first to confirm the "
            "station is in the grid, then simulate_whatif. Report baseline and projected "
            "side by side and name the intervention you tested. The grid covers only "
            "upstream FEED-buffer changes and cycle-time changes at seeded stations: if "
            "the question asks for anything else -- taking a station offline, changing a "
            "tool, reordering the line -- say plainly that the simulator cannot answer it "
            "and put that in `unanswered`. Do NOT approximate an unsupported intervention "
            "with a supported one. " + COMMON_GUIDANCE
        ),
        plan="whatif",
        requires_station=True,
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
    "Intent", "INTENTS", "KEYS", "LABELS", "COMMON_GUIDANCE",
    "get", "expand", "guidance", "plan",
]
