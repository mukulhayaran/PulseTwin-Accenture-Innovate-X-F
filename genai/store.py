"""Ops-DB persistence for triage runs.

Follows `mlops/model_registry.py`: an offline module reaching the operational tables
through `serve.db.open_ops_only()`, so the GenAI tier and `serve/routers/triage.py`
always agree on where the rows live and on which backend answered.

Importing `serve.db` from here is one-way and legal -- the import wall governs what
`serve/` may import, not what may import `serve/`. `mlops/` does exactly the same.
"""

from __future__ import annotations

import json
from typing import Any

from serve import db

# The provider rate card. Contract #2 (CLAUDE.md §3): a dollar figure in an API response
# carries its source, and our own per-run cost is a dollar figure the moment the Ops view
# renders it. Read from https://docs.x.ai/docs/models on 2026-09-20; the sub-200k-prompt
# tier, which every run here sits in (the hard ceiling is ~150k input tokens).
COST_SOURCE = "xAI published rates, docs.x.ai/docs/models, read 2026-09-20 (<200k tier)"
USD_PER_INPUT_TOKEN = 2.00 / 1_000_000
USD_PER_OUTPUT_TOKEN = 6.00 / 1_000_000


def estimate_cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    return prompt_tokens * USD_PER_INPUT_TOKEN + completion_tokens * USD_PER_OUTPUT_TOKEN


def _dumps(value: Any) -> str | None:
    return None if value is None else json.dumps(value, default=str)


def create_run(
    con: Any, *, run_id: str, plant_id: str, question: str,
    requested_by: str, acting_role: str, mode: str,
) -> None:
    """Insert the `queued` row BEFORE the worker starts, so a run is never invisible."""
    db.execute(
        con,
        """INSERT INTO triage_runs
           (run_id, plant_id, question, requested_by, acting_role, mode, status)
           VALUES (?, ?, ?, ?, ?, ?, 'queued')""",
        (run_id, plant_id, question, requested_by, acting_role, mode),
    )


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
            estimate_cost_usd(prompt_tokens, completion_tokens), COST_SOURCE,
            latency_s, error, run_id,
        ),
    )


def record_tool_calls(con: Any, run_id: str, records: list[Any]) -> None:
    """Persist the evidence of record -- the exact payloads the model was shown."""
    for r in records:
        db.execute(
            con,
            """INSERT INTO triage_tool_calls
               (run_id, call_id, seq, tool_name, arguments, result, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, r.call_id, r.seq, r.name,
             _dumps(r.arguments), _dumps(r.result), r.duration_ms),
        )
