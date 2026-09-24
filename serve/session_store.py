"""Opaque server-side sessions for the demo login (serve/routers/auth_demo.py).

Why this exists: before it, a correct login returned only a role STRING, which the
frontend then echoed back in `X-PulseTwin-Role` forever. That made the login a UI
formality -- the header was self-asserting either way, so "manager cannot open the
leadership view" was a statement the client made about itself. A token the server issued
and can revoke turns it into a statement the server can check.

What this is NOT: a JWT, an identity provider, or a replacement for the OIDC upgrade path
in docs/SECURITY.md. It is a random 256-bit opaque token in a process-local dict with a
TTL. That is deliberate and it is enough for the property we want:

  * the token cannot be forged from anything in the client bundle (unlike a role string),
  * the server decides which role it maps to, not the caller,
  * logout and expiry actually end access, and
  * it is backwards compatible -- `X-PulseTwin-Role` with no token behaves exactly as it
    did (see serve/middleware/auth.py), so every existing test and the documented
    header seam are untouched.

Process-local is correct for this deployment: the demo and `docker-compose.prod.yml` both
run a single uvicorn worker. Under multiple workers a token issued by one would be
unknown to another, and the honest fix is the shared store (redis) or the stateless JWT
that docs/SECURITY.md already prescribes -- not a bigger dict. `_LOCK` is still taken
because uvicorn runs sync handlers in a threadpool, so two logins genuinely can mutate
this map at once.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Any

# Eight hours: one shift plus handover, so a demo (or a real supervisor) is never signed
# out mid-task, and an abandoned browser tab does not stay authorised overnight.
TTL_SECONDS = 8 * 60 * 60

# A hard ceiling so a login loop cannot grow this map without bound. Four demo accounts
# do not need 256 live sessions; the cap is a memory guard, not a policy.
MAX_SESSIONS = 256

# token -> {"role": str, "user": str, "issued_at": float, "expires_at": float}
_SESSIONS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _prune(now: float) -> None:
    """Drop expired tokens. Caller holds _LOCK."""
    for token in [t for t, s in _SESSIONS.items() if s["expires_at"] <= now]:
        del _SESSIONS[token]


def issue(role: str, user: str) -> dict[str, Any]:
    """Mint a session for an already-authenticated role. Returns the public fields."""
    now = time.time()
    token = secrets.token_urlsafe(32)
    with _LOCK:
        _prune(now)
        if len(_SESSIONS) >= MAX_SESSIONS:
            # Evict the oldest live session rather than refusing the login -- a full map
            # means someone is hammering /api/auth/login, and locking everyone else out
            # would make that a denial of service against the demo.
            oldest = min(_SESSIONS, key=lambda t: _SESSIONS[t]["issued_at"])
            del _SESSIONS[oldest]
        _SESSIONS[token] = {
            "role": role,
            "user": user,
            "issued_at": now,
            "expires_at": now + TTL_SECONDS,
        }
    return {"token": token, "role": role, "user": user, "expires_at": now + TTL_SECONDS}


def resolve(token: str) -> dict[str, Any] | None:
    """Return the session for a token, or None if unknown or expired."""
    if not token:
        return None
    now = time.time()
    with _LOCK:
        session = _SESSIONS.get(token)
        if session is None:
            return None
        if session["expires_at"] <= now:
            del _SESSIONS[token]
            return None
        return dict(session)


def revoke(token: str) -> bool:
    """End a session. Idempotent -- returns whether a live session was actually removed."""
    with _LOCK:
        return _SESSIONS.pop(token, None) is not None


def clear() -> None:
    """Drop every session. Used by tests; never called by a request handler."""
    with _LOCK:
        _SESSIONS.clear()


def live_count() -> int:
    now = time.time()
    with _LOCK:
        _prune(now)
        return len(_SESSIONS)
