"""The capability-degradation ladder.

`genai/capabilities.py` exists because an OpenAI-COMPATIBLE endpoint is not an OpenAI
endpoint: Groq may reject strict json_schema, strict tool definitions or `seed`, and a
rejection is an HTTP 400 that fails the whole run rather than degrading. These tests pin
the four properties that make the ladder safe:

  * it degrades ONLY on a request-shape rejection, never on a bad key or a bad model id;
  * it is monotone and bounded, so the retry loop cannot spin;
  * `request_kwargs` is pure, so the five rungs are five assertions rather than five code
    paths nobody runs;
  * a degraded run still produces a finding that passes the identical verifier.

No API key, no network, no `openai` import: `run_loop` takes `classify_error` as a
parameter, so a stub can raise a plain sentinel and the test supplies the mapping.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("fastapi")

from genai import capabilities as C  # noqa: E402
from genai import config  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_memo():
    C.reset_cache()
    yield
    C.reset_cache()


# --------------------------------------------------------------------------- the ladder
def test_ladder_starts_at_full_strictness() -> None:
    top = C.LADDER[0]
    assert (top.name, top.response_format, top.strict_tools, top.seed) == (
        "L0", C.JSON_SCHEMA, True, True)


def test_degrade_is_monotone_and_terminates() -> None:
    seen, caps = [], C.LADDER[0]
    while caps is not None:
        seen.append(caps.name)
        caps = C.degrade(caps)
    assert seen == ["L0", "L1", "L2", "L3", "L4"]
    assert C.degrade(C.LADDER[-1]) is None


def test_the_weakest_guarantee_is_surrendered_first() -> None:
    """Order matters and is argued in the module docstring: `strict` goes first because
    tools.py already re-validates locally, so dropping it costs nothing real."""
    assert C.LADDER[0].strict_tools and not C.LADDER[1].strict_tools
    assert C.LADDER[1].response_format == C.JSON_SCHEMA
    assert C.LADDER[2].response_format == C.JSON_OBJECT
    # `seed` survives longer than response_format: config.py already disclaims that it
    # makes a hosted model reproducible, but it is also the least likely cause of a 400.
    assert C.LADDER[2].seed and not C.LADDER[3].seed
    assert C.LADDER[4].response_format == C.NO_FORMAT


# ----------------------------------------------------------------- what counts as a 400
@pytest.mark.parametrize("code", ["provider_status_400", "provider_status_422"])
def test_request_shape_rejections_degrade(code: str) -> None:
    assert C.is_capability_rejection(code) is True


@pytest.mark.parametrize("code", [
    "provider_status_401",   # bad key
    "provider_status_403",   # no access to the model
    "provider_status_404",   # no such model
    "provider_status_413",   # payload too large
    "provider_timeout",
    "provider_unreachable",
    "rate_limited",
    None,
])
def test_everything_else_fails_loudly(code) -> None:
    """Widening this set is how the ladder would start hiding real misconfiguration: a
    wrong key would walk four rungs and then fail anyway, with a misleading profile
    recorded against the run."""
    assert C.is_capability_rejection(code) is False


# ------------------------------------------------------------------- request_kwargs
TOOLS = [{"type": "function",
          "function": {"name": "t", "strict": True, "parameters": {}}}]


def _kwargs(caps):
    return C.request_kwargs(caps, model="m", messages=[], tools=TOOLS,
                            temperature=0.0, response_format={"type": "json_schema"})


def test_l0_sends_everything() -> None:
    k = _kwargs(C.LADDER[0])
    assert k["response_format"] == {"type": "json_schema"}
    assert k["seed"] == config.SEED
    assert k["tools"][0]["function"]["strict"] is True


def test_l1_drops_strict_only() -> None:
    k = _kwargs(C.LADDER[1])
    assert "strict" not in k["tools"][0]["function"]
    assert k["response_format"] == {"type": "json_schema"}
    assert "seed" in k


def test_l2_falls_back_to_json_object() -> None:
    assert _kwargs(C.LADDER[2])["response_format"] == {"type": "json_object"}


def test_l3_drops_the_seed() -> None:
    assert "seed" not in _kwargs(C.LADDER[3])


def test_l4_omits_response_format_entirely() -> None:
    """Omitted, not null: some OpenAI-compatible endpoints reject an explicit null as a
    type error in its own right, which would make the last rung fail for a new reason."""
    assert "response_format" not in _kwargs(C.LADDER[4])


def test_strip_strict_does_not_mutate_the_caller_s_tools() -> None:
    """`to_wire` is built once per run and reused every iteration; mutating it in place
    would make the first degradation permanent and invisible."""
    before = TOOLS[0]["function"]["strict"]
    C.request_kwargs(C.LADDER[4], model="m", messages=[], tools=TOOLS, temperature=0.0)
    assert TOOLS[0]["function"]["strict"] == before


def test_request_kwargs_is_pure() -> None:
    """No client, no clock, no network -- so the rungs are unit-testable at all."""
    a = _kwargs(C.LADDER[2])
    b = _kwargs(C.LADDER[2])
    assert a == b


# ------------------------------------------------------------------------------ cache
def test_remember_and_initial_round_trip(tmp_path: Path) -> None:
    C.remember("model-x", C.LADDER[2], cache_dir=tmp_path)
    C.reset_cache()
    assert C.initial("model-x", cache_dir=tmp_path).name == "L2"


def test_unknown_model_starts_at_the_top(tmp_path: Path) -> None:
    assert C.initial("never-seen", cache_dir=tmp_path).name == "L0"


def test_a_corrupt_cache_is_not_fatal(tmp_path: Path) -> None:
    """A cache is a speed-up and nothing else: deleting or corrupting it may cost one
    slow turn and must never change a verdict."""
    (tmp_path / "genai_caps.json").write_text("{not json", encoding="utf-8")
    assert C.initial("model-x", cache_dir=tmp_path).name == "L0"


def test_an_unknown_rung_name_is_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "genai_caps.json").write_text('{"model-x": "L99"}', encoding="utf-8")
    assert C.initial("model-x", cache_dir=tmp_path).name == "L0"


def test_remember_never_raises_on_an_unwritable_dir(tmp_path: Path) -> None:
    C.remember("model-x", C.LADDER[1], cache_dir=tmp_path / "nope" / "deeper")


# ------------------------------------------------- learned from the live endpoint
#
# Everything below is a behaviour MEASURED against Groq's `openai/gpt-oss-120b` on
# 2026-09-21, not a guess about what a provider might do.


def test_a_response_format_complaint_jumps_straight_past_the_doomed_rungs() -> None:
    """Groq answers L0, L1, L2 AND L3 with one identical 400 -- any `response_format` is
    incompatible with `tools`, regardless of strictness. Walking a rung at a time spends
    four rejected round trips learning one fact, and on a fresh machine those four count
    against the provider's own rate limit. That is how the first live run died."""
    groq = ("Error code: 400 - {'error': {'message': 'json mode cannot be combined with "
            "tool/function calling', 'type': 'invalid_request_error', "
            "'param': 'response_format'}}")

    nxt = C.degrade(C.LADDER[0], groq)
    assert nxt is not None and nxt.response_format == C.NO_FORMAT
    assert nxt.name == "L4"


@pytest.mark.parametrize("detail", [
    "json mode cannot be combined with tool/function calling",
    "Invalid parameter: response_format",
    "json_schema is not supported for this model",
    "response format is not supported",
])
def test_the_hints_are_recognised(detail: str) -> None:
    assert C.blames_response_format(detail) is True


@pytest.mark.parametrize("detail", [None, "", "bad request", "context length exceeded"])
def test_an_unrelated_400_still_walks_one_rung(detail) -> None:
    """The jump is an optimisation for a stated cause, never the default."""
    assert C.blames_response_format(detail) is False
    assert C.degrade(C.LADDER[0], detail).name == "L1"


def test_the_jump_is_still_monotone_and_terminates() -> None:
    groq = "param: response_format"
    for rung in C.LADDER[:-1]:
        nxt = C.degrade(rung, groq)
        assert nxt is not None
        assert C.LADDER.index(nxt) > C.LADDER.index(rung)
    assert C.degrade(C.LADDER[-1], groq) is None


def test_a_bad_tool_call_is_not_a_capability_problem() -> None:
    """Groq validates tool syntax server-side and returns 400 `tool_use_failed` when the
    MODEL invents a tool name or writes prose where the arguments JSON belongs. Degrading
    OUR request cannot fix that, and treating it as a rung burned a degradation per
    occurrence and recorded a misleading capability_profile against the run."""
    assert C.is_capability_rejection("model_tool_call_invalid") is False
    assert C.is_capability_rejection("provider_status_400") is True


# ------------------------------------------------- the SOFT rejection (200, no 400)
#
# MEASURED against OpenRouter's `nvidia/nemotron-3-ultra-550b-a55b:free` on 2026-09-21.
# Groq REFUSES `response_format` alongside `tools` with a 400, which the ladder above
# handles. OpenRouter ACCEPTS the same request with HTTP 200 and the model then emits no
# tool call at all, answering from nothing and wrapping it in malformed JSON. No status
# code is ever raised, so `is_capability_rejection` never fires and the run ends with
# zero tool calls and an unparseable finding -- silently, on the top rung, which is
# where every new model starts.
def test_a_soft_rejection_drops_response_format_entirely() -> None:
    """One step, straight to the first rung that sends no `response_format`.

    Walking L0->L1->L2->L3 would spend three more round trips discovering the same fact,
    and on a free tier those count against the provider's own budget.
    """
    for rung in C.LADDER[:-1]:
        nxt = C.suppresses_tool_calls(rung)
        assert nxt is not None
        assert nxt.response_format == C.NO_FORMAT
        assert C.LADDER.index(nxt) > C.LADDER.index(rung)


def test_a_soft_rejection_terminates_at_the_bottom() -> None:
    """L4 already sends no `response_format`, so there is nothing left to blame.

    This is what stops the loop's retry from spinning: a model that returns no tool call
    at L4 is simply a model that chose not to call one, and the verifier -- not the
    ladder -- is what rejects an answer with no evidence behind it.
    """
    assert C.LADDER[-1].response_format == C.NO_FORMAT
    assert C.suppresses_tool_calls(C.LADDER[-1]) is None
