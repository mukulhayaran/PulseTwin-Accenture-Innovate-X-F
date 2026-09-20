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

PROVENANCE = ("measured", "inferred")

CONFIDENCE = ("low", "medium", "high")


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

    return Finding(
        question=question,
        summary=_require(raw, "summary", str),
        claims=claims,
        recommendation=_require(raw, "recommendation", str),
        alternatives_considered=[
            str(a) for a in raw.get("alternatives_considered", []) if isinstance(a, str)
        ],
        confidence=confidence,
        unanswered=[str(u) for u in raw.get("unanswered", []) if isinstance(u, str)],
    )


def _opt_float(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)
