"""The structured shape the agent must emit, and a tolerant parser for it.

The agent does not return prose. It returns claims, each carrying the tool call and the
RFC 6901 JSON Pointer the number came from, so `genai/verifier.py` can re-check every
one against the recorded tool results without a model in the loop.

This module imports NOTHING from the provider SDK and does no I/O. It is pure, so the
whole contract is unit-testable on a machine with no API key.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

# Units the verifier knows how to police. `usd` triggers the sourced-economics contract
# (CLAUDE.md §3); `station_id` is exempt from the uncited-number sweep when the value is
# a real station in the plant config.
UNITS = ("uph", "pct", "usd", "count", "station_id", "minutes", "ratio", "seconds")

# ADR-064: why an investigation stopped. `recommend` is ONE outcome among several -- an
# agent that finds nothing it can support should say which of these it hit, not propose
# something anyway. Unknown values degrade to "" rather than failing the parse.
DECISIONS = ("recommend", "insufficient_evidence", "unsupported_intervention",
             "simulator_unavailable", "no_improvement", "too_uncertain",
             "engineering_review")
HYPOTHESIS_STATUS = ("supported", "rejected", "untested")

PROVENANCE = ("measured", "inferred")

CONFIDENCE = ("low", "medium", "high")

# ADR-051: the CLOSED catalogue of actions an agent may propose, and the params each one
# requires. An agent proposes; a human approves; the twin applies. Nothing here executes.
# `serve/routers/proposals.py::ACTION_EFFECTS` mirrors these keys (the import wall), and a
# test asserts the two agree.
ACTION_TYPES: dict[str, tuple[str, ...]] = {
    "increase_feed_buffer": ("station_id", "buffer_delta"),
    "rebalance_cycle": ("station_id", "cycle_delta_pct"),
    "shift_weld_load": ("from_station", "to_station"),
    "maintenance_ticket": ("station_id",),
    "hvac_setpoint": ("target_humidity_pct",),
    "resequence_paint_queue": (),
}
# Every param name any action may carry -- the strict response schema must list them all.
ACTION_PARAMS = ("station_id", "buffer_delta", "cycle_delta_pct", "from_station",
                 "to_station", "signal", "target_humidity_pct")


class ParseError(Exception):
    """The model's output could not be read as a Finding. Always ends the run."""


@dataclass(frozen=True)
class Citation:
    """Where a number came from.

    `call_id` is assigned by OUR loop (`call_0001`, `call_0002`, ...) and injected into
    the tool result the model reads. The model can therefore only cite ids it was
    actually shown -- a fabricated id fails verification immediately instead of being
    ambiguous. `pointer` is RFC 6901 into that call's recorded result.
    """

    call_id: str
    pointer: str


@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    value: float
    unit: str
    provenance: str
    citation: Citation
    ci_low: float | None = None      # required when provenance == "inferred"
    ci_high: float | None = None
    source: str | None = None        # required when unit == "usd"


@dataclass(frozen=True)
class KnowledgeCitation:
    """A quoted or paraphrased excerpt from `search_engineering_knowledge` (ADR-046).

    The text-shaped counterpart to Claim: instead of a numeric value re-derived from a
    tool result, `quoted_text` must be a real (verifier-checked) substring of the source
    chunk's own text at `citation`. Kept as a SEPARATE list from `claims`, not folded in,
    because it is checked by a different rule (substring match, not numeric tolerance) --
    conflating the two would mean one field meant two different verification contracts.
    """

    id: str
    quoted_text: str
    doc_id: str
    section: str
    citation: Citation


@dataclass(frozen=True)
class ProposedAction:
    """A corrective action the agent PROPOSES (ADR-051) -- never executes.

    `claim_ids` name the claims that justify it. The verifier serves the action only if
    its type is in ACTION_TYPES, its required params are present, and every cited claim
    verified: an action resting on a number the verifier removed is removed with it.
    """

    id: str
    action_type: str
    params: dict[str, Any]
    rationale: str
    claim_ids: list[str]


@dataclass(frozen=True)
class Hypothesis:
    """A bounded explanation the agent considered (ADR-064). `statement` is prose and is
    swept for uncited numbers like the summary; `claim_ids` name the evidence."""

    id: str
    statement: str
    status: str
    claim_ids: list[str] = field(default_factory=list)
    experiment_id: str | None = None


@dataclass(frozen=True)
class Finding:
    question: str
    summary: str
    claims: list[Claim]
    recommendation: str
    alternatives_considered: list[str] = field(default_factory=list)
    # A STRING enum, not a float. A model-produced numeric confidence is the one number
    # in the output that could never be cited -- it would either fail the uncited-number
    # sweep or need an exemption that a hallucinated figure could then hide behind.
    confidence: str = "low"
    # First-class output: "I could not read /api/economics/roi at role=manager (403)"
    # is a good answer. An agent that cannot say "I don't know" will say something else.
    unanswered: list[str] = field(default_factory=list)
    # Empty for every Finding produced before ADR-046, and for any turn where the model
    # simply chose not to use search_engineering_knowledge -- never required.
    knowledge_citations: list[KnowledgeCitation] = field(default_factory=list)
    # ADR-051. Empty unless the agent proposed a corrective action.
    proposed_actions: list[ProposedAction] = field(default_factory=list)
    # ADR-064. Optional; absent on every Finding written before experiments existed.
    decision: str = ""
    hypotheses: list[Hypothesis] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _extract_json_object(text: str) -> str:
    """Pull the outermost {...} out of a response that may be wrapped in prose.

    Brace-balanced and string-aware rather than a regex: a regex counting braces breaks
    the moment a station name or an `assumptions` string contains one, and those strings
    routinely do.
    """
    start = text.find("{")
    if start == -1:
        raise ParseError("no JSON object in model output")

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ParseError("unbalanced JSON object in model output")


def _require(obj: dict[str, Any], key: str, kind: type | tuple[type, ...]) -> Any:
    if key not in obj:
        raise ParseError(f"missing required field: {key}")
    val = obj[key]
    if not isinstance(val, kind):
        raise ParseError(f"field {key} has type {type(val).__name__}, expected {kind}")
    return val


def parse_finding(text: str, *, question: str) -> Finding:
    """Parse model output into a Finding, or raise ParseError.

    Works whether or not the provider honoured `response_format` -- structured output is
    passed as a hint to cut retries, never as a correctness dependency, because schema
    support varies across OpenAI-compatible endpoints.
    """
    if not text or not text.strip():
        raise ParseError("empty model output")

    try:
        raw = json.loads(_extract_json_object(text))
    except json.JSONDecodeError as exc:
        raise ParseError(f"invalid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ParseError("top-level JSON value is not an object")

    claims: list[Claim] = []
    for idx, c in enumerate(_require(raw, "claims", list)):
        if not isinstance(c, dict):
            raise ParseError(f"claims[{idx}] is not an object")
        cit = _require(c, "citation", dict)
        unit = _require(c, "unit", str)
        if unit not in UNITS:
            raise ParseError(f"claims[{idx}].unit {unit!r} not in {UNITS}")
        prov = _require(c, "provenance", str)
        if prov not in PROVENANCE:
            raise ParseError(f"claims[{idx}].provenance {prov!r} not in {PROVENANCE}")
        value = _require(c, "value", (int, float))
        if isinstance(value, bool):
            raise ParseError(f"claims[{idx}].value is a bool")

        claims.append(
            Claim(
                id=str(c.get("id") or f"c{idx + 1}"),
                text=_require(c, "text", str),
                value=float(value),
                unit=unit,
                provenance=prov,
                citation=Citation(
                    call_id=_require(cit, "call_id", str),
                    pointer=_require(cit, "pointer", str),
                ),
                ci_low=_opt_float(c.get("ci_low")),
                ci_high=_opt_float(c.get("ci_high")),
                source=c.get("source") if isinstance(c.get("source"), str) else None,
            )
        )

    confidence = raw.get("confidence", "low")
    if confidence not in CONFIDENCE:
        confidence = "low"

    # OPTIONAL key (ADR-046), not required like `claims` -- absent entirely means the
    # model made no knowledge-tool citations this turn, which every Finding produced
    # before this field existed already looks like. A PRESENT-but-malformed entry is
    # still a hard parse error, the same strictness `claims` gets: half-structured output
    # is not more trustworthy than none.
    knowledge_citations: list[KnowledgeCitation] = []
    for idx, kc in enumerate(raw.get("knowledge_citations") or []):
        if not isinstance(kc, dict):
            raise ParseError(f"knowledge_citations[{idx}] is not an object")
        cit = _require(kc, "citation", dict)
        knowledge_citations.append(
            KnowledgeCitation(
                id=str(kc.get("id") or f"k{idx + 1}"),
                quoted_text=_require(kc, "quoted_text", str),
                doc_id=_require(kc, "doc_id", str),
                section=_require(kc, "section", str),
                citation=Citation(
                    call_id=_require(cit, "call_id", str),
                    pointer=_require(cit, "pointer", str),
                ),
            )
        )

    # OPTIONAL (ADR-051), same strictness as knowledge_citations for SHAPE. An unknown
    # action_type is NOT a parse error: the verifier drops it, so one invented action
    # costs that action, not the whole investigation.
    proposed_actions: list[ProposedAction] = []
    for idx, pa in enumerate(raw.get("proposed_actions") or []):
        if not isinstance(pa, dict):
            raise ParseError(f"proposed_actions[{idx}] is not an object")
        params = _require(pa, "params", dict)
        claim_ids = _require(pa, "claim_ids", list)
        proposed_actions.append(
            ProposedAction(
                id=str(pa.get("id") or f"a{idx + 1}"),
                action_type=_require(pa, "action_type", str),
                # A strict schema forces every param key to be present; null means absent.
                params={k: v for k, v in params.items() if v is not None},
                rationale=_require(pa, "rationale", str),
                claim_ids=[str(c) for c in claim_ids],
            )
        )

    # ADR-064, OPTIONAL and LENIENT: a hypothesis is context, not evidence -- one badly
    # shaped entry costs that entry, never the investigation.
    hypotheses: list[Hypothesis] = []
    for idx, h in enumerate(raw.get("hypotheses") or []):
        if not isinstance(h, dict) or not isinstance(h.get("statement"), str):
            continue
        status = h.get("status") if h.get("status") in HYPOTHESIS_STATUS else "untested"
        hypotheses.append(Hypothesis(
            id=str(h.get("id") or f"h{idx + 1}"), statement=h["statement"], status=status,
            claim_ids=[str(c) for c in h.get("claim_ids") or [] if isinstance(c, (str, int))],
            experiment_id=(h.get("experiment_id")
                           if isinstance(h.get("experiment_id"), str) else None)))
    decision = raw.get("decision") if raw.get("decision") in DECISIONS else ""

    return Finding(
        question=question,
        summary=_require(raw, "summary", str),
        claims=claims,
        recommendation=_require(raw, "recommendation", str),
        knowledge_citations=knowledge_citations,
        proposed_actions=proposed_actions,
        alternatives_considered=[
            str(a) for a in raw.get("alternatives_considered", []) if isinstance(a, str)
        ],
        confidence=confidence,
        unanswered=[str(u) for u in raw.get("unanswered", []) if isinstance(u, str)],
        decision=decision,
        hypotheses=hypotheses,
    )


def _opt_float(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)
