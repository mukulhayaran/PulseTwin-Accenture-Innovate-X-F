"""Entry point: run one triage, verify it, persist it.

    python -m genai.triage --plant plant_a --question "..." [--run-id X] [--mode auto]
    python -m genai.triage --plant plant_a --intent handover --mode deterministic

Spawned detached by `serve/routers/triage.py`, exactly as `mlops.retrainer` is spawned by
`serve/routers/admin.py` (ADR-036: process management, not compute). Also runnable by
hand, which is how the live smoke test works.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from genai import client as provider
from genai import config, intents, loop as L, offline_triage, prompts, store, tools as T
from genai import verifier as V
from genai.api_client import PulseTwinAPI
from genai.finding import ParseError, parse_finding

DEFAULT_API_BASE = os.environ.get("PULSETWIN_API_BASE", "http://127.0.0.1:7860")


def _jobs_dir() -> Path:
    base = Path(os.environ.get("PULSETWIN_JOBS_DIR", tempfile.gettempdir()))
    d = base / "pulsetwin_triage_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# The heartbeat document version. Bumped from an implicit v1 when phases were added: a
# stale v1 file left by a previous session must be DROPPED by the reader, not parsed as
# v2 and rendered as a blank trail.
HEARTBEAT_VERSION = 2

# Everything a heartbeat may contain. An allowlist rather than a dump of whatever the
# caller passed, because `openai/gpt-oss-120b` returns a `reasoning` field on the
# assistant message and this file is written to disk and rendered in a browser. Model
# prose -- reasoning or content -- must never reach it.
# `tests/test_genai_progress.py` asserts this rather than trusting the convention.
_HEARTBEAT_KEYS = frozenset({
    "v", "run_id", "thread_id", "turn_index", "status", "mode", "intent",
    "phase", "phase_index", "iteration", "max_iterations", "elapsed_s",
    "tools", "done", "claims_verified", "claims_total", "reject_reason",
    "tool_calls", "capability_profile", "error",
})

_TOOL_KEYS = frozenset({"call_id", "tool_name", "phase", "status", "duration_ms"})


def _heartbeat(run_id: str, payload: dict[str, Any]) -> None:
    """Live progress only. The ops DB is authoritative for the verdict.

    RetrainingConsole.tsx's own comment records the cost of the other choice: the
    retrain compare() verdict lives ONLY in an ephemeral job file and is lost on restart.

    Filtered through `_HEARTBEAT_KEYS` on the way out, so adding a field to a progress
    call site cannot accidentally publish model text.
    """
    safe = {k: v for k, v in payload.items() if k in _HEARTBEAT_KEYS}
    safe["v"] = HEARTBEAT_VERSION
    if isinstance(safe.get("tools"), list):
        safe["tools"] = [
            {k: v for k, v in t.items() if k in _TOOL_KEYS}
            for t in safe["tools"] if isinstance(t, dict)
        ]
    try:
        (_jobs_dir() / f"{run_id}.json").write_text(
            json.dumps(safe, default=str), encoding="utf-8")
    except OSError:
        pass  # a heartbeat failure must never end a run


def _tool_row(record: Any, status: str = "ok") -> dict[str, Any]:
    return {
        "call_id": getattr(record, "call_id", ""),
        "tool_name": getattr(record, "name", ""),
        "phase": getattr(record, "phase", "") or "",
        "status": status,
        "duration_ms": getattr(record, "duration_ms", None),
    }


def _plant_ids(api: PulseTwinAPI) -> list[str]:
    status, payload = api.get("/api/config/plants")
    if status != 200 or not isinstance(payload, list):
        return []
    return [p["plant_id"] for p in payload if isinstance(p, dict) and "plant_id" in p]


def _station_ids(api: PulseTwinAPI, plant_id: str) -> set[int]:
    status, payload = api.get("/api/config/stations", {"plant_id": plant_id})
    if status != 200 or not isinstance(payload, dict):
        return set()
    return {s["station_id"] for s in payload.get("stations", [])
            if isinstance(s, dict) and "station_id" in s}


def run(
    *, plant_id: str, question: str, run_id: str, api_base: str,
    acting_role: str, requested_by: str, mode: str = "auto",
    intent: str = "ask", station_id: int | None = None,
    thread_id: str | None = None, turn_index: int = 0,
) -> dict[str, Any]:
    started = time.monotonic()
    api = PulseTwinAPI(api_base, acting_role, run_id)
    con, _backend = db_handle()

    plants = _plant_ids(api)
    if not plants:
        store.finish_run(con, run_id, status="failed", finding=None, finding_raw=None,
                         verification=None, claims_total=0, claims_verified=0,
                         iterations=0, tool_calls=0, prompt_tokens=0,
                         completion_tokens=0, latency_s=time.monotonic() - started,
                         error="api_unreachable")
        return {"run_id": run_id, "status": "failed", "error": "api_unreachable"}

    registry = T.build_registry(plants)
    use_agent = mode == "agent" or (mode == "auto" and provider.credential_present())
    mode_label = "agent" if use_agent else "deterministic"

    # Earlier turns of this conversation, and the values they established. Loaded from
    # the DATABASE, never from the request: a client that could seed `carried_values`
    # could disarm the verifier's uncited-number sweep by asserting a figure (ADR-039).
    context = (
        store.load_thread_context(con, thread_id, before_turn=turn_index)
        if thread_id else store.ThreadContext()
    )
    carried = set(context.carried_values)

    task = intents.expand(intent, plant_id=plant_id, question=question,
                          station_id=station_id)

    store.mark_running(
        con, run_id,
        config.PROVIDER if use_agent else None,
        config.MODEL_ID if use_agent else None,
    )

    def beat(**extra: Any) -> None:
        _heartbeat(run_id, {
            "run_id": run_id, "thread_id": thread_id, "turn_index": turn_index,
            "status": "running", "mode": mode_label, "intent": intent,
            "max_iterations": config.MAX_ITERATIONS, **extra,
        })

    beat(phase=T.PHASE_START, phase_index=T.phase_index(T.PHASE_START),
         iteration=0, elapsed_s=0.0, tools=[])

    raw_finding = None
    if use_agent:
        def on_progress(event: Any) -> None:
            rows = [_tool_row(r) for r in event.records]
            if event.kind == "tool_started" and event.pending_tool:
                rows.append({"call_id": "", "tool_name": event.pending_tool,
                             "phase": event.phase, "status": "running",
                             "duration_ms": None})
            beat(phase=event.phase, phase_index=T.phase_index(event.phase),
                 iteration=event.iteration, elapsed_s=time.monotonic() - started,
                 tools=rows)

        result = L.run_loop(
            provider.build_client(), api, registry,
            plant_id=plant_id, question=task, acting_role=acting_role,
            thread_context=prompts.render_thread_context(context.turns),
            intent_guidance=intents.guidance(intent),
            classify_error=provider.classify_error,
            retry_after=provider.retry_after_s,
            on_progress=on_progress,
        )
        if result.status == "finished":
            try:
                raw_finding = parse_finding(result.content, question=question)
            except ParseError as exc:
                ver, served = V.reject_unparseable(question, str(exc))
                # Keep what the model actually wrote. Without it `finding_raw` is NULL
                # and the row says only "unparseable" -- which cannot distinguish a
                # model that invented a schema from one that was cut off mid-sentence,
                # and those want opposite fixes. Never served: the triage router drops
                # `finding_raw` from every response, so this stays operator-only.
                return _persist(con, run_id, result, ver, served, None, started,
                                thread_id=thread_id, carried=carried,
                                raw_text=result.content)
        else:
            ver, served = V.reject_unparseable(question, result.error or result.status)
            ver.reject_reason = result.error or result.status
            return _persist(con, run_id, result, ver, served, None, started,
                            thread_id=thread_id, carried=carried)
    else:
        result, raw_finding = offline_triage.run_plan(
            intents.plan(intent), registry, api,
            plant_id=plant_id, question=task, station_id=station_id)
        beat(phase=T.PHASE_COMPOSE, phase_index=T.phase_index(T.PHASE_COMPOSE),
             iteration=1, elapsed_s=time.monotonic() - started,
             tools=[_tool_row(r) for r in result.records])

    # The verification step is ~0ms, but it is the most important sentence in the demo.
    # Showing it is the difference between "the AI answered" and "the AI answered and
    # every number was re-checked".
    beat(phase=T.PHASE_VERIFY, phase_index=T.phase_index(T.PHASE_VERIFY),
         iteration=result.iterations, elapsed_s=time.monotonic() - started,
         tools=[_tool_row(r) for r in result.records])

    ver, served = V.verify(raw_finding, result.transcript,
                           station_ids=_station_ids(api, plant_id),
                           carried_values=carried)
    return _persist(con, run_id, result, ver, served, raw_finding, started,
                    thread_id=thread_id, carried=carried)


def _persist(con, run_id, result, ver, served, raw_finding, started, *,
             thread_id: str | None = None, carried: set | None = None,
             raw_text: str = "") -> dict[str, Any]:
    store.record_tool_calls(con, run_id, result.records)
    # Persisted so `python -m genai.verify_cli --run-id X` reproduces this turn's verdict
    # byte-identically. Without it a threaded turn would re-verify differently from how
    # it was served, which would make the audit claim false exactly where it matters.
    store.record_carried_values(con, run_id, sorted(carried or ()))
    if getattr(result, "capability_profile", ""):
        store.record_capability_profile(con, run_id, result.capability_profile)
    store.finish_run(
        con, run_id,
        status=ver.status if result.status in ("finished",) else "failed",
        finding=served.to_dict() if ver.status != V.REJECTED else None,
        finding_raw=(raw_finding.to_dict() if raw_finding
                     else ({"unparsed_model_output": raw_text[:8000]} if raw_text
                           else None)),
        verification=ver.to_dict(),
        claims_total=ver.claims_total, claims_verified=ver.claims_verified,
        iterations=result.iterations, tool_calls=len(result.records),
        prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
        latency_s=time.monotonic() - started, error=result.error,
        provider_cost_usd=getattr(result, "provider_cost_usd", None),
    )
    if thread_id:
        store.bump_thread(con, thread_id)
    out = {"run_id": run_id, "status": ver.status,
           "claims_verified": ver.claims_verified, "claims_total": ver.claims_total,
           "reject_reason": ver.reject_reason, "tool_calls": len(result.records),
           "capability_profile": getattr(result, "capability_profile", "")}
    _heartbeat(run_id, {**out, "status": ver.status, "done": True,
                        "tools": [_tool_row(r) for r in result.records]})
    return out


def db_handle():
    from serve import db
    return db.open_ops_only()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run one PulseTwin triage.")
    p.add_argument("--plant", required=True)
    p.add_argument("--question", default="")
    p.add_argument("--intent", default="ask", choices=sorted(intents.KEYS))
    p.add_argument("--station-id", type=int, default=None)
    p.add_argument("--thread-id", default=None)
    p.add_argument("--turn-index", type=int, default=0)
    p.add_argument("--run-id", default=None)
    p.add_argument("--api-base", default=DEFAULT_API_BASE)
    p.add_argument("--role", default="manager",
                   help="role the tools act AS (capped by the caller's own role)")
    p.add_argument("--requested-by", default="cli")
    p.add_argument("--mode", default="auto", choices=("auto", "agent", "deterministic"))
    args = p.parse_args(argv)

    # `ask` is the only intent that needs the operator's own words; a named action
    # carries its task in genai/intents.py and its label in `question` for display.
    if args.intent == intents.ASK and not args.question.strip():
        p.error("--question is required for --intent ask")
    if not args.question.strip():
        args.question = intents.LABELS[args.intent]

    # When --run-id is supplied the caller (serve/routers/triage.py) has ALREADY
    # inserted the queued row, so do not touch the database here.
    #
    # Opening a second connection to re-insert it was a real deadlock: the duplicate
    # INSERT raises, and because serve/db.py::execute commits AFTER the statement, the
    # failed write left a transaction open on that connection -- which then blocked this
    # process's own second connection on the very next UPDATE. Two handles to one SQLite
    # file inside one process contend exactly as two processes do.
    run_id = args.run_id
    if run_id is None:
        run_id = secrets.token_hex(8)
        con, _ = db_handle()
        store.create_run(
            con, run_id=run_id, plant_id=args.plant, question=args.question,
            requested_by=args.requested_by, acting_role=args.role,
            mode="agent" if args.mode != "deterministic" else "deterministic",
            thread_id=args.thread_id, turn_index=args.turn_index,
            intent=args.intent, station_id=args.station_id,
        )

    out = run(plant_id=args.plant, question=args.question, run_id=run_id,
              api_base=args.api_base, acting_role=args.role,
              requested_by=args.requested_by, mode=args.mode,
              intent=args.intent, station_id=args.station_id,
              thread_id=args.thread_id, turn_index=args.turn_index)
    print(json.dumps(out, indent=2))
    return 0 if out["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
