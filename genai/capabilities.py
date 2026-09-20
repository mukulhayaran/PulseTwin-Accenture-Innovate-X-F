"""Provider capability negotiation: degrade the request, never the guarantee.

`genai/loop.py` sends three things an OpenAI-COMPATIBLE endpoint may not actually
support, and Groq is exactly such an endpoint:

  * `response_format={"type": "json_schema", ...}` with a nested strict schema
  * `"strict": True` on every tool definition
  * `seed=42`

Support for all three is model-dependent. A rejection is an HTTP 400, which
`client.classify_error` maps to `provider_status_400`, which FAILS THE RUN. Before this
module existed, pointing the tier at a model that dislikes strict schemas turned every
question into a hard error with no diagnosis.

WHY A LADDER AND NOT A STARTUP PROBE
------------------------------------
A probe costs a round trip on the happy path, and -- worse -- it can pass while the real
request fails, because the real request carries a large `tools` array, a long system
prompt AND a nested schema. Rejections at these endpoints are frequently about the
COMBINATION rather than any one parameter. So degrade against the real payload.

THE ORDER IS NOT ARBITRARY. Each rung gives up the guarantee that this package has
ALREADY documented as non-load-bearing, weakest claim first:

  L0 -> L1  drop `strict` on tools.     `tools.py::to_wire` already says validate_args
                                        re-checks locally regardless. Costs nothing.
  L1 -> L2  json_schema -> json_object. `finding.py::parse_finding` already says it
                                        works "whether or not the provider honoured
                                        response_format" -- it extracts a brace-balanced
                                        object out of prose.
  L2 -> L3  drop `seed`.                `config.py` already says a fixed seed does not
                                        make a hosted model reproducible.
  L3 -> L4  drop response_format.       Last resort; the tolerant parser carries it.

MEASURED AGAINST THE REAL ENDPOINT (2026-09-21). Groq's `openai/gpt-oss-120b` rejects
L0, L1, L2 AND L3 with one identical error -- `json mode cannot be combined with
tool/function calling` -- because ANY `response_format` is incompatible with `tools`,
regardless of strictness. It answers L4 normally, with tool calls. So for this provider
the ladder's rung order is beside the point and only its destination matters, which is
why `degrade()` reads the provider's error text and jumps straight past the rungs that
would fail for the reason it just stated. The rung order still earns its place on an
endpoint whose objection IS the strictness rather than the parameter.

What NEVER degrades: the verifier, the citation contract, the tool registry, the role
cap. A degraded request still produces a finding that must pass the identical gate. The
rung is persisted as `triage_runs.capability_profile` and shown in the UI, because a
silently degraded run is the kind of thing this project refuses to ship.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from genai import config

# Response-format modes, weakest constraint last.
JSON_SCHEMA = "json_schema"
JSON_OBJECT = "json_object"
NO_FORMAT = "none"


@dataclass(frozen=True)
class Capabilities:
    name: str                 # "L0".."L4"; persisted as triage_runs.capability_profile
    response_format: str      # JSON_SCHEMA | JSON_OBJECT | NO_FORMAT
    strict_tools: bool
    seed: bool


LADDER: tuple[Capabilities, ...] = (
    Capabilities("L0", JSON_SCHEMA, True, True),
    Capabilities("L1", JSON_SCHEMA, False, True),
    Capabilities("L2", JSON_OBJECT, False, True),
    Capabilities("L3", JSON_OBJECT, False, False),
    Capabilities("L4", NO_FORMAT, False, False),
)

_BY_NAME = {c.name: c for c in LADDER}

# Only a request-SHAPE rejection degrades. Deliberately NOT 401 (bad key), 403 (no
# access to the model), 404 (no such model) or 413 (payload too large): those must fail
# loudly on the first attempt instead of silently walking four rungs and then failing
# anyway with a misleading profile recorded against the run. Widening this set is how
# this module would start hiding real misconfiguration -- do not.
_CAPABILITY_REJECTIONS = frozenset({"provider_status_400", "provider_status_422"})

# Phrases an OpenAI-compatible endpoint uses when the problem is `response_format`
# ITSELF rather than the strictness of the schema inside it.
#
# MEASURED, not guessed. Groq's `openai/gpt-oss-120b` answers every one of L0..L3 with
# the identical 400:
#
#     {"error": {"message": "json mode cannot be combined with tool/function calling",
#                "type": "invalid_request_error", "param": "response_format"}}
#
# so walking the ladder one rung at a time spends four rejected round trips discovering
# one fact -- and on a fresh machine those four count against the provider's OWN rate
# limit, which is exactly how the first live run of this tier died. When the error names
# the parameter, believe it and jump.
_RESPONSE_FORMAT_HINTS = (
    "response_format",
    "json mode cannot be combined",
    "json_schema is not supported",
    "response format is not supported",
)

_CACHE_FILENAME = "genai_caps.json"
_memo: dict[str, str] = {}


def blames_response_format(detail: str | None) -> bool:
    """True if the provider's error text points at `response_format`."""
    if not detail:
        return False
    low = detail.lower()
    return any(h in low for h in _RESPONSE_FORMAT_HINTS)


def is_capability_rejection(code: str | None) -> bool:
    return code in _CAPABILITY_REJECTIONS


def _first_no_format_below(idx: int) -> Capabilities | None:
    """The first rung below `idx` that sends no `response_format` at all."""
    return next((c for i, c in enumerate(LADDER)
                 if c.response_format == NO_FORMAT and i > idx), None)


def suppresses_tool_calls(caps: Capabilities) -> Capabilities | None:
    """The rung to fall to when `response_format` is accepted but breaks tool calling.

    NOT every rejection is an HTTP 400. MEASURED against OpenRouter's
    `nvidia/nemotron-3-ultra-550b-a55b:free` on 2026-09-21: sending `response_format`
    alongside `tools` returns HTTP 200 and the model then emits NO tool call at all,
    answering the question from nothing and wrapping the result in malformed JSON. Groq
    refuses the same request with a 400, which `degrade` handles; this endpoint accepts
    it and quietly breaks the agent instead, so `is_capability_rejection` never fires and
    the run ends with zero tool calls and an unparseable finding.

    A first turn with no tool call is ALWAYS wrong for this agent -- the system prompt
    orders it to establish what happened before answering, and an answer with no tool
    result behind it has no citable evidence and would fail verification anyway. So the
    signature is unambiguous, and the response to it is the same as for a 400: drop
    `response_format` and re-ask. Returns None if this rung already sends none, which is
    what makes the caller's retry terminate.
    """
    if caps.response_format == NO_FORMAT:
        return None
    return _first_no_format_below(LADDER.index(caps))


def degrade(caps: Capabilities, detail: str | None = None) -> Capabilities | None:
    """The next rung down, or None at the bottom.

    `detail` is the provider's error text. When it names `response_format` as the
    offending parameter, skip straight to the first rung that omits it entirely --
    every intermediate rung would fail for the same stated reason, and each failure is a
    request the provider counts.

    Still strictly monotone: the jump only ever moves DOWN the ladder, and never past
    the end, so the retry loop that calls this always terminates.
    """
    idx = LADDER.index(caps)

    if blames_response_format(detail):
        target = _first_no_format_below(idx)
        if target is not None:
            return target

    return LADDER[idx + 1] if idx + 1 < len(LADDER) else None


def _cache_path(cache_dir: Path | None = None) -> Path:
    base = cache_dir or (
        Path(os.environ.get("PULSETWIN_JOBS_DIR", tempfile.gettempdir()))
        / "pulsetwin_triage_runs"
    )
    return base / _CACHE_FILENAME


def initial(model_id: str, *, cache_dir: Path | None = None) -> Capabilities:
    """The rung to start at: whatever this model settled on last time, else the top.

    Each turn of a conversation is a fresh process (ADR-037: serve/ spawns genai/ rather
    than hosting it), so without this file every turn re-pays the rejected requests. It
    is a CACHE and nothing else: deleting it may cost one slow turn and must never change
    a verdict.
    """
    if model_id in _memo:
        return _BY_NAME.get(_memo[model_id], LADDER[0])
    try:
        raw = json.loads(_cache_path(cache_dir).read_text(encoding="utf-8"))
        name = raw.get(model_id) if isinstance(raw, dict) else None
    except (OSError, ValueError):
        name = None
    return _BY_NAME.get(name, LADDER[0]) if isinstance(name, str) else LADDER[0]


def remember(model_id: str, caps: Capabilities, *, cache_dir: Path | None = None) -> None:
    """Persist the settled rung. Never raises -- a cache failure must not end a run."""
    _memo[model_id] = caps.name
    path = _cache_path(cache_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        raw[model_id] = caps.name
        path.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def reset_cache() -> None:
    """Clear the in-process memo. Tests only."""
    _memo.clear()


def strip_strict(wire_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A copy of the tool definitions with `strict` removed.

    A COPY, not an in-place edit: `to_wire`'s output is built once per run and reused
    every iteration, so mutating it would make the first degradation permanent for the
    process and invisible to anything still holding the original list.
    """
    out: list[dict[str, Any]] = []
    for tool in wire_tools:
        fn = {k: v for k, v in tool.get("function", {}).items() if k != "strict"}
        out.append({**tool, "function": fn})
    return out


def request_kwargs(
    caps: Capabilities,
    *,
    model: str,
    messages: list[Any],
    tools: list[dict[str, Any]],
    temperature: float,
    response_format: Any = None,
) -> dict[str, Any]:
    """Assemble exactly one chat-completions request for this rung.

    Pure: no client, no network, no clock. This is the ONLY place a request is built, so
    the five rungs are a five-case unit test rather than five code paths.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": tools if caps.strict_tools else strip_strict(tools),
        "tool_choice": "auto",
        "temperature": temperature,
    }
    if caps.seed:
        kwargs["seed"] = config.SEED
    if caps.response_format == JSON_SCHEMA:
        kwargs["response_format"] = response_format
    elif caps.response_format == JSON_OBJECT:
        kwargs["response_format"] = {"type": "json_object"}
    # NO_FORMAT: omit the key entirely rather than sending null, which some
    # OpenAI-compatible endpoints reject as a type error in its own right.
    return kwargs


__all__ = [
    "Capabilities", "LADDER", "JSON_SCHEMA", "JSON_OBJECT", "NO_FORMAT",
    "initial", "degrade", "suppresses_tool_calls", "remember", "reset_cache",
    "request_kwargs", "strip_strict", "is_capability_rejection",
]
