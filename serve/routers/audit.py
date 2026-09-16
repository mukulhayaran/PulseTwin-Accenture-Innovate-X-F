"""GET /api/audit/log -- paginated, filterable query over the audit trail
(migrations/002). Leadership and system only: CLAUDE.md's operator-privacy and
sourced-economics contracts extend to who can even SEE the audit trail, not just who can
act.
"""

from __future__ import annotations

import json as _json
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from serve import db
from serve.middleware.auth import require_role

router = APIRouter()


@router.get("/api/audit/log")
def audit_log(
    request: Request,
    role_filter: str | None = Query(None, alias="role"),
    action: str | None = None,
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    limit: int = 50,
    caller_role: str = Depends(require_role("leadership")),
) -> dict[str, Any]:
    ops = request.app.state.ops
    clauses: list[str] = []
    params: list[Any] = []
    if role_filter:
        clauses.append("user_role = ?")
        params.append(role_filter)
    if action:
        clauses.append("action = ?")
        params.append(action)
    if from_ts:
        clauses.append("timestamp >= ?")
        params.append(from_ts)
    if to_ts:
        clauses.append("timestamp <= ?")
        params.append(to_ts)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    rows = db.query_all(
        ops, f"SELECT * FROM audit_log {where} ORDER BY timestamp DESC LIMIT ?",
        (*params, max(1, min(limit, 500))),
    )
    for r in rows:
        if isinstance(r.get("payload"), str):
            try:
                r["payload"] = _json.loads(r["payload"])
            except _json.JSONDecodeError:
                pass
    return {"entries": rows, "count": len(rows)}


@router.get("/api/audit/actions")
def audit_actions(request: Request,
                   caller_role: str = Depends(require_role("leadership"))) -> dict[str, Any]:
    """Distinct action types seen so far -- populates the AuditLogViewer filter dropdown."""
    ops = request.app.state.ops
    rows = db.query_all(ops, "SELECT DISTINCT action FROM audit_log ORDER BY action", ())
    return {"actions": [r["action"] for r in rows]}
