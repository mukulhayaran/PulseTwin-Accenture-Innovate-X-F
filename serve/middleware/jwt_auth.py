"""OIDC/JWT verification, and the switch that closes the demo role header in production.

THE PROBLEM THIS FIXES. `X-PulseTwin-Role: system` is a self-declared role. Anyone who can
reach the API can send it. That is a reasonable posture for a judged demo and an
unacceptable one for anything else, and until now the only thing standing between the two
was a comment.

WHAT CHANGES. `PULSETWIN_AUTH_MODE` selects the posture explicitly:

    dev  (default)  the header is honoured, exactly as before. Local development, the
                    docker demo, and the test suite run here. Every response carries
                    `X-PulseTwin-Auth-Mode: dev` so nobody can mistake which one is live.

    jwt             the header ALONE grants nothing. A caller must present either a
                    server-issued session token (POST /api/auth/login) or a signed JWT in
                    `Authorization: Bearer ...`. A bare role header is 401, and a role
                    header ABOVE the verified role is ignored, never honoured.

There is no third state and no way to be in `jwt` mode with verification disabled: a
misconfigured `jwt` mode refuses to start (`validate_config`) rather than falling open.
Failing closed at boot is the whole point -- an auth system that degrades to "allow" under
misconfiguration is worse than none, because it is trusted.

VENDOR NEUTRALITY. Nothing here names an identity provider. It verifies a signature
(HS256 with a shared secret, or RS256/ES256 against a JWKS endpoint), checks issuer,
audience and expiry, reads a configurable claim, and maps the values it finds onto
PulseTwin's four roles through a configurable table. Azure AD / Entra, Okta, Auth0,
Keycloak and Cognito all fit that shape; docs/SECURITY.md carries the per-provider
settings. Adding a vendor SDK would be a dependency the import wall does not permit and,
more to the point, is not needed.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass

from fastapi import HTTPException

log = logging.getLogger("pulsetwin.auth")

ROLES = ("supervisor", "manager", "leadership", "system")

DEV = "dev"
JWT = "jwt"

# How long a fetched JWKS is reused before refetching. Providers rotate signing keys; a
# short cache keeps a rotation from causing more than one window of 401s, while still
# avoiding a network round trip per request.
JWKS_TTL_S = 600

_JWKS_CACHE: dict[str, tuple[float, dict]] = {}


def auth_mode() -> str:
    """The active posture. Anything other than 'jwt' is dev -- there is no silent third."""
    mode = os.environ.get("PULSETWIN_AUTH_MODE", DEV).strip().lower()
    return JWT if mode == JWT else DEV


def is_production_auth() -> bool:
    return auth_mode() == JWT


@dataclass(frozen=True)
class JwtConfig:
    issuer: str | None
    audience: str | None
    secret: str | None
    jwks_url: str | None
    algorithms: tuple[str, ...]
    role_claim: str
    subject_claim: str
    role_map: dict[str, str]
    default_role: str | None

    @property
    def verifies(self) -> bool:
        return bool(self.secret or self.jwks_url)


def _role_map_from_env() -> dict[str, str]:
    """IdP group/role value -> PulseTwin role.

    JSON in `PULSETWIN_ROLE_MAP`, e.g.
        {"PulseTwin-Admins": "system", "Plant-Managers": "manager"}
    Keys are compared case-insensitively because directory group names are not
    consistently cased across providers. An unmapped value is IGNORED, never guessed --
    a group nobody mapped must not silently become a role.
    """
    raw = os.environ.get("PULSETWIN_ROLE_MAP", "").strip()
    if not raw:
        # Identity mapping over PulseTwin's own role names, so a provider configured to
        # emit them directly needs no table at all.
        return {role: role for role in ROLES}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"PULSETWIN_ROLE_MAP is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("PULSETWIN_ROLE_MAP must be a JSON object")
    table = {}
    for key, value in parsed.items():
        role = str(value).strip().lower()
        if role not in ROLES:
            raise RuntimeError(
                f"PULSETWIN_ROLE_MAP maps '{key}' to unknown role '{value}'; "
                f"must be one of {ROLES}"
            )
        table[str(key).strip().lower()] = role
    return table


def load_config() -> JwtConfig:
    default_role = os.environ.get("PULSETWIN_JWT_DEFAULT_ROLE", "").strip().lower() or None
    if default_role and default_role not in ROLES:
        raise RuntimeError(f"PULSETWIN_JWT_DEFAULT_ROLE must be one of {ROLES}")
    algorithms = tuple(
        a.strip() for a in os.environ.get("PULSETWIN_JWT_ALGORITHMS", "RS256,HS256").split(",")
        if a.strip()
    )
    return JwtConfig(
        issuer=os.environ.get("PULSETWIN_JWT_ISSUER", "").strip() or None,
        audience=os.environ.get("PULSETWIN_JWT_AUDIENCE", "").strip() or None,
        secret=os.environ.get("PULSETWIN_JWT_SECRET", "").strip() or None,
        jwks_url=os.environ.get("PULSETWIN_JWT_JWKS_URL", "").strip() or None,
        algorithms=algorithms,
        role_claim=os.environ.get("PULSETWIN_JWT_ROLE_CLAIM", "roles").strip() or "roles",
        subject_claim=os.environ.get("PULSETWIN_JWT_SUBJECT_CLAIM", "sub").strip() or "sub",
        role_map=_role_map_from_env(),
        default_role=default_role,
    )


def validate_config() -> list[str]:
    """Fail-closed startup check. Returns warnings; RAISES on anything unsafe.

    Called from serve/main.py's lifespan. A production posture that cannot verify a
    signature is a configuration error, not a warning -- it would accept any token.
    """
    warnings: list[str] = []
    if not is_production_auth():
        warnings.append(
            "PULSETWIN_AUTH_MODE=dev: the X-PulseTwin-Role header is trusted as sent. "
            "Set PULSETWIN_AUTH_MODE=jwt for any deployment reachable by anyone else."
        )
        return warnings

    cfg = load_config()
    if not cfg.verifies:
        raise RuntimeError(
            "PULSETWIN_AUTH_MODE=jwt requires a verification key: set "
            "PULSETWIN_JWT_JWKS_URL (OIDC provider) or PULSETWIN_JWT_SECRET (HS256). "
            "Refusing to start -- without one, every token would be accepted unverified."
        )
    try:
        from jose import jwt  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "PULSETWIN_AUTH_MODE=jwt requires python-jose. Add it to "
            "serve/requirements.txt (it is on the import-wall allowlist, ADR-033)."
        ) from exc

    if not cfg.issuer:
        warnings.append("PULSETWIN_JWT_ISSUER is unset; the issuer claim will not be checked.")
    if not cfg.audience:
        warnings.append("PULSETWIN_JWT_AUDIENCE is unset; the audience claim will not be checked.")
    return warnings


# ------------------------------------------------------------------------------- JWKS


def _fetch_jwks(url: str) -> dict:
    cached = _JWKS_CACHE.get(url)
    if cached and time.time() - cached[0] < JWKS_TTL_S:
        return cached[1]
    # Only https, and only the configured URL. This is a server-side fetch of a
    # well-known document, not user-controlled input.
    if not url.startswith("https://"):
        raise RuntimeError("PULSETWIN_JWT_JWKS_URL must be https")
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - scheme checked
        payload = json.loads(response.read().decode("utf-8"))
    _JWKS_CACHE[url] = (time.time(), payload)
    return payload


def _clear_jwks_cache() -> None:
    """Test hook. Never called by request handling."""
    _JWKS_CACHE.clear()


# ----------------------------------------------------------------------- verification


@dataclass(frozen=True)
class VerifiedIdentity:
    subject: str
    role: str
    claims: dict


def _roles_from_claims(claims: dict, cfg: JwtConfig) -> list[str]:
    raw = claims.get(cfg.role_claim)
    if raw is None:
        return []
    if isinstance(raw, str):
        # Providers emit either a list or a space/comma separated string.
        return [part for part in raw.replace(",", " ").split() if part]
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw]
    return []


def map_role(values: list[str], cfg: JwtConfig) -> str | None:
    """Highest PulseTwin role any claim value maps to. Unmapped values are ignored.

    Taking the HIGHEST rather than the first is what makes a user in both
    'Plant-Managers' and 'PulseTwin-Admins' a system user regardless of claim ordering,
    which providers do not guarantee.
    """
    best: str | None = None
    for value in values:
        role = cfg.role_map.get(str(value).strip().lower())
        if role and (best is None or ROLES.index(role) > ROLES.index(best)):
            best = role
    return best or cfg.default_role


def verify_bearer(token: str, cfg: JwtConfig | None = None) -> VerifiedIdentity:
    """Verify a JWT and map it onto a PulseTwin role. Raises HTTPException(401) on failure.

    Every failure path returns 401 with a short reason and logs nothing sensitive. The
    token itself is never logged -- it is a bearer credential.
    """
    cfg = cfg or load_config()
    if not cfg.verifies:
        raise HTTPException(500, "server is not configured to verify tokens")

    try:
        from jose import jwt
        from jose.exceptions import ExpiredSignatureError, JWTError
    except ImportError as exc:  # pragma: no cover - validate_config catches this at boot
        raise HTTPException(500, "JWT verification unavailable") from exc

    key: object
    if cfg.jwks_url:
        try:
            key = _fetch_jwks(cfg.jwks_url)
        except Exception as exc:
            log.warning("JWKS fetch failed: %s", exc)
            raise HTTPException(503, "identity provider keys unavailable") from exc
    else:
        key = cfg.secret

    options = {
        "verify_aud": bool(cfg.audience),
        "verify_iss": bool(cfg.issuer),
    }
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=list(cfg.algorithms),
            audience=cfg.audience,
            issuer=cfg.issuer,
            options=options,
        )
    except ExpiredSignatureError as exc:
        raise HTTPException(401, "token expired") from exc
    except JWTError as exc:
        raise HTTPException(401, f"invalid token: {exc}") from exc

    subject = str(claims.get(cfg.subject_claim) or "").strip()
    if not subject:
        raise HTTPException(401, f"token carries no '{cfg.subject_claim}' claim")

    role = map_role(_roles_from_claims(claims, cfg), cfg)
    if role is None:
        raise HTTPException(
            403,
            "no claim in this token maps to a PulseTwin role; check PULSETWIN_ROLE_MAP",
        )
    return VerifiedIdentity(subject=subject, role=role, claims=claims)


def bearer_token(headers) -> str | None:
    """Extract a bearer token from an Authorization header, if present and well-formed."""
    raw = headers.get("Authorization") or headers.get("authorization") or ""
    parts = raw.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
        return parts[1].strip()
    return None


# ------------------------------------------------------------------------------ CORS


def cors_origins() -> list[str]:
    """Allowed browser origins.

    `PULSETWIN_CORS_ORIGINS` is a comma-separated list. In dev mode an unset value means
    `*`, which is what a judged local demo needs. In jwt mode a wildcard is REFUSED:
    `*` cannot be combined with credentialed requests anyway, and a production API that
    echoes every origin is an invitation.
    """
    raw = os.environ.get("PULSETWIN_CORS_ORIGINS", "").strip()
    if not raw:
        if is_production_auth():
            raise RuntimeError(
                "PULSETWIN_AUTH_MODE=jwt requires PULSETWIN_CORS_ORIGINS to name the "
                "exact browser origins allowed, e.g. https://pulsetwin.example.com"
            )
        return ["*"]
    origins = [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]
    if "*" in origins and is_production_auth():
        raise RuntimeError(
            "PULSETWIN_CORS_ORIGINS='*' is refused in jwt mode; list the exact origins"
        )
    return origins
