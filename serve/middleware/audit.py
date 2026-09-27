"""Action-audit logging against the `audit_log` table (migrations/002).

Every mutating endpoint calls `log_action()` directly, once, right after it succeeds --
not sniffed out of a generic ASGI middleware by matching request paths against a route
table. There are a handful of mutating routes in this API (what-if, alert
acknowledgement, proposal decisions); an explicit call at each site is easier
to verify complete (grep for `log_action` and cross-check against the route list) than
trusting path-template matching to catch every one, including path-parameterised routes
like `/api/alerts/{id}/acknowledge`, from outside the handler.

Never contains raw sensor data or operator identity (CLAUDE.md's operator-privacy and
sourced-economics contracts extend to this table) -- callers pass a small, already-
sanitised payload of IDs and outcomes.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request


def log_action(request: Request, ops_con: Any, role: str, action: str,
                resource_type: str | None, resource_id: str | None,
                payload: dict | None = None) -> None:
    """Never raises -- a failure to audit-log must not break the request it audits."""
    if ops_con is None:
        return
    from serve import db  # local import: avoids a cycle with serve.main at module load

    try:
        db.execute(
            ops_con,
            """INSERT INTO audit_log
                   (user_role, action, resource_type, resource_id, payload, ip_address)
               VALUES (?,?,?,?,?,?)""",
            (role, action, resource_type, resource_id,
             json.dumps(payload or {}, default=str),
             request.client.host if request.client else None),
        )
    except Exception as e:  # pragma: no cover - defensive, exercised by test_audit.py
        print(f"[audit] failed to log action '{action}': {e}")
