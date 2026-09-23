"""Lightweight RBAC (finals brief Phase F4). Not full OAuth/SSO -- a role header,
structured so the production swap is small: replace `get_role`'s body with a JWT-claim
read and nothing else in this file, or any caller of it, changes. See docs/SECURITY.md for
the OIDC upgrade path (Azure AD / Okta, role claims mapped from AD groups).

Header: `X-PulseTwin-Role: supervisor | manager | leadership | system`. Missing header
defaults to `supervisor` -- the most common floor-facing caller -- so an unauthenticated
request gets the LEAST access this API grants, never the most.

Role permissions (finals brief SS4), as a strict hierarchy -- each role can do everything
the one before it can:
    supervisor  -- stream, predict, trace/containment, feedback, bottleneck (read)
    manager     -- + whatif, bottleneck (full), station stats, trends
    leadership  -- + economics, instrumentation plan, model health
    system      -- + admin endpoints (retrain, promote, rollback, config)
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

ROLES = ("supervisor", "manager", "leadership", "system")
_HIERARCHY = {role: i for i, role in enumerate(ROLES)}


def get_role(request: Request) -> str:
    role = request.headers.get("X-PulseTwin-Role", "supervisor").strip().lower()
    if role not in ROLES:
        raise HTTPException(400, f"unknown role '{role}'; must be one of {ROLES}")
    request.state.role = role
    return role


def get_user(request: Request, role: str = Depends(get_role)) -> str:
    """Demo identity seam; replace this header with the OIDC subject claim in production."""
    user = request.headers.get("X-PulseTwin-User", f"demo-{role}").strip()
    if not user or len(user) > 100 or any(ord(char) < 32 for char in user):
        raise HTTPException(400, "X-PulseTwin-User must be 1-100 visible characters")
    request.state.user = user
    return user


def require_role(minimum: str):
    """FastAPI dependency factory: `Depends(require_role("system"))`.

    Role hierarchy, not an exact match -- `system` satisfies a `manager` requirement too.
    """
    if minimum not in _HIERARCHY:
        raise ValueError(f"unknown role '{minimum}'")

    def _check(role: str = Depends(get_role)) -> str:
        if _HIERARCHY[role] < _HIERARCHY[minimum]:
            raise HTTPException(
                403, f"role '{role}' cannot access this endpoint (requires "
                     f"'{minimum}' or higher)"
            )
        return role

    return _check
