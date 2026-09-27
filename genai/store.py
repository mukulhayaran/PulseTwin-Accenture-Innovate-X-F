"""Ops-DB persistence for triage runs.

Follows `mlops/model_registry.py`: an offline module reaching the operational tables
through `serve.db.open_ops_only()`, so the GenAI tier and `serve/routers/triage.py`
always agree on where the rows live and on which backend answered.

Importing `serve.db` from here is one-way and legal -- the import wall governs what
`serve/` may import, not what may import `serve/`. `mlops/` does exactly the same.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from genai import config
from serve import db

# The provider rate card. Contract #2 (CLAUDE.md §3): a dollar figure in an API response
# carries its source, and our own per-run cost is a dollar figure the moment the Ops view
# renders it. Read from https://console.groq.com/docs/models on 2026-09-20 for
# openai/gpt-oss-120b: $0.15 per million input tokens, $0.60 per million output.
#
# These constants are tied to config.MODEL_ID. Point PULSETWIN_GENAI_MODEL at a different
# model and the rate card is wrong -- which is why `cost_source` is persisted per run
# alongside `cost_usd` rather than being assumed constant across the table's history.
# FALLBACK ONLY, and it names the model it was read for so a reader can tell whether it
# applies. `_cost()` prefers the provider's own `usage.cost` whenever one is reported --
# OpenRouter reports it on every response, so on the current default this rate card is
# never reached. It stays for endpoints that report no cost at all, such as Groq.
COST_SOURCE = (
    "estimated from Groq published rates for openai/gpt-oss-120b, "
    "console.groq.com/docs/models, read 2026-09-20 ($0.15/M input, $0.60/M output) -- "
    "the endpoint used reported no cost of its own"
)
USD_PER_INPUT_TOKEN = 0.15 / 1_000_000
USD_PER_OUTPUT_TOKEN = 0.60 / 1_000_000


def estimate_cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    return prompt_tokens * USD_PER_INPUT_TOKEN + completion_tokens * USD_PER_OUTPUT_TOKEN


def _cost(prompt_tokens: int, completion_tokens: int,
          provider_cost_usd: float | None) -> tuple[float, str]:
    """(cost_usd, cost_source) -- the provider's own figure when it reports one.

    The rate card above is tied to ONE model on ONE provider. Pointing
    PULSETWIN_GENAI_MODEL elsewhere silently kept charging Groq's gpt-oss-120b prices:
    a free OpenRouter model billed $0.015 a run, carrying a `source` string naming a
    provider that was never called. That is a contract #2 violation (CLAUDE.md §3), not
    a rounding error -- the figure and its source disagreed about which model produced
    it. When the endpoint returns `usage.cost`, that IS the source and it is used.
    """
    if prompt_tokens == 0 and completion_tokens == 0 and provider_cost_usd is None:
        # The deterministic path (`genai/offline_triage.py`) calls no model at all. Its
        # cost is zero as a FACT, not as an estimate, and citing a token rate card for it
        # would name a price list that had nothing to do with the number.
        return 0.0, "no language model was called (deterministic path)"
    if provider_cost_usd is not None:
        return float(provider_cost_usd), (
            f"{config.PROVIDER} reported usage.cost for {config.MODEL_ID}, "
            f"summed over the run's round trips")
    return (estimate_cost_usd(prompt_tokens, completion_tokens), COST_SOURCE)


def _dumps(value: Any) -> str | None:
    return None if value is None else json.dumps(value, default=str)


def create_run(
    con: Any, *, run_id: str, plant_id: str, question: str,
    requested_by: str, acting_role: str, mode: str,
    thread_id: str | None = None, turn_index: int = 0,
    intent: str = "ask", station_id: int | None = None,
) -> None:
    """Insert the `queued` row BEFORE the worker starts, so a run is never invisible.

    The thread arguments default to the one-shot shape, so every existing caller keeps
    working and a run with no thread is still a valid run.
    """
    db.execute(
        con,
        """INSERT INTO triage_runs
           (run_id, plant_id, question, requested_by, acting_role, mode, status,
            thread_id, turn_index, intent, station_id)
           VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)""",
        (run_id, plant_id, question, requested_by, acting_role, mode,
         thread_id, turn_index, intent, station_id),
    )


# ------------------------------------------------------------------------------ threads


@dataclass(frozen=True)
class ThreadTurn:
    turn_index: int
    question: str
    intent: str
    status: str
    summary: str
    recommendation: str
    claim_texts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ThreadContext:
    turns: list[ThreadTurn] = field(default_factory=list)
    # Numbers an earlier turn's verifier already passed. Handed to verifier.verify() as
    # a C3 prose exemption and nothing else (ADR-039).
    carried_values: list[float] = field(default_factory=list)


def load_thread_context(
    con: Any, thread_id: str, *, before_turn: int,
    max_turns: int = config.MAX_THREAD_TURNS,
) -> ThreadContext:
    """Earlier turns of this thread, and the values they established.

    Only runs that actually SERVED something contribute: a `rejected` turn served
    nothing, so it has nothing to carry -- the same posture as the rest of this tier.

    Bounded by `max_turns` and by MAX_THREAD_CLAIMS_PER_TURN so a long conversation
    cannot grow into a 40k-token prompt. Turn 12 should cost what turn 2 cost.
    """
    rows = db.query_all(
        con,
        """SELECT turn_index, question, intent, status, finding
             FROM triage_runs
            WHERE thread_id = ? AND turn_index < ?
              AND status IN ('verified', 'verified_with_redactions')
            ORDER BY turn_index DESC LIMIT ?""",
        (thread_id, before_turn, max_turns),
    )

    turns: list[ThreadTurn] = []
    carried: set[float] = set()
    for row in reversed(rows):
        finding = row.get("finding")
        if isinstance(finding, str):
            try:
                finding = json.loads(finding)
            except ValueError:
                finding = None
        if not isinstance(finding, dict):
            continue

        claim_texts: list[str] = []
        for claim in (finding.get("claims") or [])[:config.MAX_THREAD_CLAIMS_PER_TURN]:
            if not isinstance(claim, dict):
                continue
            text = str(claim.get("text") or "")
            # A redacted claim carries `value: 0.0` as a placeholder. Admitting it would
            # exempt EVERY sentence containing a zero in every later turn, which is the
            # single easiest way to accidentally disarm the uncited-number sweep.
            if text.startswith("[claim removed"):
                continue
            claim_texts.append(text)
            value = claim.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                carried.add(float(value))

        turns.append(ThreadTurn(
            turn_index=int(row.get("turn_index") or 0),
            question=str(row.get("question") or ""),
            intent=str(row.get("intent") or "ask"),
            status=str(row.get("status") or ""),
            summary=str(finding.get("summary") or ""),
            recommendation=str(finding.get("recommendation") or ""),
            claim_texts=claim_texts,
        ))

    return ThreadContext(turns=turns, carried_values=sorted(carried))


def record_carried_values(con: Any, run_id: str, values: list[float]) -> None:
    """Persist what this turn was allowed to carry.

    Required for replay: `genai/verify_cli.py` re-runs the verifier over stored bytes and
    must reach a byte-identical verdict. Without the carried set on the row, a thread
    turn would re-verify differently from how it was served, and the audit claim would
    be false in exactly the case that needed it most.
    """
    db.execute(con, "UPDATE triage_runs SET carried_values=? WHERE run_id=?",
               (_dumps(list(values)), run_id))


def record_capability_profile(con: Any, run_id: str, profile: str) -> None:
    db.execute(con, "UPDATE triage_runs SET capability_profile=? WHERE run_id=?",
               (profile, run_id))


def record_agents_used(con: Any, run_id: str, agents: list[dict[str, Any]]) -> None:
    """Which specialists ran and how each finished (ADR-045). Set only for orchestrated
    runs -- `genai/triage.py::_persist()` calls this only when
    `LoopResult.agents_used` is non-empty, so a single-agent run's row keeps
    `orchestrated=0` and `agents_used=NULL`."""
    db.execute(
        con,
        # A bound bool, not a literal 1: Postgres's column is BOOLEAN (migration 014) and
        # refuses an integer, which crashed every orchestrated incident run on the stack.
        "UPDATE triage_runs SET orchestrated=?, agents_used=? WHERE run_id=?",
        (True, _dumps(agents), run_id),
    )


def bump_thread(con: Any, thread_id: str) -> None:
    db.execute(
        con,
        """UPDATE triage_threads
              SET turn_count = COALESCE(turn_count, 0) + 1,
                  last_run_at = ?
            WHERE thread_id = ?""",
        (_now(), thread_id),
    )


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mark_fallback(con: Any, run_id: str) -> None:
    """ADR-064: the provider was unavailable, so this run is now the deterministic plan.
    The row must say so -- the inbox and the cards label every run by its real mode."""
    db.execute(con, "UPDATE triage_runs SET mode='deterministic' WHERE run_id=?", (run_id,))


def mark_running(con: Any, run_id: str, provider: str | None, model_id: str | None) -> None:
    db.execute(
        con,
        "UPDATE triage_runs SET status='running', provider=?, model_id=? WHERE run_id=?",
        (provider, model_id, run_id),
    )


def finish_run(
    con: Any, run_id: str, *, status: str,
    finding: dict[str, Any] | None, finding_raw: dict[str, Any] | None,
    verification: dict[str, Any] | None,
    claims_total: int, claims_verified: int,
    iterations: int, tool_calls: int,
    prompt_tokens: int, completion_tokens: int,
    latency_s: float, error: str = "",
    provider_cost_usd: float | None = None,
) -> None:
    db.execute(
        con,
        """UPDATE triage_runs SET
             status=?, finding=?, finding_raw=?, verification=?,
             claims_total=?, claims_verified=?, iterations=?, tool_calls=?,
             prompt_tokens=?, completion_tokens=?, cost_usd=?, cost_source=?,
             latency_s=?, error=?
           WHERE run_id=?""",
        (
            status, _dumps(finding), _dumps(finding_raw), _dumps(verification),
            claims_total, claims_verified, iterations, tool_calls,
            prompt_tokens, completion_tokens,
            *_cost(prompt_tokens, completion_tokens, provider_cost_usd),
            latency_s, error, run_id,
        ),
    )


def record_tool_calls(con: Any, run_id: str, records: list[Any]) -> None:
    """Persist the evidence of record -- the exact payloads the model was shown."""
    for r in records:
        db.execute(
            con,
            """INSERT INTO triage_tool_calls
               (run_id, call_id, seq, tool_name, arguments, result, duration_ms, phase,
                agent)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, r.call_id, r.seq, r.name,
             _dumps(r.arguments), _dumps(r.result), r.duration_ms,
             getattr(r, "phase", "") or "", getattr(r, "agent", "") or None),
        )
