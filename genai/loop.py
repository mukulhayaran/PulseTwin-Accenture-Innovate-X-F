"""The agent loop, provider-shaped but verifier-agnostic.

Separated from `genai/triage.py` (the CLI and persistence) so the loop mechanics can be
tested against a stub client with no credential and no database.

Four details here are load-bearing and easy to get wrong:

  * `call_id` is assigned by US (`call_0001`, ...) and injected into the tool result the
    model reads. The model can only cite ids it was actually shown, so a fabricated id
    fails verification immediately instead of being ambiguous. Ids restart at 0001 every
    run, which is exactly why citations MUST NOT cross conversation turns -- see ADR-039
    and `genai/prompts.py::THREAD_CONTEXT_TEMPLATE`.
  * The transcript records the TRUNCATED bytes -- exactly what the model saw. Recording
    the full result while sending a truncated one would let a citation resolve into data
    the model never received, inverting the whole guarantee. It is a one-line mistake.
  * The two caps degrade differently on purpose. See `genai/config.py`.
  * Progress events carry ONLY our own phase labels and tool names. Model prose --
    including the `reasoning` field `openai/gpt-oss-120b` returns -- never enters a
    ProgressEvent, because the heartbeat built from it is rendered in a browser and
    stored on disk. `tests/test_genai_progress.py` asserts this rather than trusting it.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from genai import capabilities, config, prompts, tools as T
from genai.api_client import PulseTwinAPI


@dataclass
class ToolRecord:
    call_id: str
    provider_call_id: str
    seq: int
    name: str
    arguments: dict[str, Any]
    result: Any                 # the truncated payload the model received
    duration_ms: float
    phase: str = ""             # human-readable activity label, from ToolSpec.phase
    # Which specialist produced this call, for an orchestrated run (genai/orchestrator.py,
    # ADR-045). Never set by run_loop itself -- stamped by the orchestrator after a
    # specialist's run_loop call returns, so this module stays fully agent-agnostic and a
    # single-agent run's records are indistinguishable from before this field existed.
    agent: str = ""


@dataclass(frozen=True)
class ProgressEvent:
    """One tick of the activity surface.

    Emitted per EVENT rather than per iteration, because the single longest wait in a run
    is the first provider round trip -- before any tool has been called. Emitting only
    after a completed iteration means that window produces nothing to render, which is
    the spinner this replaced.
    """

    kind: str                   # model_request | tool_started | tool_finished | loop_end
    iteration: int
    phase: str
    records: list[ToolRecord]
    pending_tool: str = ""      # tool name in flight, for kind == "tool_started"


@dataclass
class LoopResult:
    status: str                 # "finished" | "iteration_cap" | "failed"
    content: str                # final model text, empty unless finished
    records: list[ToolRecord] = field(default_factory=list)
    iterations: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str = ""
    # The call_id counter this invocation reached, for a caller that runs several loops
    # against one shared transcript (genai/orchestrator.py). The NEXT invocation's
    # initial_seq should be this value, so call_ids stay globally unique across the whole
    # orchestrated run without genai/verifier.py needing any concept of "agent" at all.
    final_seq: int = 0
    # Per-specialist status snapshots, set ONLY by genai/orchestrator.py on the LoopResult
    # it returns for an orchestrated run. Empty for every single-agent run -- this is what
    # lets genai/triage.py::_persist() detect "was this orchestrated" with a plain
    # truthiness check instead of a new result type or isinstance check.
    agents_used: list[dict[str, Any]] = field(default_factory=list)
    # The capability rung the run settled on, and how it got there. Persisted and shown:
    # a degraded run must be visibly degraded (genai/capabilities.py).
    capability_profile: str = ""
    degradations: list[str] = field(default_factory=list)
    # What the PROVIDER says this run cost, summed over round trips, or None if it does
    # not report one. OpenRouter returns `usage.cost`; Groq does not. Preferred over the
    # local rate card in `genai/store.py`, which is per-model and goes stale on a swap
    # (contract #2, CLAUDE.md §3: a dollar figure carries a source, and "the provider
    # billed this" is a better source than "a rate card read once for another model").
    provider_cost_usd: float | None = None

    @property
    def transcript(self) -> dict[str, Any]:
        """call_id -> the recorded result, as handed to the verifier."""
        return {r.call_id: r.result for r in self.records}


CLOSING_INSTRUCTION = (
    "Your time budget is nearly spent. Do not call any more tools. Write your final "
    "finding now, in the required output shape, from the evidence already gathered above; "
    "put anything you could not establish in `unanswered`.")

class ProviderDeadline(TimeoutError):
    """A provider call that did not COMPLETE in time. The SDK's timeout is per read and
    OpenRouter keeps a slow request alive with whitespace, so it never fires: measured
    2026-09-26, two workers sat 14+ minutes in one call with the 300 s cap unchecked."""


def _create_within(client: Any, kwargs: dict[str, Any], deadline_s: float) -> Any:
    """`chat.completions.create` with a TOTAL deadline. The abandoned call finishes (or
    not) on a daemon thread; the process exits when the run does, so nothing leaks."""
    box: dict[str, Any] = {}

    def call() -> None:
        try:
            box["resp"] = client.chat.completions.create(**kwargs)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            box["exc"] = exc

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    worker.join(deadline_s)
    if worker.is_alive():
        raise ProviderDeadline(f"no complete response within {deadline_s:.0f}s")
    if "exc" in box:
        raise box["exc"]
    return box["resp"]


# Sent once when a model answers having called NO tool at all. Measured live (OpenRouter
# Nemotron, 2026-09-26): a shift_intel run returned valid JSON on turn 0 with 12 claims
# citing `call_0001` -- a call that never happened -- and invented every figure. The gate
# rejected all 12, so the run delivered nothing. One re-ask is cheaper than that.
EVIDENCE_FIRST_INSTRUCTION = (
    "You answered without calling any tool, so none of your figures has evidence behind "
    "it and every claim would be rejected. Call the tools you need first, then answer; "
    "cite only call_ids that appear in tool results above.")


def truncate(value: Any, budget: int = config.MAX_TOOL_RESULT_BYTES) -> Any:
    """Shrink a tool result to fit, without ever producing invalid JSON.

    Long arrays keep their head and tail and gain a `_truncated` marker; cutting the
    serialized string instead would hand the model a broken document.
    """
    if len(json.dumps(value, default=str)) <= budget:
        return value

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out[k] = truncate(v, budget // max(1, len(value)))
        if len(json.dumps(out, default=str)) <= budget:
            return out
        value = out

    if isinstance(value, list) and len(value) > 4:
        keep = max(2, budget // max(1, len(json.dumps(value[0], default=str)) + 1) // 2)
        keep = min(keep, len(value) // 2)
        head, tail = value[:keep], value[-keep:]
        return head + [{"_truncated": {"omitted": len(value) - 2 * keep,
                                       "of": len(value)}}] + tail

    text = json.dumps(value, default=str)
    return {"_truncated_value": text[: budget - 64] + "...", "_original_bytes": len(text)}


def build_messages(
    *,
    plant_id: str,
    question: str,
    acting_role: str,
    thread_context: str = "",
    intent_guidance: str = "",
) -> list[Any]:
    """The opening messages array.

    Deliberate property, and worth preserving: with `thread_context` and
    `intent_guidance` both empty this is BYTE-IDENTICAL to what the one-shot tier sent
    before threads existed. Turn 0 of every conversation is therefore the code path the
    existing loop tests already cover, and a regression there is a test failure rather
    than a demo surprise.
    """
    user = prompts.USER_TEMPLATE.format(
        plant_id=plant_id, acting_role=acting_role, question=question)
    messages: list[Any] = [{"role": "system", "content": prompts.SYSTEM}]
    if thread_context:
        messages.append({"role": "user", "content": thread_context})
    if intent_guidance:
        user = f"{user}\n\n{intent_guidance}"
    messages.append({"role": "user", "content": user})
    return messages


def run_loop(
    client: Any,
    api: PulseTwinAPI,
    registry: dict[str, T.ToolSpec],
    *,
    plant_id: str,
    question: str,
    acting_role: str,
    thread_context: str = "",
    intent_guidance: str = "",
    on_progress: Callable[[ProgressEvent], None] | None = None,
    classify_error: Callable[[BaseException], str | None] | None = None,
    retry_after: Callable[[BaseException], float | None] | None = None,
    now: Callable[[], float] = time.monotonic,
    initial_seq: int = 0,
    max_iterations: int = config.MAX_ITERATIONS,
    max_tool_calls: int = config.MAX_TOOL_CALLS,
    wallclock_cap_s: float = config.RUN_WALLCLOCK_CAP_S,
) -> LoopResult:
    """Run one tool-calling loop.

    The last four parameters all default to today's module constants, so the original
    single-agent call site (and every test written against it) is byte-identical whether
    or not this signature exists. They exist so genai/orchestrator.py (ADR-045) can run
    SEVERAL bounded loops against one shared transcript: `initial_seq` is where THIS
    invocation's call_id numbering starts (threaded from a previous invocation's
    `LoopResult.final_seq`), and the three caps let a specialist loop be given a smaller
    budget than the single-agent path's, independent of the module-wide constants.
    """
    wire_tools = T.to_wire(registry)
    messages = build_messages(
        plant_id=plant_id, question=question, acting_role=acting_role,
        thread_context=thread_context, intent_guidance=intent_guidance,
    )

    result = LoopResult(status="failed", content="")
    result.final_seq = initial_seq
    caps = capabilities.initial(config.MODEL_ID)
    result.capability_profile = caps.name
    started = now()
    calls_made = 0
    rate_limit_retries = 0
    tool_call_retries = 0
    transient_retries = 0
    nudged = False
    evidence_nudged = False

    def emit(kind: str, iteration: int, phase: str, pending: str = "") -> None:
        if on_progress:
            on_progress(ProgressEvent(kind=kind, iteration=iteration, phase=phase,
                                      records=list(result.records), pending_tool=pending))

    for iteration in range(max_iterations):
        result.iterations = iteration + 1
        if now() - started > wallclock_cap_s:
            result.status, result.error = "failed", "wallclock_cap"
            return result

        # The closing turn. A slow reasoning model (~12 s a turn) used to reach the
        # wall-clock cap mid-investigation and end with NOTHING: a live shift_intel run
        # made 24 good tool calls and returned no finding. With evidence in hand and the
        # budget nearly spent, one request goes out WITHOUT tools, so the model can only
        # answer; the answer still meets the same verifier as any other.
        closing = bool(result.records) and (
            iteration == max_iterations - 1
            or now() - started > wallclock_cap_s - min(config.FINALIZE_RESERVE_S,
                                                       wallclock_cap_s / 4))
        if closing and not nudged:
            messages.append({"role": "user", "content": CLOSING_INSTRUCTION})
            nudged = True

        # After the first round trip the model has evidence in hand, so the honest label
        # for "waiting on the provider" changes from starting to composing.
        emit("model_request", iteration + 1,
             T.PHASE_START if iteration == 0 else T.PHASE_COMPOSE)

        # Capability negotiation. Bounded by construction: `degrade` is strictly monotone
        # and returns None at the bottom rung, and `caps` is loop-scoped, so the whole
        # PROCESS pays at most len(LADDER)-1 extra requests -- not once per iteration.
        while True:
            try:
                kwargs = capabilities.request_kwargs(
                    caps,
                    model=config.MODEL_ID,
                    messages=messages,
                    tools=wire_tools,
                    temperature=config.TEMPERATURE,
                    response_format=prompts.RESPONSE_FORMAT,
                )
                if closing:
                    # Omitted, not emptied: some OpenAI-compatible endpoints reject
                    # `tools: []` as malformed.
                    kwargs.pop("tools", None)
                    kwargs.pop("tool_choice", None)
                # Bounded by the RUN's remaining budget: a reasoning model can need more
                # than the SDK's 90 s read timeout for one large call, but no single call
                # may outlive the cap the whole run is held to.
                resp = _create_within(client, kwargs,
                                      max(1.0, wallclock_cap_s - (now() - started)))
            except BaseException as exc:  # noqa: BLE001 - re-raised unless provider-shaped
                code = ("provider_timeout" if isinstance(exc, ProviderDeadline)
                        else classify_error(exc) if classify_error else None)
                if code is None:
                    # A bug in OUR code must never be recorded as a provider failure.
                    raise
                if (code == "rate_limited"
                        and rate_limit_retries < config.MAX_RATE_LIMIT_RETRIES):
                    rate_limit_retries += 1
                    # The provider knows when its own budget resets; a local guess is
                    # strictly worse. Capped so a long hint cannot park a detached
                    # worker, and the wallclock check at the top of the next iteration
                    # ends the run if the wait consumed the budget.
                    hint = retry_after(exc) if retry_after else None
                    wait = min(
                        hint if hint and hint > 0 else config.RATE_LIMIT_BACKOFF_S,
                        config.MAX_RATE_LIMIT_WAIT_S,
                    )
                    remaining = wallclock_cap_s - (now() - started)
                    if wait >= remaining:
                        result.status, result.error = "failed", "rate_limited_wallclock"
                        return result
                    result.degradations.append(f"rate_limited->wait_{wait:.0f}s")
                    time.sleep(wait)
                    continue
                if (code in config.TRANSIENT_ERRORS
                        and transient_retries < config.MAX_TRANSIENT_RETRIES):
                    # The network, not the request: the same call again, not a rung.
                    transient_retries += 1
                    wait = config.TRANSIENT_BACKOFF_S * transient_retries
                    if wait >= wallclock_cap_s - (now() - started):
                        result.status, result.error = "failed", code
                        return result
                    result.degradations.append(f"{code}->retry_{transient_retries}")
                    time.sleep(wait)
                    continue
                if (code == "model_tool_call_invalid"
                        and tool_call_retries < config.MAX_TOOL_CALL_RETRIES):
                    # The MODEL emitted an unparseable tool call. Degrading the request
                    # cannot fix that, so retry the same one instead of walking a rung.
                    tool_call_retries += 1
                    result.degradations.append(
                        f"model_tool_call_invalid->retry_{tool_call_retries}")
                    continue
                if capabilities.is_capability_rejection(code):
                    # The provider's own message, passed so the ladder can skip rungs
                    # that would fail for the reason it just stated.
                    nxt = capabilities.degrade(caps, str(exc))
                    if nxt is not None:
                        caps = nxt
                        capabilities.remember(config.MODEL_ID, caps)
                        result.capability_profile = caps.name
                        result.degradations.append(f"{code}->{caps.name}")
                        continue
                result.status, result.error = "failed", code
                return result

            # A SOFT capability rejection: HTTP 200, but `response_format` silently
            # disabled tool calling (see capabilities.suppresses_tool_calls). Checked on
            # the FIRST round trip only, and before `messages` has been touched -- so
            # falling a rung re-asks the very same question rather than resuming a
            # poisoned conversation.
            #
            # BOTH halves of the signature are required. "No tool call" alone is a
            # legitimate turn: a model may answer straight out, and the verifier -- not
            # the ladder -- is what rejects an answer carrying no evidence. What is NOT
            # legitimate is no tool call AND output that is not even a JSON object, when
            # a JSON object is the one thing `response_format` was sent to guarantee.
            # That pair is the measured OpenRouter failure and nothing else looks like
            # it; tightening to it is what keeps a model that simply answers early from
            # being charged an extra round trip.
            #
            # Skipped once anything else has already degraded this run. This is an
            # INFERENCE from silence, and it is only sound while the provider has said
            # nothing: an endpoint that answered a 400 has already named the rung it
            # wants, and second-guessing that from the shape of its next reply would
            # spend a round trip to reach a rung it had told us about.
            # Measured 2026-09-26, three runs: at a rung sending `response_format` this
            # provider also answers with VALID JSON and no tool call -- invented figures,
            # citing call_ids that never existed. So on such a rung the JSON half no longer
            # excuses it; on a no-format rung a toolless answer gets the evidence re-ask.
            _m = resp.choices[0].message
            if (iteration == 0 and not result.degradations
                    and not (getattr(_m, "tool_calls", None) or [])
                    and max_tool_calls > 0 and not closing):
                nxt = capabilities.suppresses_tool_calls(caps)
                if nxt is not None:
                    caps = nxt
                    capabilities.remember(config.MODEL_ID, caps)
                    result.capability_profile = caps.name
                    result.degradations.append(
                        f"response_format_suppressed_tool_calls->{caps.name}")
                    continue
            break

        choice = resp.choices[0]
        usage = getattr(resp, "usage", None)
        if usage is not None:
            result.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            result.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            # `cost` is an OpenRouter extension, absent on Groq. A reported 0.0 is a real
            # answer ("this model is free"), so test for None rather than truthiness.
            cost = getattr(usage, "cost", None)
            if cost is not None:
                result.provider_cost_usd = (result.provider_cost_usd or 0.0) + float(cost)

        messages.append(choice.message)
        calls = getattr(choice.message, "tool_calls", None) or []

        if choice.finish_reason != "tool_calls" or not calls:
            if (not result.records and not evidence_nudged and max_tool_calls > 0
                    and iteration < max_iterations - 1):
                messages.append({"role": "user", "content": EVIDENCE_FIRST_INSTRUCTION})
                evidence_nudged = True
                continue
            result.status = "finished"
            result.content = choice.message.content or ""
            emit("loop_end", iteration + 1, T.PHASE_COMPOSE)
            return result

        for call in calls:
            result.final_seq += 1
            calls_made += 1
            call_id = f"call_{result.final_seq:04d}"
            phase = T.phase_for(registry, call.function.name)
            emit("tool_started", iteration + 1, phase, pending=call.function.name)
            t0 = now()

            if calls_made > max_tool_calls:
                # Budget exhausted is reported TO THE MODEL so it can still write a
                # finding from evidence it already has. Only the iteration cap is fatal.
                payload: Any = {
                    "error": "tool_call_budget_exhausted",
                    "hint": "Write your finding now from the evidence you already have.",
                }
                args: dict[str, Any] = {}
            else:
                raw_args = call.function.arguments or "{}"
                payload = T.dispatch(registry, api, call.function.name, raw_args)
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}

            payload = truncate(payload)
            result.records.append(ToolRecord(
                call_id=call_id,
                provider_call_id=getattr(call, "id", "") or "",
                seq=result.final_seq,
                name=call.function.name,
                arguments=args if isinstance(args, dict) else {},
                result=payload,
                duration_ms=(now() - t0) * 1000.0,
                phase=phase,
            ))

            messages.append({
                "role": "tool",
                "tool_call_id": getattr(call, "id", "") or call_id,
                "content": json.dumps({"call_id": call_id, "result": payload},
                                      default=str),
            })
            emit("tool_finished", iteration + 1, phase)

    result.status, result.error = "iteration_cap", "iteration_cap"
    emit("loop_end", result.iterations, T.PHASE_COMPOSE)
    return result
