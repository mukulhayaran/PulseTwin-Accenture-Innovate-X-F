"""Agentic triage: trigger a run, poll it, read the verified finding.

The heavy half (`genai/`) is spawned as a DETACHED SUBPROCESS -- `genai` is not on
serve/'s import allowlist and must never be (CLAUDE.md §2 / ADR-036). "Trigger heavy,
don't host it."

`tests/test_serve_imports.py` needed NO edit to cover this: `genai` is absent from its
ALLOWED set, so any `import genai` here would already fail the wall test. The wall held
without being widened, which is the point.
"""

from __future__ import annotations

import json
from datetime import datetime
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from serve import db
from serve.middleware.audit import log_action
from serve.middleware.auth import require_role

router = APIRouter()
ROOT = Path(__file__).resolve().parents[2]

ROLE_ORDER = ("supervisor", "manager", "leadership", "system")
MAX_QUESTION_CHARS = 500

# Mirrors genai/intents.py. Only the KEYS and the short user-facing LABELS live here --
# the instructions, the model guidance and the deterministic plan for each intent stay in
# genai/, which serve/ cannot import (the import wall). Same precedent as API_KEY_ENV
# above; `tests/test_triage_api.py` asserts these keys equal set(genai.intents.INTENTS),
# so a new intent added on one side fails a test rather than silently 422-ing.
INTENT_LABELS = {
    "ask": "Ask",
    "incident": "Investigate this incident",
    "handover": "Generate shift handover",
    "shift_intel": "Generate shift intelligence",
    "whatif": "Run a what-if",
    "starvation": "Prevent line starvation",
    "paint_env": "Mitigate paint environment risk",
    "tool_wear": "Assess tool degradation",
}
INTENT_KEYS = frozenset(INTENT_LABELS)
INTENT_PATTERN = "^(ask|incident|handover|shift_intel|whatif|starvation|paint_env|tool_wear)$"

# The phase trail the UI renders. Mirrors genai/tools.py::PHASE_ORDER, for the same
# import-wall reason. The first entry is written by THIS module before the worker exists.
PHASE_ORDER = (
    "Starting investigation",
    "Checking production state",
    "Reading station performance",
    "Examining risk signals",
    "Tracing affected units",
    "Running simulation",
    "Costing the options",
    "Searching engineering knowledge",
    "Composing the finding",
    "Verifying every number",
)

HEARTBEAT_VERSION = 2

# Mirrors genai/config.py's credential names. Duplicated rather than imported for the
# same import-wall reason serve/ mirrors any genai/ constant -- keep the two in
# sync; a change to one without the other is a bug. `tests/test_triage_api.py` reads
# these symbolically and asserts they equal genai's, so drift is a test failure.
#
# The canonical name is provider-neutral: a provider is a base_url plus a model id, and
# swapping between OpenAI-compatible endpoints should not require a code change. The
# legacy names are read as a fallback so an existing deployment keeps working.
API_KEY_ENV = "PULSETWIN_GENAI_API_KEY"
LEGACY_KEY_ENVS = ("GROQ_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY")
KEY_ENVS = (API_KEY_ENV, *LEGACY_KEY_ENVS)


def _jobs_dir() -> Path:
    base = Path(os.environ.get("PULSETWIN_JOBS_DIR", tempfile.gettempdir()))
    return base / "pulsetwin_triage_runs"


def _credential_present() -> bool:
    return any(os.environ.get(name, "").strip() for name in KEY_ENVS)


def _cap_role(requested: str | None, caller: str) -> str:
    """Never let a run act above the role that triggered it.

    Without this a `manager` could start a run acting as `leadership` and read economics
    they cannot read directly -- privilege escalation through the agent. Capping turns a
    restricted run into a FEATURE: the agent reports what it could not read, rather than
    estimating it.
    """
    if requested is None or requested not in ROLE_ORDER:
        return caller
    return requested if ROLE_ORDER.index(requested) <= ROLE_ORDER.index(caller) else caller


def _loads(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


class TriageRequest(BaseModel):
    plant_id: str = "plant_a"
    # Optional now: a named action (handover, shift intelligence) carries its task in
    # genai/intents.py and needs no operator prose. Only `ask` requires a question, and
    # that is enforced in the handler where a useful message can be returned.
    question: str | None = Field(default=None, max_length=MAX_QUESTION_CHARS)
    intent: str = Field(default="ask", pattern=INTENT_PATTERN)
    thread_id: str | None = None
    # ALWAYS structured, never parsed out of the question text. CLAUDE.md §1 bans station
    # literals, and regexing "S17" out of prose is how a copilot simulates the wrong
    # station with complete confidence.
    station_id: int | None = None
    acting_role: str | None = None
    mode: str = Field(default="auto", pattern="^(auto|agent|deterministic)$")


@router.get("/api/triage/availability")
def availability() -> dict[str, Any]:
    """Ungated, like /api/health: the one call every client can always make.

    Reports credential PRESENCE only -- never the value, a prefix, or its length.
    """
    if os.environ.get("PULSETWIN_JOBS_ENABLED", "1") == "0":
        return {"enabled": False, "reason": "disabled_in_deployment", "mode": "none",
                "intents": {}, "phase_order": []}
    if _credential_present():
        return {"enabled": True, "reason": "ok", "mode": "agent",
                "intents": INTENT_LABELS, "phase_order": list(PHASE_ORDER)}
    return {"enabled": True, "reason": "no_provider_credential",
            "mode": "deterministic",
            "intents": INTENT_LABELS, "phase_order": list(PHASE_ORDER)}


def _thread_title(question: str) -> str:
    """A short, stable name for a conversation, taken from its first turn.

    Derived from the operator's own words, never model-written: a title the model chose
    would be one more unverified string on screen, and it would change between reloads.
    """
    text = " ".join(question.split())
    return text[:72] + ("..." if len(text) > 72 else "")


def start_triage_run(
    ops: Any,
    *,
    plant_id: str,
    question: str,
    intent: str,
    role: str,
    acting_role: str,
    mode: str,
    api_base: str,
    thread_id: str | None = None,
    station_id: int | None = None,
) -> dict[str, Any]:
    """Create the thread/run rows, write the first heartbeat, and spawn the detached
    `genai.triage` subprocess. This is the ONE path that starts a run -- called both by
    the HTTP endpoint below (a human asked) and by
    `serve.live_simulation.LiveSimulationManager._trigger_auto_investigation` (the system
    investigates before anyone asked). An auto-triggered run must go through the exact
    same verifier and the exact same DB rows as a manual one, not a second, weaker path
    for the case nobody is watching yet.

    Raises ValueError on a bad thread reference -- the HTTP caller translates that to the
    matching HTTPException; the background caller just logs and moves on (see
    `_trigger_auto_investigation`'s try/except).
    """
    run_id = secrets.token_hex(8)

    turn_index = 0
    if thread_id:
        thread = db.query_one(
            ops, "SELECT * FROM triage_threads WHERE thread_id = ?", (thread_id,))
        if thread is None:
            raise ValueError(f"unknown thread '{thread_id}'")
        # A thread is plant-scoped because the values it carries forward are plant facts.
        # Letting a turn switch plants would carry plant_a's throughput into a plant_b
        # answer as a verifier exemption.
        if thread["plant_id"] != plant_id:
            raise ValueError(f"thread '{thread_id}' belongs to plant '{thread['plant_id']}'")
        busy = db.query_one(
            ops,
            "SELECT run_id FROM triage_runs WHERE thread_id = ? "
            "AND status IN ('queued','running') LIMIT 1",
            (thread_id,),
        )
        if busy:
            # Per-turn spawn means two concurrent turns would interleave heartbeats in
            # one file and race for the same turn index. One turn at a time per thread.
            raise ValueError(
                f"thread '{thread_id}' already has run '{busy['run_id']}' in flight")
        row = db.query_one(
            ops, "SELECT MAX(turn_index) AS n FROM triage_runs WHERE thread_id = ?",
            (thread_id,))
        turn_index = int((row or {}).get("n") or 0) + 1
    else:
        thread_id = secrets.token_hex(8)
        db.execute(
            ops,
            """INSERT INTO triage_threads
               (thread_id, plant_id, title, opened_by, turn_count)
               VALUES (?, ?, ?, ?, 0)""",
            (thread_id, plant_id, _thread_title(question), role),
        )

    # Insert BEFORE spawning so a run is never invisible, even if the worker dies.
    db.execute(
        ops,
        """INSERT INTO triage_runs
           (run_id, plant_id, question, requested_by, acting_role, mode, status,
            thread_id, turn_index, intent, station_id)
           VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)""",
        (run_id, plant_id, question, role, acting_role, mode,
         thread_id, turn_index, intent, station_id),
    )

    # A list argv never goes through a shell, so the question carries no injection
    # surface; the length and control-character caps in start_run are defence in depth.
    # The child's stdio goes to a per-run log beside its heartbeat, NOT to DEVNULL.
    # A detached worker whose failures vanish is precisely how this repo ended up with
    # a container nobody had run: if the job dies on import, the only evidence is the
    # row staying `queued` forever, with nothing to read.
    jobs = _jobs_dir()
    jobs.mkdir(parents=True, exist_ok=True)

    # Write the FIRST heartbeat before spawning anything.
    #
    # The longest single wait in a run is the interpreter starting, importing the
    # provider SDK, and making the first model round trip -- all of it BEFORE the worker
    # can write its own first heartbeat. Without this the panel shows a bare spinner for
    # one to three seconds at the exact moment a demo audience is deciding whether the
    # thing works. Six lines buy a truthful label in about 200ms.
    try:
        (jobs / f"{run_id}.json").write_text(json.dumps({
            "v": HEARTBEAT_VERSION, "run_id": run_id, "thread_id": thread_id,
            "turn_index": turn_index, "status": "queued", "mode": mode,
            "intent": intent, "phase": PHASE_ORDER[0], "phase_index": 0,
            "iteration": 0, "elapsed_s": 0.0, "tools": [], "done": False,
        }), encoding="utf-8")
    except OSError:
        pass  # a heartbeat failure must never stop a run from starting

    log_path = jobs / f"{run_id}.log"
    try:
        log = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 - owned by the child
    except OSError:
        log = None

    subprocess.Popen(
        [sys.executable, "-m", "genai.triage",
         "--plant", plant_id, "--question", question,
         "--run-id", run_id, "--api-base", api_base,
         "--role", acting_role, "--requested-by", role, "--mode", mode,
         "--intent", intent,
         "--thread-id", thread_id, "--turn-index", str(turn_index)]
        + (["--station-id", str(station_id)] if station_id is not None else []),
        cwd=str(ROOT),
        env={**os.environ, "OMP_NUM_THREADS": "1", "PYTHONPATH": str(ROOT)},
        stdout=log or subprocess.DEVNULL,
        stderr=subprocess.STDOUT if log else subprocess.DEVNULL,
    )

    return {"run_id": run_id, "status": "queued", "mode": mode, "acting_role": acting_role,
            "thread_id": thread_id, "turn_index": turn_index, "intent": intent}


@router.post("/api/triage/runs")
def start_run(req: TriageRequest, request: Request,
              role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    if os.environ.get("PULSETWIN_JOBS_ENABLED", "1") == "0":
        raise HTTPException(503, "GenAI triage is disabled in this deployment")
    config_path = ROOT / "configs" / f"{req.plant_id}.yaml"
    if not config_path.exists():
        raise HTTPException(404, f"unknown plant '{req.plant_id}'")

    question = (req.question or "").strip()
    if req.intent == "ask":
        if len(question) < 4:
            raise HTTPException(422, f"question is required for intent '{req.intent}'")
    elif not question:
        # A named action still needs a `question` on the row: it is what the thread
        # renders as the operator's turn, immediately, before the worker has started.
        question = INTENT_LABELS[req.intent]
    if any(ord(c) < 32 for c in question):
        raise HTTPException(422, "question must contain no control characters")
    if req.intent == "whatif" and req.station_id is None:
        raise HTTPException(422, "station_id is required for intent 'whatif'")

    acting = _cap_role(req.acting_role, role)
    mode = req.mode
    if mode == "auto":
        mode = "agent" if _credential_present() else "deterministic"
    if mode == "agent" and not _credential_present():
        raise HTTPException(503, f"{API_KEY_ENV} is not configured")

    ops = request.app.state.ops
    port = request.url.port or 7860
    api_base = os.environ.get("PULSETWIN_API_BASE", f"http://127.0.0.1:{port}")

    try:
        result = start_triage_run(
            ops, plant_id=req.plant_id, question=question, intent=req.intent,
            role=role, acting_role=acting, mode=mode, api_base=api_base,
            thread_id=req.thread_id, station_id=req.station_id,
        )
    except ValueError as exc:
        status = 404 if str(exc).startswith("unknown thread") else 409
        raise HTTPException(status, str(exc)) from exc

    log_action(request, ops, role, "triage_run_started", "triage", result["run_id"],
               {"plant_id": req.plant_id, "acting_role": acting, "mode": mode,
                "intent": req.intent, "thread_id": result["thread_id"],
                "turn_index": result["turn_index"]})
    return result


@router.get("/api/triage/runs")
def list_runs(request: Request, plant_id: str = "plant_a", limit: int = 10,
              role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    limit = max(1, min(int(limit), 50))
    rows = db.query_all(
        request.app.state.ops,
        """SELECT run_id, created_at, plant_id, question, status, mode, acting_role,
                  claims_total, claims_verified, tool_calls, latency_s, cost_usd,
                  thread_id, turn_index, intent, capability_profile,
                  orchestrated, agents_used
           FROM triage_runs WHERE plant_id = ?
           ORDER BY created_at DESC LIMIT ?""",
        (plant_id, limit),
    )
    # `agents_used` is JSONB on Postgres (auto-decoded) but TEXT on the SQLite dev/test
    # backend -- `_loads()` normalises both, same convention `finding`/`verification`
    # already follow in get_run/get_thread below.
    for r in rows:
        r["agents_used"] = _loads(r.get("agents_used"))
    return {"runs": rows, "count": len(rows)}


@router.get("/api/triage/runs/{run_id}")
def get_run(run_id: str, request: Request, include_evidence: bool = False,
            role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    ops = request.app.state.ops
    row = db.query_one(ops, "SELECT * FROM triage_runs WHERE run_id = ?", (run_id,))
    if row is None:
        return {"run_id": run_id, "status": "unknown"}

    out = dict(row)
    # Only the POST-REDACTION finding is ever served. `finding_raw` stays in the database
    # for audit and is deliberately not returned: a redacted claim that reaches a screen
    # has not been redacted.
    out.pop("finding_raw", None)
    out["finding"] = _loads(row.get("finding"))
    out["verification"] = _loads(row.get("verification"))
    # ADR-045: SELECT * already carries `orchestrated`/`agents_used` for any run where
    # genai/store.py::record_agents_used ran -- NULL/0 for every other run, unaffected.
    out["agents_used"] = _loads(row.get("agents_used"))

    calls = db.query_all(
        ops,
        """SELECT call_id, seq, tool_name, arguments, result, duration_ms, agent
           FROM triage_tool_calls WHERE run_id = ? ORDER BY seq""",
        (run_id,),
    )
    out["tool_ledger"] = [
        {
            "call_id": c["call_id"], "seq": c["seq"], "tool_name": c["tool_name"],
            "agent": c["agent"], "arguments": _loads(c["arguments"]),
            "duration_ms": c["duration_ms"],
            **({"result": _loads(c["result"])} if include_evidence else {}),
        }
        for c in calls
    ]

    # While the worker is still going the DB row has no ledger yet -- merge the
    # heartbeat so the panel shows the phase trail and tool calls filling in rather than
    # 30s of spinner.
    #
    # `progress` is merged whenever the run is still in flight, but the TOOL LEDGER only
    # when the database has none: once the worker has persisted its calls, the DB is
    # authoritative and re-reading the file would make the trail flicker backwards at the
    # handover. A flicker at exactly that moment looks like a bug on a projector.
    if row.get("status") in ("queued", "running") and _worker_is_dead(run_id, row.get("created_at")):
        # A worker killed outright (OOM, memory reaper, kill -9) cannot record its own
        # failure. Report it rather than let the panel poll a dead run forever. The row
        # is left as-is: a GET does not write, and the worker may still finish it.
        out["status"] = "failed"
        out["error"] = "worker_stopped: no progress for "                        f"{int(WORKER_STALE_S // 60)} minutes"
    elif row.get("status") in ("queued", "running"):
        hb = _read_heartbeat(run_id)
        if hb:
            out["progress"] = {
                "phase": hb.get("phase"),
                "phase_index": hb.get("phase_index"),
                "iteration": hb.get("iteration"),
                "max_iterations": hb.get("max_iterations"),
                "elapsed_s": hb.get("elapsed_s"),
                "agent": hb.get("agent"),
                "agents": hb.get("agents"),
            }
            if not calls:
                out["tool_ledger"] = hb.get("tools", [])
    out["phase_order"] = list(PHASE_ORDER)
    return out


# Three times genai/config.py's RUN_WALLCLOCK_CAP_S (300s). The worker rewrites its
# heartbeat on every phase and tool call, so this much silence means the process is gone.
WORKER_STALE_S = 900.0


def _worker_is_dead(run_id: str, created_at: Any) -> bool:
    """No heartbeat write for WORKER_STALE_S. With no heartbeat file at all (it was
    written to a different jobs dir, or cleaned up), judge by the row's age instead."""
    try:
        last = (_jobs_dir() / f"{run_id}.json").stat().st_mtime
    except OSError:
        try:
            last = datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return False  # nothing to judge by: never guess "dead"
    return time.time() - last > WORKER_STALE_S


def _read_heartbeat(run_id: str) -> dict[str, Any] | None:
    """The live progress file, or None.

    A document whose version this build does not recognise is DROPPED rather than
    partially read: a v1 file left in the jobs directory by a previous session has no
    `phase`, and rendering it would show an empty trail on a run that is working fine.
    """
    try:
        hb = json.loads((_jobs_dir() / f"{run_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(hb, dict) or hb.get("v") != HEARTBEAT_VERSION:
        return None
    return hb


# ----------------------------------------------------------------------------- threads


@router.get("/api/triage/threads")
def list_threads(request: Request, plant_id: str = "plant_a", limit: int = 20,
                 intent: str | None = Query(default=None, pattern=INTENT_PATTERN),
                 role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    """`intent` filters on the thread's OPENING turn: the copilot lists `ask` threads
    (conversations) only, so agent runs from /manager and /ops never appear there."""
    limit = max(1, min(int(limit), 50))
    where, params = "t.plant_id = ?", [plant_id]
    if intent:
        where += (" AND EXISTS (SELECT 1 FROM triage_runs r WHERE r.thread_id = "
                  "t.thread_id AND r.turn_index = 0 AND r.intent = ?)")
        params.append(intent)
    rows = db.query_all(
        request.app.state.ops,
        f"""SELECT t.thread_id, t.created_at, t.plant_id, t.title, t.opened_by,
                   t.turn_count, t.last_run_at
              FROM triage_threads t WHERE {where}
             ORDER BY COALESCE(t.last_run_at, t.created_at) DESC LIMIT ?""",
        (*params, limit),
    )
    return {"threads": rows, "count": len(rows)}


@router.get("/api/triage/threads/{thread_id}")
def get_thread(thread_id: str, request: Request,
               role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    """A conversation: the thread row plus every turn, oldest first.

    Turn rows carry the served finding and the verdict but NOT `finding_raw` -- the same
    rule get_run enforces. A redacted claim that reaches a screen has not been redacted,
    and a thread view is still a screen.
    """
    ops = request.app.state.ops
    thread = db.query_one(
        ops, "SELECT * FROM triage_threads WHERE thread_id = ?", (thread_id,))
    if thread is None:
        raise HTTPException(404, f"unknown thread '{thread_id}'")

    rows = db.query_all(
        ops,
        """SELECT run_id, created_at, turn_index, intent, station_id, question, status,
                  mode, acting_role, claims_total, claims_verified, tool_calls,
                  latency_s, cost_usd, cost_source, capability_profile, error,
                  finding, verification, orchestrated, agents_used
             FROM triage_runs WHERE thread_id = ? ORDER BY turn_index""",
        (thread_id,),
    )
    turns = []
    for r in rows:
        turn = dict(r)
        turn["finding"] = _loads(r.get("finding"))
        turn["verification"] = _loads(r.get("verification"))
        turn["agents_used"] = _loads(r.get("agents_used"))
        turns.append(turn)

    return {"thread": dict(thread), "turns": turns, "count": len(turns),
            "phase_order": list(PHASE_ORDER)}


@router.get("/api/triage/intents")
def list_intents(role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    """The named actions the copilot offers, so the UI does not hardcode them."""
    return {
        "intents": [
            {"key": k, "label": v,
             "requires_station": k == "whatif",
             "requires_question": k == "ask"}
            for k, v in INTENT_LABELS.items()
        ]
    }
