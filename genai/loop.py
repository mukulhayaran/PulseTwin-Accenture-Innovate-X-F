"""The agent loop, provider-shaped but verifier-agnostic.

Separated from `genai/triage.py` (the CLI and persistence) so the loop mechanics can be
tested against a stub client with no credential and no database.

Three details here are load-bearing and easy to get wrong:

  * `call_id` is assigned by US (`call_0001`, ...) and injected into the tool result the
    model reads. The model can only cite ids it was actually shown, so a fabricated id
    fails verification immediately instead of being ambiguous.
  * The transcript records the TRUNCATED bytes -- exactly what the model saw. Recording
    the full result while sending a truncated one would let a citation resolve into data
    the model never received, inverting the whole guarantee. It is a one-line mistake.
  * The two caps degrade differently on purpose. See `genai/config.py`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from genai import config, prompts, tools as T
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


@dataclass
class LoopResult:
    status: str                 # "finished" | "iteration_cap" | "failed"
    content: str                # final model text, empty unless finished
    records: list[ToolRecord] = field(default_factory=list)
    iterations: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str = ""

    @property
    def transcript(self) -> dict[str, Any]:
        """call_id -> the recorded result, as handed to the verifier."""
        return {r.call_id: r.result for r in self.records}


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


def run_loop(
    client: Any,
    api: PulseTwinAPI,
    registry: dict[str, T.ToolSpec],
    *,
    plant_id: str,
    question: str,
    acting_role: str,
    on_progress: Callable[[list[ToolRecord]], None] | None = None,
    classify_error: Callable[[BaseException], str | None] | None = None,
    now: Callable[[], float] = time.monotonic,
) -> LoopResult:
    wire_tools = T.to_wire(registry)
    messages: list[Any] = [
        {"role": "system", "content": prompts.SYSTEM},
        {"role": "user", "content": prompts.USER_TEMPLATE.format(
            plant_id=plant_id, acting_role=acting_role, question=question)},
    ]

    result = LoopResult(status="failed", content="")
    started = now()
    seq = 0
    retried_rate_limit = False

    for iteration in range(config.MAX_ITERATIONS):
        result.iterations = iteration + 1
        if now() - started > config.RUN_WALLCLOCK_CAP_S:
            result.status, result.error = "failed", "wallclock_cap"
            return result

        try:
            resp = client.chat.completions.create(
                model=config.MODEL_ID,
                messages=messages,
                tools=wire_tools,
                tool_choice="auto",
                temperature=config.TEMPERATURE,
                seed=config.SEED,
                response_format=prompts.RESPONSE_FORMAT,
            )
        except BaseException as exc:  # noqa: BLE001 - re-raised unless provider-shaped
            code = classify_error(exc) if classify_error else None
            if code is None:
                raise
            if code == "rate_limited" and not retried_rate_limit:
                retried_rate_limit = True
                time.sleep(5.0)
                continue
            result.status, result.error = "failed", code
            return result

        choice = resp.choices[0]
        usage = getattr(resp, "usage", None)
        if usage is not None:
            result.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            result.completion_tokens += getattr(usage, "completion_tokens", 0) or 0

        messages.append(choice.message)
        calls = getattr(choice.message, "tool_calls", None) or []

        if choice.finish_reason != "tool_calls" or not calls:
            result.status = "finished"
            result.content = choice.message.content or ""
            return result

        for call in calls:
            seq += 1
            call_id = f"call_{seq:04d}"
            t0 = now()

            if seq > config.MAX_TOOL_CALLS:
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
                seq=seq,
                name=call.function.name,
                arguments=args if isinstance(args, dict) else {},
                result=payload,
                duration_ms=(now() - t0) * 1000.0,
            ))

            messages.append({
                "role": "tool",
                "tool_call_id": getattr(call, "id", "") or call_id,
                "content": json.dumps({"call_id": call_id, "result": payload},
                                      default=str),
            })

        if on_progress:
            on_progress(result.records)

    result.status, result.error = "iteration_cap", "iteration_cap"
    return result
