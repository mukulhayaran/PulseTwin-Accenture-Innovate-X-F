"""POST /api/auth/login -- a credential check in front of the demo role system.

Still not authentication in the production sense (docs/SECURITY.md's OIDC/JWT upgrade
path is the real answer, deliberately not built per the finals brief's anti-pattern
guidance: "do not implement full OAuth/SSO -- a role header is sufficient for the demo").
This is smaller than that: a fixed set of demo credentials, one per role, checked
server-side so they are not just a string sitting in the client JS bundle for anyone who
opens devtools. It issues no session or token -- a correct login tells the frontend which
role to set via the EXISTING X-PulseTwin-Role mechanism (lib/api.ts::setRole), unchanged.
Anyone who skips this endpoint and calls the API directly with a role header still gets
in, exactly as before -- this raises the bar for someone clicking through the UI, it does
not close the header-trusts-itself gap that SECURITY.md already documents honestly.

DEMO_CREDENTIALS below are intentionally plain, fixed, and NOT secrets -- change them (or
wire this to something real) before this app is anything other than a hackathon demo.
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
    entry = DEMO_CREDENTIALS.get(req.username.strip().lower())
    # secrets.compare_digest (re-exported from hmac, which is NOT on serve/'s import
    # wall) even though these are not real secrets: constant-time comparison is nearly
    # free and it's the correct habit regardless of what the string protects, unlike a
    # plain `==` that would make timing the ONE incorrect thing this file does not do
    # wrong.
    if not entry or not secrets.compare_digest(req.password, entry[0]):
        raise HTTPException(401, "incorrect username or password")
    return {"ok": True, "role": entry[1]}
