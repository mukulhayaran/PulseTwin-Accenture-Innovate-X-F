"""Entry point: run one triage, verify it, persist it.

    python -m genai.triage --plant plant_a --question "..." [--run-id X] [--mode auto]

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
from genai import config, loop as L, offline_triage, store, tools as T, verifier as V
from genai.api_client import PulseTwinAPI
from genai.finding import ParseError, parse_finding

DEFAULT_API_BASE = os.environ.get("PULSETWIN_API_BASE", "http://127.0.0.1:7860")


def _jobs_dir() -> Path:
    base = Path(os.environ.get("PULSETWIN_JOBS_DIR", tempfile.gettempdir()))
    d = base / "pulsetwin_triage_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _heartbeat(run_id: str, payload: dict[str, Any]) -> None:
    """Live progress only. The ops DB is authoritative for the verdict.

    RetrainingConsole.tsx's own comment records the cost of the other choice: the
    retrain compare() verdict lives ONLY in an ephemeral job file and is lost on restart.
    """
    try:
        (_jobs_dir() / f"{run_id}.json").write_text(
            json.dumps(payload, default=str), encoding="utf-8")
    except OSError:
        pass  # a heartbeat failure must never end a run


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

    store.mark_running(
        con, run_id,
        config.PROVIDER if use_agent else None,
        config.MODEL_ID if use_agent else None,
    )
    _heartbeat(run_id, {"run_id": run_id, "status": "running",
                        "mode": "agent" if use_agent else "deterministic", "tools": []})

    raw_finding = None
    if use_agent:
        result = L.run_loop(
            provider.build_client(), api, registry,
            plant_id=plant_id, question=question, acting_role=acting_role,
            classify_error=provider.classify_error,
            on_progress=lambda recs: _heartbeat(run_id, {
                "run_id": run_id, "status": "running", "mode": "agent",
                "tools": [{"call_id": r.call_id, "name": r.name,
                           "duration_ms": r.duration_ms} for r in recs],
            }),
        )
        if result.status == "finished":
            try:
                raw_finding = parse_finding(result.content, question=question)
            except ParseError as exc:
                ver, served = V.reject_unparseable(question, str(exc))
                return _persist(con, run_id, result, ver, served, None, started)
        else:
            ver, served = V.reject_unparseable(question, result.error or result.status)
            ver.reject_reason = result.error or result.status
            return _persist(con, run_id, result, ver, served, None, started)
    else:
        result, raw_finding = offline_triage.run(
            registry, api, plant_id=plant_id, question=question)

    ver, served = V.verify(raw_finding, result.transcript,
                           station_ids=_station_ids(api, plant_id))
    return _persist(con, run_id, result, ver, served, raw_finding, started)


def _persist(con, run_id, result, ver, served, raw_finding, started) -> dict[str, Any]:
    store.record_tool_calls(con, run_id, result.records)
    store.finish_run(
        con, run_id,
        status=ver.status if result.status in ("finished",) else "failed",
        finding=served.to_dict() if ver.status != V.REJECTED else None,
        finding_raw=raw_finding.to_dict() if raw_finding else None,
        verification=ver.to_dict(),
        claims_total=ver.claims_total, claims_verified=ver.claims_verified,
        iterations=result.iterations, tool_calls=len(result.records),
        prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
        latency_s=time.monotonic() - started, error=result.error,
    )
    out = {"run_id": run_id, "status": ver.status,
           "claims_verified": ver.claims_verified, "claims_total": ver.claims_total,
           "reject_reason": ver.reject_reason, "tool_calls": len(result.records)}
    _heartbeat(run_id, {**out, "done": True})
    return out


def db_handle():
    from serve import db
    return db.open_ops_only()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run one PulseTwin triage.")
    p.add_argument("--plant", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--run-id", default=None)
    p.add_argument("--api-base", default=DEFAULT_API_BASE)
    p.add_argument("--role", default="manager",
                   help="role the tools act AS (capped by the caller's own role)")
    p.add_argument("--requested-by", default="cli")
    p.add_argument("--mode", default="auto", choices=("auto", "agent", "deterministic"))
    args = p.parse_args(argv)

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
        )

    out = run(plant_id=args.plant, question=args.question, run_id=run_id,
              api_base=args.api_base, acting_role=args.role,
              requested_by=args.requested_by, mode=args.mode)
    print(json.dumps(out, indent=2))
    return 0 if out["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
