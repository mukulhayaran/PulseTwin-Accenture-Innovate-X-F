"""Lightweight RBAC (finals brief Phase F4). Not full OAuth/SSO -- a role header,
structured so the production swap is small: replace `get_role`'s body with a JWT-claim
read and nothing else in this file, or any caller of it, changes. See docs/SECURITY.md for
the OIDC upgrade path (Azure AD / Okta, role claims mapped from AD groups).

Header: `X-PulseTwin-Role: supervisor | manager | leadership | system`. Missing header
defaults to `supervisor` -- the most common floor-facing caller -- so an unauthenticated
request gets the LEAST access this API grants, never the most.

`X-PulseTwin-Token`, when present, WINS over that header: the role comes from the session
the server issued at /api/auth/login (serve/session_store.py) and the declared role is
ignored, except that a caller may still declare a role BELOW its own -- /ops's "Viewing
as" selector drops privilege deliberately, and a session must be allowed to ask for less
than it holds, never more. A token that is unknown or expired is a 401 rather than a
silent fall-back to the header, because falling back would make an expired session
indistinguishable from a valid one and quietly undo the sign-out it represents.

Presenting no token is unchanged from before sessions existed, which is what keeps the
header seam docs/SECURITY.md documents honest rather than pretending to be closed.

Role permissions (finals brief SS4), as a strict hierarchy -- each role can do everything
the one before it can:
    supervisor  -- stream, predict, trace/containment, feedback, bottleneck (read)
    manager     -- + whatif, bottleneck (full), station stats, trends
    leadership  -- + economics, instrumentation plan, model health
    system      -- + everything below; no endpoint requires it alone since ADR-053
                   removed the retrain/promote/rollback admin endpoints
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from serve import session_store
from serve.middleware import jwt_auth

ROLES = ("supervisor", "manager", "leadership", "system")
_HIERARCHY = {role: i for i, role in enumerate(ROLES)}


def get_role(request: Request) -> str:
    # Absent and "supervisor" are NOT the same thing once a session exists: absent means
    # "whatever I am", while an explicit header is a deliberate statement. Defaulting a
    # missing header to supervisor before consulting the token would silently demote every
    # request that did not restate its own role.
    raw = request.headers.get("X-PulseTwin-Role")
    declared = (raw or "supervisor").strip().lower()
    if declared not in ROLES:
        raise HTTPException(400, f"unknown role '{declared}'; must be one of {ROLES}")

    # PRODUCTION POSTURE (ADR-049). Under PULSETWIN_AUTH_MODE=jwt the declared header is
    # no longer a credential: a caller must present a verified bearer token or a
    # server-issued session. The header survives only as a deliberate privilege DROP,
    # clamped below, exactly as it works for a session. Everything below this block is
    # the unchanged dev path.
    if jwt_auth.is_production_auth():
        bearer = jwt_auth.bearer_token(request.headers)
        if bearer:
            identity = jwt_auth.verify_bearer(bearer)
            granted = identity.role
            role = (
                declared if raw is not None and _HIERARCHY[declared] <= _HIERARCHY[granted]
                else granted
            )
            request.state.session_role = granted
            request.state.session_user = identity.subject
            request.state.auth_mode = "jwt"
            request.state.role = role
            return role
        if not request.headers.get("X-PulseTwin-Token", "").strip():
            raise HTTPException(
                401,
                "authentication required: present 'Authorization: Bearer <token>' or a "
                "session token from POST /api/auth/login. The X-PulseTwin-Role header "
                "is not a credential when PULSETWIN_AUTH_MODE=jwt.",
            )

    token = request.headers.get("X-PulseTwin-Token", "").strip()
    if token:
        session = session_store.resolve(token)
        if session is None:
            raise HTTPException(401, "session expired or invalid; sign in again")
        granted = session["role"]
        if raw is None:
            role = granted
        else:
            # A declared role at or below the session's is honoured (deliberate privilege
            # drop); anything above it is ignored rather than rejected, so a stale header
            # left over from a previous role cannot escalate and cannot 403 a legitimate
            # request either.
            role = declared if _HIERARCHY[declared] <= _HIERARCHY[granted] else granted
        request.state.session_role = granted
        request.state.session_user = session["user"]
    else:
        role = declared

    request.state.role = role
    return role


def get_user(request: Request, role: str = Depends(get_role)) -> str:
    """Demo identity seam; replace this header with the OIDC subject claim in production."""
    # A session names its own user; the client cannot relabel who it is once it holds one.
    session_user = getattr(request.state, "session_user", None)
    if session_user:
        request.state.user = session_user
        return session_user

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
