"""Agent-loop mechanics, driven by a scripted stub in place of the provider.

No credential, no network, no database. What is under test is the loop's recording
discipline -- runner-assigned call_ids, truncation, the two caps -- because those are
what the verifier's guarantees actually rest on.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("fastapi")

if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from genai import config, loop as L, tools as T  # noqa: E402
from genai.api_client import PulseTwinAPI  # noqa: E402
from genai.api_client import testclient_transport as _tc_transport  # noqa: E402
from serve.main import app  # noqa: E402

PLANTS = ["plant_a"]


# ------------------------------------------------------------------ provider stubs
@dataclass
class _Fn:
    name: str
    arguments: str


@dataclass
class _Call:
    id: str
    function: _Fn


@dataclass
class _Msg:
    content: str | None = None
    tool_calls: list[_Call] = field(default_factory=list)


@dataclass
class _Choice:
    message: _Msg
    finish_reason: str


@dataclass
class _Usage:
    prompt_tokens: int = 100
    completion_tokens: int = 20


@dataclass
class _Resp:
    choices: list[_Choice]
    usage: _Usage = field(default_factory=_Usage)


class StubClient:
    """Replays a script of responses; records the messages it was sent."""

    def __init__(self, script: list[_Resp]) -> None:
        self._script = list(script)
        self.seen_messages: list[list[Any]] = []
        self.chat = self                     # client.chat.completions.create(...)
        self.completions = self

    def create(self, **kwargs: Any) -> _Resp:
        self.seen_messages.append(list(kwargs["messages"]))
        if not self._script:
            return _Resp([_Choice(_Msg(content="{}"), "stop")])
        return self._script.pop(0)


def _tool_turn(*specs: tuple[str, dict]) -> _Resp:
    calls = [_Call(f"prov_{i}", _Fn(name, json.dumps(args)))
             for i, (name, args) in enumerate(specs)]
    return _Resp([_Choice(_Msg(tool_calls=calls), "tool_calls")])


def _final(text: str) -> _Resp:
    return _Resp([_Choice(_Msg(content=text), "stop")])


@pytest.fixture(scope="module")
def registry():
    return T.build_registry(PLANTS)


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as c:
        yield PulseTwinAPI("http://testserver", "leadership", "looptest",
                           transport=_tc_transport(c))


def _run(client, api, registry, **kw):
    return L.run_loop(client, api, registry, plant_id="plant_a",
                      question="why did the constraint move?",
                      acting_role="leadership", **kw)


# --------------------------------------------------------------------------- basics
def test_finishes_and_returns_content(registry, api) -> None:
    # A toolless answer is re-asked: once as a rung degradation when the run starts on a
    # response_format rung (a fresh capability cache), once more as the evidence-first
    # nudge. Enough identical answers for either starting rung.
    out = _run(StubClient([_final('{"summary":"s"}')] * 3), api, registry)
    assert out.status == "finished"
    assert out.content == '{"summary":"s"}'
    assert out.records == []


def test_executes_a_tool_then_finishes(registry, api) -> None:
    client = StubClient([
        _tool_turn(("get_bottleneck_current", {"plant_id": "plant_a"})),
        _final("{}"),
    ])
    out = _run(client, api, registry)
    assert out.status == "finished"
    assert [r.name for r in out.records] == ["get_bottleneck_current"]
    assert out.records[0].result["ok"] is True


def test_call_ids_are_runner_assigned_and_sequential(registry, api) -> None:
    """The model can only cite ids it was shown -- a fabricated id fails C1."""
    client = StubClient([
        _tool_turn(("get_bottleneck_current", {"plant_id": "plant_a"}),
                   ("get_station_stats", {"plant_id": "plant_a"})),
        _tool_turn(("get_metrics", {"plant_id": "plant_a"})),
        _final("{}"),
    ])
    out = _run(client, api, registry)
    assert [r.call_id for r in out.records] == ["call_0001", "call_0002", "call_0003"]
    assert set(out.transcript) == {"call_0001", "call_0002", "call_0003"}


def test_parallel_tool_calls_in_one_turn_all_execute(registry, api) -> None:
    client = StubClient([
        _tool_turn(("get_bottleneck_current", {"plant_id": "plant_a"}),
                   ("get_station_stats", {"plant_id": "plant_a"}),
                   ("get_metrics", {"plant_id": "plant_a"})),
        _final("{}"),
    ])
    out = _run(client, api, registry)
    assert len(out.records) == 3


def test_tool_results_are_appended_as_tool_role_messages(registry, api) -> None:
    client = StubClient([
        _tool_turn(("get_bottleneck_current", {"plant_id": "plant_a"})),
        _final("{}"),
    ])
    _run(client, api, registry)
    final_messages = client.seen_messages[-1]
    tool_msgs = [m for m in final_messages if isinstance(m, dict) and m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    body = json.loads(tool_msgs[0]["content"])
    assert body["call_id"] == "call_0001"
    assert tool_msgs[0]["tool_call_id"] == "prov_0"


def test_usage_accumulates_across_turns(registry, api) -> None:
    client = StubClient([
        _tool_turn(("get_metrics", {"plant_id": "plant_a"})),
        _final("{}"),
    ])
    out = _run(client, api, registry)
    assert out.prompt_tokens == 200 and out.completion_tokens == 40


# ----------------------------------------------------------------------------- caps
def test_iteration_cap_is_terminal(registry, api) -> None:
    """Never ask for a summary under duress -- that is where uncited numbers appear."""
    script = [_tool_turn(("get_metrics", {"plant_id": "plant_a"}))] * (config.MAX_ITERATIONS + 2)
    out = _run(StubClient(script), api, registry)
    assert out.status == "iteration_cap"
    assert out.iterations == config.MAX_ITERATIONS


def test_tool_budget_returns_an_error_to_the_model_not_a_crash(registry, api) -> None:
    per_turn = 6
    turns = (config.MAX_TOOL_CALLS // per_turn) + 2
    script = [_tool_turn(*[("get_metrics", {"plant_id": "plant_a"})] * per_turn)
              for _ in range(turns)] + [_final("{}")]
    out = _run(StubClient(script), api, registry)
    exhausted = [r for r in out.records
                 if isinstance(r.result, dict)
                 and r.result.get("error") == "tool_call_budget_exhausted"]
    assert exhausted, "budget cap never engaged"
    assert "hint" in exhausted[0].result


# ----------------------------------------------------------------------- truncation
def test_transcript_records_the_truncated_bytes_the_model_saw(registry, api) -> None:
    """Recording the FULL result while sending a truncated one would let a citation
    resolve into data the model never received."""
    client = StubClient([
        _tool_turn(("get_bottleneck_history", {"plant_id": "plant_a", "limit": 700})),
        _final("{}"),
    ])
    out = _run(client, api, registry)
    recorded = out.records[0].result
    sent = json.loads(
        [m for m in client.seen_messages[-1]
         if isinstance(m, dict) and m.get("role") == "tool"][0]["content"]
    )["result"]
    assert recorded == sent


def test_truncate_keeps_valid_json_and_marks_the_omission() -> None:
    big = [{"i": i, "pad": "x" * 200} for i in range(500)]
    out = L.truncate(big, budget=2048)
    assert json.loads(json.dumps(out))            # still valid
    assert any(isinstance(e, dict) and "_truncated" in e for e in out)
    assert len(json.dumps(out)) <= 4096


def test_truncate_leaves_small_results_untouched() -> None:
    small = {"a": 1, "b": [1, 2, 3]}
    assert L.truncate(small) == small


# -------------------------------------------------------------------- error mapping
def test_provider_error_is_classified_not_raised(registry, api) -> None:
    class Boom(Exception):
        pass

    class Failing(StubClient):
        def create(self, **kwargs):
            raise Boom("provider exploded")

    out = _run(Failing([]), api, registry, classify_error=lambda e: "provider_error")
    assert out.status == "failed" and out.error == "provider_error"


def test_a_dropped_connection_is_retried_not_fatal(registry, api, monkeypatch) -> None:
    """One dropped response used to throw away a whole run (provider_unreachable after six
    good tool calls). The same request is retried; a 4xx is not."""
    monkeypatch.setattr(config, "TRANSIENT_BACKOFF_S", 0.0)

    class Flaky(StubClient):
        drops = 1

        def create(self, **kwargs):
            if self.drops:
                self.drops -= 1
                raise ConnectionError("server disconnected")
            return super().create(**kwargs)

    out = _run(Flaky([_final("{}")]), api, registry,
               classify_error=lambda e: "provider_unreachable")
    assert out.error != "provider_unreachable"
    assert out.degradations == ["provider_unreachable->retry_1"]


def test_a_persistent_outage_still_fails_after_bounded_retries(registry, api,
                                                               monkeypatch) -> None:
    monkeypatch.setattr(config, "TRANSIENT_BACKOFF_S", 0.0)
    calls = []

    class Down(StubClient):
        def create(self, **kwargs):
            calls.append(1)
            raise ConnectionError("no route")

    out = _run(Down([]), api, registry, classify_error=lambda e: "provider_unreachable")
    assert out.status == "failed" and out.error == "provider_unreachable"
    assert len(calls) == 1 + config.MAX_TRANSIENT_RETRIES


def test_non_provider_exception_propagates(registry, api) -> None:
    """A bug in our own code must not be silently recorded as a provider failure."""
    class Failing(StubClient):
        def create(self, **kwargs):
            raise ValueError("our bug")

    with pytest.raises(ValueError):
        _run(Failing([]), api, registry, classify_error=lambda e: None)


def test_wallclock_cap_ends_the_run(registry, api) -> None:
    ticks = iter([0.0] + [config.RUN_WALLCLOCK_CAP_S + 1] * 50)
    out = _run(StubClient([_final("{}")]), api, registry, now=lambda: next(ticks))
    assert out.status == "failed" and out.error == "wallclock_cap"


# ---------------------------------------------------------- orchestration seam (ADR-045)
# genai/orchestrator.py runs several run_loop invocations against one shared transcript.
# These four params are what make that safe: call_ids never collide, and each invocation
# can be given a smaller budget than the single-agent path's module-wide constants.
def test_initial_seq_offsets_call_ids_and_final_seq(registry, api) -> None:
    client = StubClient([
        _tool_turn(("get_bottleneck_current", {"plant_id": "plant_a"}),
                   ("get_station_stats", {"plant_id": "plant_a"})),
        _final("{}"),
    ])
    out = _run(client, api, registry, initial_seq=250)
    assert [r.call_id for r in out.records] == ["call_0251", "call_0252"]
    assert [r.seq for r in out.records] == [251, 252]
    assert out.final_seq == 252


def test_initial_seq_defaults_to_zero_and_matches_todays_behaviour(registry, api) -> None:
    client = StubClient([
        _tool_turn(("get_bottleneck_current", {"plant_id": "plant_a"})),
        _final("{}"),
    ])
    out = _run(client, api, registry)
    assert out.records[0].call_id == "call_0001"
    assert out.final_seq == 1


def test_max_tool_calls_override_is_independent_of_the_module_constant(registry, api) -> None:
    """A specialist loop's own cap must not be silently coupled to config.MAX_TOOL_CALLS,
    and must not be coupled to a nonzero initial_seq either -- calls_made and final_seq
    are separate counters on purpose."""
    script = [_tool_turn(("get_metrics", {"plant_id": "plant_a"}))] * 5 + [_final("{}")]
    out = _run(StubClient(script), api, registry, initial_seq=20, max_tool_calls=2)
    exhausted = [r for r in out.records
                 if isinstance(r.result, dict)
                 and r.result.get("error") == "tool_call_budget_exhausted"]
    assert exhausted, "specialist-scoped budget cap never engaged"
    # The third call (calls_made=3 > max_tool_calls=2) is the first to be exhausted,
    # and it still gets a call_id continuing from initial_seq, not from 1.
    assert exhausted[0].call_id == "call_0023"


def test_max_iterations_override_is_independent_of_the_module_constant(registry, api) -> None:
    script = [_tool_turn(("get_metrics", {"plant_id": "plant_a"}))] * 10
    out = _run(StubClient(script), api, registry, max_iterations=3)
    assert out.status == "iteration_cap"
    assert out.iterations == 3
    assert out.iterations < config.MAX_ITERATIONS


def test_wallclock_cap_override_is_independent_of_the_module_constant(registry, api) -> None:
    ticks = iter([0.0, 10.0, 20.0])
    out = _run(StubClient([_final("{}")]), api, registry,
              now=lambda: next(ticks), wallclock_cap_s=5.0)
    assert out.status == "failed" and out.error == "wallclock_cap"


def test_agents_used_is_empty_by_default(registry, api) -> None:
    """Only genai/orchestrator.py populates this -- a single-agent LoopResult must never
    carry it, so genai/triage.py::_persist() can tell the two apart with a plain
    truthiness check."""
    out = _run(StubClient([_final("{}")]), api, registry)
    assert out.agents_used == []


def test_a_records_agent_field_defaults_to_empty(registry, api) -> None:
    """run_loop itself never stamps ToolRecord.agent -- only the orchestrator does, after
    a specialist's run_loop call returns. This keeps loop.py fully agent-agnostic."""
    client = StubClient([
        _tool_turn(("get_bottleneck_current", {"plant_id": "plant_a"})),
        _final("{}"),
    ])
    out = _run(client, api, registry)
    assert out.records[0].agent == ""



def test_the_closing_turn_answers_without_tools_instead_of_timing_out(registry, api) -> None:
    """A live shift_intel run made 24 tool calls and ended with nothing at the wall-clock
    cap. On the last iteration (or near the cap) with evidence in hand, the request goes
    out with NO tools, so the model can only answer."""
    seen: list[dict] = []

    class Recording(StubClient):
        def create(self, **kwargs):
            seen.append(kwargs)
            return super().create(**kwargs)

    client = Recording([_tool_turn(("get_bottleneck_current", {"plant_id": "plant_a"})),
                        _final("{}")])
    out = _run(client, api, registry, max_iterations=2)
    assert "tools" in seen[0] and "tools" not in seen[1] and "tool_choice" not in seen[1]
    assert any("Do not call any more tools" in str(m) for m in seen[1]["messages"])
    assert out.error != "iteration_cap"


def test_no_closing_turn_before_any_evidence(registry, api) -> None:
    seen: list[dict] = []

    class Recording(StubClient):
        def create(self, **kwargs):
            seen.append(kwargs)
            return super().create(**kwargs)

    _run(Recording([_final("{}")]), api, registry, max_iterations=1)
    assert "tools" in seen[0]      # nothing gathered yet: the first turn keeps its tools


def test_a_provider_call_that_never_completes_is_bounded(registry, api, monkeypatch) -> None:
    """The SDK timeout is per READ; OpenRouter trickles whitespace on a slow request, so it
    never fired and two live workers sat 14+ minutes in one call. The loop now enforces a
    TOTAL deadline per call, which is a transient `provider_timeout`: retried, then failed."""
    import threading
    release = threading.Event()

    class Hanging(StubClient):
        def create(self, **kwargs):
            release.wait(5)
            return _final("{}").__class__([])

    monkeypatch.setattr(L.config, "TRANSIENT_BACKOFF_S", 0.01)
    t0 = time.monotonic()
    # the deadline is the RUN's remaining budget, so a 1.5 s cap ends it in seconds
    out = _run(Hanging([]), api, registry, classify_error=lambda e: None, wallclock_cap_s=1.5)
    release.set()
    assert out.status == "failed" and out.error in ("provider_timeout", "wallclock_cap")
    assert time.monotonic() - t0 < 5
