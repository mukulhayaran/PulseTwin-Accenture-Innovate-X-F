"""/api/auth/* -- a credential check and a session in front of the demo role system.

Still not authentication in the production sense (docs/SECURITY.md's OIDC/JWT upgrade
path is the real answer, deliberately not built per the finals brief's anti-pattern
guidance: "do not implement full OAuth/SSO -- a role header is sufficient for the demo").
This is smaller than that: a fixed set of demo credentials, one per role, checked
server-side so they are not just a string sitting in the client JS bundle for anyone who
opens devtools.

A correct login now also mints an opaque session token (serve/session_store.py). The
token, not the caller, decides the role for any request that presents one -- see
serve/middleware/auth.py. `X-PulseTwin-Role` on its own still works exactly as before for
anyone who skips this endpoint, so the header-trusts-itself seam SECURITY.md documents is
unchanged; what the token adds is that the UI's own path through the app is a role the
SERVER issued and can revoke, which is what makes "manager cannot open the leadership
view" more than a claim the client makes about itself.

DEMO_CREDENTIALS below are intentionally plain, fixed, and NOT secrets -- change them (or
wire this to something real) before this app is anything other than a hackathon demo.
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from serve import session_store

router = APIRouter()

# username -> (password, role). One fixed account per role, on purpose -- this demo has
# four personas, not a user directory.
DEMO_CREDENTIALS: dict[str, tuple[str, str]] = {
    "supervisor": ("sup2026", "supervisor"),
    "manager": ("mgr2026", "manager"),
    "leadership": ("lead2026", "leadership"),
    "system": ("sys2026", "system"),
}


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/api/auth/login")
def login(req: LoginRequest) -> dict[str, Any]:
    username = req.username.strip().lower()
    entry = DEMO_CREDENTIALS.get(username)
    # secrets.compare_digest (re-exported from hmac, which is NOT on serve/'s import
    # wall) even though these are not real secrets: constant-time comparison is nearly
    # free and it's the correct habit regardless of what the string protects, unlike a
    # plain `==` that would make timing the ONE incorrect thing this file does not do
    # wrong.
    if not entry or not secrets.compare_digest(req.password, entry[0]):
        raise HTTPException(401, "incorrect username or password")
    role = entry[1]
    session = session_store.issue(role, f"demo-{role}")
    return {"ok": True, "role": role, **session}


@router.get("/api/auth/session")
def whoami(x_pulsetwin_token: str = Header(default="")) -> dict[str, Any]:
    """Validate a token the client is holding.

    The frontend calls this on load to decide whether a restored session is still good,
    which is the whole reason expiry and logout mean anything -- without a server check
    the client would just believe its own stored role again.
    """
    session = session_store.resolve(x_pulsetwin_token.strip())
    if session is None:
        raise HTTPException(401, "no active session")
    return {
        "ok": True,
        "role": session["role"],
        "user": session["user"],
        "expires_at": session["expires_at"],
    }


@router.post("/api/auth/logout")
def logout(x_pulsetwin_token: str = Header(default="")) -> dict[str, Any]:
    """Idempotent: signing out twice, or with a token that already expired, is a 200.

    A 401 here would make the UI's sign-out path fail exactly when the session was
    already gone, which is the one case where the user's intent is definitely satisfied.
    """
    revoked = session_store.revoke(x_pulsetwin_token.strip())
    return {"ok": True, "revoked": revoked}
