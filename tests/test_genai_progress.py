"""Progress reporting, capability degradation inside the loop, and what must NEVER leak.

Three separable concerns, together because they all ride on `run_loop`'s stub-driven
path and none of them needs a credential:

  1. the phase trail the UI renders is emitted, in order, and starts BEFORE the first
     tool call -- the window that used to show a bare spinner;
  2. a provider that rejects the request shape degrades and still finishes, while a
     provider that rejects the CREDENTIAL fails immediately;
  3. model prose -- including the `reasoning` field `openai/gpt-oss-120b` returns --
     never reaches a ProgressEvent or a heartbeat.

(3) is structural: the heartbeat is assembled from an explicit key allowlist. Structure
that is not tested drifts, so it is tested.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("fastapi")

if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from genai import capabilities as C  # noqa: E402
from genai import loop as L  # noqa: E402
from genai import tools as T  # noqa: E402
from genai import triage as TR  # noqa: E402
from genai.api_client import PulseTwinAPI  # noqa: E402
from genai.api_client import testclient_transport as _tc_transport  # noqa: E402
from serve.main import app  # noqa: E402

PLANT = "plant_a"
MAGIC = "SECRET-CHAIN-OF-THOUGHT-MARKER"

# Verbatim from OpenRouter's nvidia/nemotron-3-ultra-550b-a55b:free on
# 2026-09-21, asked for a tool call with `response_format` also set: a stray
# second brace and no closing one, produced INSTEAD of calling the tool.
BROKEN_JSON = '{\n{\n  "x": "get_bottleneck_current"\n}'


@pytest.fixture(autouse=True)
def _isolated_caps(tmp_path, monkeypatch):
    """Isolate the capability cache per test.

    `remember()` writes BOTH an in-process memo and a file under PULSETWIN_JOBS_DIR, and
    that is deliberate -- each turn is a fresh process, so the file is what stops every
    turn re-paying the rejected requests. It also means a test that degrades leaves a
    profile behind: clearing only the memo let the next test start at L2 and attempt 3
    requests where it expected 5, and it would have written a degraded profile into the
    developer's real temp directory as a side effect of running the suite.
    """
    monkeypatch.setenv("PULSETWIN_JOBS_DIR", str(tmp_path))
    C.reset_cache()
    yield
    C.reset_cache()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def registry():
    return T.build_registry([PLANT])


@pytest.fixture
def api(client):
    return PulseTwinAPI("http://testserver", "leadership", "progress",
                        transport=_tc_transport(client))


def _msg(content="", tool_calls=None, reasoning=None):
    m = SimpleNamespace(content=content, tool_calls=tool_calls, role="assistant")
    if reasoning is not None:
        m.reasoning = reasoning
    return m


def _call(name, args="{}", cid="p1"):
    return SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments=args))


def _resp(message, finish_reason):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


class ScriptedClient:
    """Replays a fixed list of responses; records the kwargs of every request."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self._responses.pop(0)


class RejectingClient:
    """Raises a sentinel for any request matching `reject`, else replays responses."""

    def __init__(self, responses, reject):
        self._responses = list(responses)
        self._reject = reject
        self.attempts = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.attempts += 1
        if self._reject(kwargs):
            raise RuntimeError("provider rejected the request shape")
        return self._responses.pop(0)


# ------------------------------------------------------------------------- phase trail
def test_phase_is_emitted_before_any_tool_runs(registry, api) -> None:
    """The first provider round trip is the longest wait in a run and happens BEFORE any
    tool call. Emitting only after a completed iteration left that window blank."""
    events = []
    client = ScriptedClient([
        _resp(_msg(tool_calls=[_call("get_bottleneck_current",
                                     json.dumps({"plant_id": PLANT}))]), "tool_calls"),
        _resp(_msg(content="{}"), "stop"),
    ])
    L.run_loop(client, api, registry, plant_id=PLANT, question="q",
               acting_role="leadership", on_progress=events.append)

    assert events[0].kind == "model_request"
    assert events[0].phase == T.PHASE_START
    assert events[0].records == []


def test_the_trail_follows_the_tools_it_calls(registry, api) -> None:
    events = []
    client = ScriptedClient([
        _resp(_msg(tool_calls=[
            _call("get_bottleneck_current", json.dumps({"plant_id": PLANT}), "a"),
            _call("get_whatif_stations", json.dumps({"plant_id": PLANT}), "b"),
        ]), "tool_calls"),
        _resp(_msg(content="{}"), "stop"),
    ])
    L.run_loop(client, api, registry, plant_id=PLANT, question="q",
               acting_role="leadership", on_progress=events.append)

    started = [e.phase for e in events if e.kind == "tool_started"]
    assert started == ["Checking production state", "Running simulation"]
    assert all(p in T.PHASE_ORDER for p in started)


def test_a_tool_in_flight_is_announced_before_it_returns(registry, api) -> None:
    """So the panel can say 'Running simulation...' while the call is happening rather
    than only after it lands."""
    events = []
    client = ScriptedClient([
        _resp(_msg(tool_calls=[_call("get_whatif_stations",
                                     json.dumps({"plant_id": PLANT}))]), "tool_calls"),
        _resp(_msg(content="{}"), "stop"),
    ])
    L.run_loop(client, api, registry, plant_id=PLANT, question="q",
               acting_role="leadership", on_progress=events.append)

    starts = [e for e in events if e.kind == "tool_started"]
    assert starts and starts[0].pending_tool == "get_whatif_stations"
    assert starts[0].records == []          # nothing recorded yet -- it is still running


def test_every_recorded_call_carries_its_phase(registry, api) -> None:
    """The DB ledger stores the phase too, so the trail does not flicker backwards when
    the UI switches from the live heartbeat to the persisted ledger."""
    client = ScriptedClient([
        _resp(_msg(tool_calls=[_call("get_station_stats",
                                     json.dumps({"plant_id": PLANT}))]), "tool_calls"),
        _resp(_msg(content="{}"), "stop"),
    ])
    out = L.run_loop(client, api, registry, plant_id=PLANT, question="q",
                     acting_role="leadership")
    assert out.records[0].phase == "Reading station performance"


def test_phase_order_positions_are_monotone() -> None:
    idx = [T.phase_index(p) for p in T.PHASE_ORDER]
    assert idx == sorted(idx) == list(range(len(T.PHASE_ORDER)))


def test_an_unknown_tool_name_does_not_raise_in_the_progress_path(registry) -> None:
    """A progress label must never be the thing that ends a run."""
    assert T.phase_for(registry, "no_such_tool") == T.PHASE_START


# ------------------------------------------------------- capability degradation in situ
def test_a_400_degrades_and_the_run_still_finishes(registry, api) -> None:
    client = RejectingClient(
        [_resp(_msg(content='{"summary":"s"}'), "stop")] * 2,   # + the evidence-first re-ask
        reject=lambda kw: (kw.get("response_format") or {}).get("type") == "json_schema",
    )
    out = L.run_loop(client, api, registry, plant_id=PLANT, question="q",
                     acting_role="leadership",
                     classify_error=lambda e: "provider_status_400")

    assert out.status == "finished"
    assert out.capability_profile == "L2"        # two rungs: strict, then json_schema
    assert out.degradations == ["provider_status_400->L1", "provider_status_400->L2"]


def test_a_401_does_not_degrade(registry, api) -> None:
    """A bad credential must fail on the first attempt, not walk the ladder and then fail
    anyway with a misleading profile recorded."""
    client = RejectingClient([], reject=lambda kw: True)
    out = L.run_loop(client, api, registry, plant_id=PLANT, question="q",
                     acting_role="leadership",
                     classify_error=lambda e: "provider_status_401")

    assert out.status == "failed"
    assert out.error == "provider_status_401"
    assert client.attempts == 1
    assert out.degradations == []


def test_degradation_is_bounded_by_the_ladder(registry, api) -> None:
    """A provider that rejects everything costs exactly len(LADDER) attempts, once, for
    the whole process -- not once per iteration."""
    client = RejectingClient([], reject=lambda kw: True)
    out = L.run_loop(client, api, registry, plant_id=PLANT, question="q",
                     acting_role="leadership",
                     classify_error=lambda e: "provider_status_400")

    assert out.status == "failed"
    assert client.attempts == len(C.LADDER)
    assert out.capability_profile == "L4"


def test_a_bug_in_our_own_code_still_propagates(registry, api) -> None:
    """classify_error returning None means "not provider-shaped" -- the loop must re-raise
    rather than record our bug as a provider failure."""
    client = RejectingClient([], reject=lambda kw: True)
    with pytest.raises(RuntimeError):
        L.run_loop(client, api, registry, plant_id=PLANT, question="q",
                   acting_role="leadership", classify_error=lambda e: None)


# ------------------------------------------------------- chain-of-thought containment
def test_progress_events_never_carry_model_prose(registry, api) -> None:
    events = []
    client = ScriptedClient([
        _resp(_msg(content=MAGIC,
                   reasoning=MAGIC,
                   tool_calls=[_call("get_bottleneck_current",
                                     json.dumps({"plant_id": PLANT}))]), "tool_calls"),
        _resp(_msg(content=MAGIC, reasoning=MAGIC), "stop"),
    ])
    L.run_loop(client, api, registry, plant_id=PLANT, question="q",
               acting_role="leadership", on_progress=events.append)

    for e in events:
        blob = json.dumps({"kind": e.kind, "phase": e.phase,
                           "pending_tool": e.pending_tool,
                           "records": [r.name for r in e.records]})
        assert MAGIC not in blob


def test_the_heartbeat_filters_to_an_allowlist(tmp_path, monkeypatch) -> None:
    """The heartbeat is written to disk and rendered in a browser. Adding a field at a
    call site must not be able to publish model text."""
    monkeypatch.setenv("PULSETWIN_JOBS_DIR", str(tmp_path))
    TR._heartbeat("run_alw", {
        "run_id": "run_alw", "status": "running", "phase": "Running simulation",
        "content": MAGIC, "reasoning": MAGIC, "messages": [MAGIC],
        "tools": [{"call_id": "call_0001", "tool_name": "get_metrics",
                   "phase": "Examining risk signals", "status": "ok",
                   "duration_ms": 1.0, "reasoning": MAGIC}],
    })
    written = (tmp_path / "pulsetwin_triage_runs" / "run_alw.json").read_text("utf-8")
    assert MAGIC not in written
    hb = json.loads(written)
    assert hb["v"] == TR.HEARTBEAT_VERSION
    assert hb["phase"] == "Running simulation"
    assert hb["tools"][0]["tool_name"] == "get_metrics"
    assert "reasoning" not in hb["tools"][0]


def test_a_stale_v1_heartbeat_is_dropped_not_rendered(tmp_path, monkeypatch) -> None:
    """A file left by a previous session has no `phase`; rendering it would show an empty
    trail on a run that is working fine."""
    from serve.routers import triage as R

    monkeypatch.setenv("PULSETWIN_JOBS_DIR", str(tmp_path))
    d = tmp_path / "pulsetwin_triage_runs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "old.json").write_text(json.dumps({"run_id": "old", "tools": []}), "utf-8")
    assert R._read_heartbeat("old") is None

    (d / "new.json").write_text(
        json.dumps({"v": R.HEARTBEAT_VERSION, "phase": "Composing the finding"}), "utf-8")
    assert R._read_heartbeat("new")["phase"] == "Composing the finding"


def test_serve_and_genai_agree_on_the_phase_trail() -> None:
    """serve/ mirrors PHASE_ORDER by hand (the import wall). A UI driven by one and a
    worker emitting the other would render a trail whose steps never light up."""
    from serve.routers import triage as R

    assert tuple(R.PHASE_ORDER) == T.PHASE_ORDER
    assert R.HEARTBEAT_VERSION == TR.HEARTBEAT_VERSION


def test_a_200_with_no_tool_calls_degrades_and_the_rerun_uses_tools(registry, api) -> None:
    """The OpenRouter signature: HTTP 200, no 400 anywhere, and no tool call.

    The stub accepts every request. The FIRST reply carries `response_format` and comes
    back with no tool call and broken JSON -- exactly what the live endpoint did. The
    loop must notice, drop `response_format`, and re-ask the SAME question rather than
    resuming the poisoned conversation, which is why the second request's `messages` is
    the same length as the first's.
    """
    client = ScriptedClient([
        _resp(_msg(content=BROKEN_JSON), "stop"),
        _resp(_msg(tool_calls=[_call("get_bottleneck_current",
                                     '{"plant_id": "%s"}' % PLANT)]), "tool_calls"),
        _resp(_msg(content='{"summary":"s","claims":[]}'), "stop"),
    ])
    out = L.run_loop(client, api, registry, plant_id=PLANT,
                     question="q", acting_role="leadership",
                     classify_error=lambda e: None)

    assert out.status == "finished"
    assert len(out.records) == 1, "the re-ask must actually call a tool"
    assert out.capability_profile == "L4"
    assert out.degradations == ["response_format_suppressed_tool_calls->L4"]
    assert "response_format" in client.requests[0]
    assert "response_format" not in client.requests[1]
    assert len(client.requests[1]["messages"]) == len(client.requests[0]["messages"])


def test_a_200_with_no_tool_calls_degrades_even_when_the_json_is_valid(registry, api) -> None:
    """The signature used to need broken JSON as well. Measured live 2026-09-26, three
    runs: at a rung sending `response_format` this provider ALSO answers with valid JSON
    and no tool call -- invented figures citing call_ids that never existed, rejected
    whole by the gate. So a toolless first answer on such a rung degrades regardless."""
    client = ScriptedClient([
        _resp(_msg(content='{"summary":"s","claims":[]}'), "stop"),
        _resp(_msg(tool_calls=[_call("get_bottleneck_current",
                                     '{"plant_id": "%s"}' % PLANT)]), "tool_calls"),
        _resp(_msg(content='{"summary":"s","claims":[]}'), "stop"),
    ])
    out = L.run_loop(client, api, registry, plant_id=PLANT, question="q",
                     acting_role="leadership", classify_error=lambda e: None)

    assert out.status == "finished"
    assert out.degradations == ["response_format_suppressed_tool_calls->L4"]
    assert len(out.records) == 1, "the re-ask at the no-format rung must call a tool"
    assert "response_format" not in client.requests[1]


def test_a_toolless_answer_at_a_no_format_rung_gets_one_evidence_re_ask(
        registry, api, monkeypatch) -> None:
    """Below the format rungs there is nothing left to drop, so the model is told to
    gather evidence first -- once, at the same rung."""
    from genai import capabilities as C
    monkeypatch.setattr(C, "initial", lambda model_id: C.LADDER[-1])
    answer = _resp(_msg(content='{"summary":"s","claims":[]}'), "stop")
    client = ScriptedClient([answer, answer])
    out = L.run_loop(client, api, registry, plant_id=PLANT, question="q",
                     acting_role="leadership", classify_error=lambda e: None)

    assert out.status == "finished" and out.degradations == []
    assert len(client.requests) == 2
    assert {"role": "user", "content": L.EVIDENCE_FIRST_INSTRUCTION} in client.requests[1]["messages"]
