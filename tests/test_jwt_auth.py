"""Production authentication posture (ADR-049).

The property under test is narrow and important: **in production mode, a self-declared
role header grants nothing.** Everything else here supports that -- signature verification,
expiry, issuer/audience binding, role mapping, and the fail-closed startup check that stops
a misconfigured deployment from booting into an "allow everything" state.

Every test sets its environment through monkeypatch, so the default dev posture is restored
afterwards and the rest of the suite is unaffected.
"""

from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from serve.middleware import jwt_auth

jose_jwt = pytest.importorskip("jose.jwt", reason="python-jose is only needed for jwt mode")

SECRET = "test-secret-not-used-anywhere-real"
ISSUER = "https://idp.example.com/"
AUDIENCE = "pulsetwin-api"


def make_token(
    roles=("PulseTwin-Admins",),
    subject="alice@example.com",
    expires_in=3600,
    issuer=ISSUER,
    audience=AUDIENCE,
    secret=SECRET,
    role_claim="roles",
):
    now = int(time.time())
    claims = {
        "sub": subject,
        "iat": now,
        "exp": now + expires_in,
        "iss": issuer,
        "aud": audience,
        role_claim: list(roles),
    }
    return jose_jwt.encode(claims, secret, algorithm="HS256")


@pytest.fixture
def jwt_mode(monkeypatch):
    """Configure a verifiable HS256 posture."""
    monkeypatch.setenv("PULSETWIN_AUTH_MODE", "jwt")
    monkeypatch.setenv("PULSETWIN_JWT_SECRET", SECRET)
    monkeypatch.setenv("PULSETWIN_JWT_ISSUER", ISSUER)
    monkeypatch.setenv("PULSETWIN_JWT_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("PULSETWIN_JWT_ALGORITHMS", "HS256")
    monkeypatch.setenv("PULSETWIN_CORS_ORIGINS", "https://pulsetwin.example.com")
    monkeypatch.setenv(
        "PULSETWIN_ROLE_MAP",
        '{"PulseTwin-Admins":"system","Plant-Managers":"manager",'
        '"Line-Leads":"supervisor","Execs":"leadership"}',
    )
    jwt_auth._clear_jwks_cache()
    return jwt_auth.load_config()


# ------------------------------------------------------------------------------ modes


def test_dev_is_the_default_and_is_announced(monkeypatch):
    monkeypatch.delenv("PULSETWIN_AUTH_MODE", raising=False)
    assert jwt_auth.auth_mode() == "dev"
    assert jwt_auth.is_production_auth() is False
    warnings = jwt_auth.validate_config()
    assert any("dev" in w for w in warnings), "dev mode must warn that the header is trusted"


def test_unknown_mode_is_dev_not_a_silent_third_state(monkeypatch):
    monkeypatch.setenv("PULSETWIN_AUTH_MODE", "production")  # not a valid value
    assert jwt_auth.auth_mode() == "dev"


def test_jwt_mode_without_a_key_refuses_to_start(monkeypatch):
    """Fail closed. A production posture that cannot verify would accept any token."""
    monkeypatch.setenv("PULSETWIN_AUTH_MODE", "jwt")
    monkeypatch.delenv("PULSETWIN_JWT_SECRET", raising=False)
    monkeypatch.delenv("PULSETWIN_JWT_JWKS_URL", raising=False)
    with pytest.raises(RuntimeError, match="requires a verification key"):
        jwt_auth.validate_config()


def test_jwt_mode_with_a_key_starts(jwt_mode):
    warnings = jwt_auth.validate_config()
    assert isinstance(warnings, list)


# ------------------------------------------------------------------------ verification


def test_valid_token_maps_to_a_role(jwt_mode):
    identity = jwt_auth.verify_bearer(make_token(roles=("Plant-Managers",)), jwt_mode)
    assert identity.role == "manager"
    assert identity.subject == "alice@example.com"


def test_expired_token_is_rejected(jwt_mode):
    with pytest.raises(HTTPException) as exc:
        jwt_auth.verify_bearer(make_token(expires_in=-60), jwt_mode)
    assert exc.value.status_code == 401
    assert "expired" in str(exc.value.detail).lower()


def test_token_signed_with_the_wrong_key_is_rejected(jwt_mode):
    with pytest.raises(HTTPException) as exc:
        jwt_auth.verify_bearer(make_token(secret="a-different-secret"), jwt_mode)
    assert exc.value.status_code == 401


def test_wrong_issuer_is_rejected(jwt_mode):
    with pytest.raises(HTTPException) as exc:
        jwt_auth.verify_bearer(make_token(issuer="https://attacker.example/"), jwt_mode)
    assert exc.value.status_code == 401


def test_wrong_audience_is_rejected(jwt_mode):
    with pytest.raises(HTTPException) as exc:
        jwt_auth.verify_bearer(make_token(audience="some-other-api"), jwt_mode)
    assert exc.value.status_code == 401


def test_garbage_is_rejected(jwt_mode):
    with pytest.raises(HTTPException) as exc:
        jwt_auth.verify_bearer("not-a-jwt", jwt_mode)
    assert exc.value.status_code == 401


def test_token_with_no_subject_is_rejected(jwt_mode):
    with pytest.raises(HTTPException) as exc:
        jwt_auth.verify_bearer(make_token(subject=""), jwt_mode)
    assert exc.value.status_code == 401


def test_token_whose_groups_map_to_nothing_is_403_not_a_default_role(jwt_mode):
    """An unmapped directory group must not silently become a role."""
    with pytest.raises(HTTPException) as exc:
        jwt_auth.verify_bearer(make_token(roles=("Some-Unrelated-Group",)), jwt_mode)
    assert exc.value.status_code == 403


# ------------------------------------------------------------------------ role mapping


def test_highest_role_wins_regardless_of_claim_order(jwt_mode):
    """Providers do not guarantee claim ordering, so first-match would be a coin flip."""
    a = jwt_auth.verify_bearer(make_token(roles=("Plant-Managers", "PulseTwin-Admins")), jwt_mode)
    b = jwt_auth.verify_bearer(make_token(roles=("PulseTwin-Admins", "Plant-Managers")), jwt_mode)
    assert a.role == b.role == "system"


def test_role_mapping_is_case_insensitive(jwt_mode):
    identity = jwt_auth.verify_bearer(make_token(roles=("plant-managers",)), jwt_mode)
    assert identity.role == "manager"


def test_space_separated_string_claim_is_accepted(jwt_mode):
    """Some providers emit a space-delimited string rather than a list."""
    now = int(time.time())
    token = jose_jwt.encode(
        {"sub": "bob", "iat": now, "exp": now + 600, "iss": ISSUER, "aud": AUDIENCE,
         "roles": "Line-Leads Plant-Managers"},
        SECRET, algorithm="HS256",
    )
    assert jwt_auth.verify_bearer(token, jwt_mode).role == "manager"


def test_configurable_role_claim(monkeypatch, jwt_mode):
    monkeypatch.setenv("PULSETWIN_JWT_ROLE_CLAIM", "groups")
    cfg = jwt_auth.load_config()
    token = make_token(roles=("Execs",), role_claim="groups")
    assert jwt_auth.verify_bearer(token, cfg).role == "leadership"


def test_role_map_rejects_an_unknown_target_role(monkeypatch):
    monkeypatch.setenv("PULSETWIN_ROLE_MAP", '{"Group":"superuser"}')
    with pytest.raises(RuntimeError, match="unknown role"):
        jwt_auth.load_config()


def test_role_map_rejects_invalid_json(monkeypatch):
    monkeypatch.setenv("PULSETWIN_ROLE_MAP", "{not json")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        jwt_auth.load_config()


# ------------------------------------------------------- the header is not a credential


def test_bearer_extraction():
    assert jwt_auth.bearer_token({"Authorization": "Bearer abc.def.ghi"}) == "abc.def.ghi"
    assert jwt_auth.bearer_token({"authorization": "bearer xyz"}) == "xyz"
    assert jwt_auth.bearer_token({"Authorization": "Basic abc"}) is None
    assert jwt_auth.bearer_token({"Authorization": "Bearer"}) is None
    assert jwt_auth.bearer_token({}) is None


def _request(headers: dict):
    """A minimal Request stand-in: get_role reads only .headers and writes .state."""
    class _State:
        pass

    class _Request:
        def __init__(self, headers):
            self.headers = headers
            self.state = _State()

    return _Request(headers)


def test_production_mode_refuses_a_bare_role_header(jwt_mode):
    """THE test. In jwt mode `X-PulseTwin-Role: system` must grant nothing."""
    from serve.middleware.auth import get_role

    with pytest.raises(HTTPException) as exc:
        get_role(_request({"X-PulseTwin-Role": "system"}))
    assert exc.value.status_code == 401
    assert "not a credential" in str(exc.value.detail)


def test_production_mode_refuses_an_anonymous_request(jwt_mode):
    from serve.middleware.auth import get_role

    with pytest.raises(HTTPException) as exc:
        get_role(_request({}))
    assert exc.value.status_code == 401


def test_production_mode_accepts_a_verified_bearer(jwt_mode):
    from serve.middleware.auth import get_role

    request = _request({"Authorization": f"Bearer {make_token(roles=('Plant-Managers',))}"})
    assert get_role(request) == "manager"
    assert request.state.session_user == "alice@example.com"
    assert request.state.auth_mode == "jwt"


def test_a_header_may_drop_privilege_but_never_raise_it(jwt_mode):
    """A page dropping to a lower persona is legitimate; climbing is not."""
    from serve.middleware.auth import get_role

    token = make_token(roles=("Plant-Managers",))  # -> manager
    lower = _request({"Authorization": f"Bearer {token}", "X-PulseTwin-Role": "supervisor"})
    assert get_role(lower) == "supervisor"

    higher = _request({"Authorization": f"Bearer {token}", "X-PulseTwin-Role": "system"})
    assert get_role(higher) == "manager", "a role header escalated above the verified role"


def test_dev_mode_still_honours_the_header(monkeypatch):
    """The demo posture is unchanged -- that is what makes it safe to keep as default."""
    monkeypatch.setenv("PULSETWIN_AUTH_MODE", "dev")
    from serve.middleware.auth import get_role

    assert get_role(_request({"X-PulseTwin-Role": "system"})) == "system"
    assert get_role(_request({})) == "supervisor"


# ------------------------------------------------------------------------------ CORS


def test_cors_defaults_to_wildcard_in_dev(monkeypatch):
    monkeypatch.setenv("PULSETWIN_AUTH_MODE", "dev")
    monkeypatch.delenv("PULSETWIN_CORS_ORIGINS", raising=False)
    assert jwt_auth.cors_origins() == ["*"]


def test_cors_must_be_configured_in_jwt_mode(monkeypatch):
    monkeypatch.setenv("PULSETWIN_AUTH_MODE", "jwt")
    monkeypatch.setenv("PULSETWIN_JWT_SECRET", SECRET)
    monkeypatch.delenv("PULSETWIN_CORS_ORIGINS", raising=False)
    with pytest.raises(RuntimeError, match="PULSETWIN_CORS_ORIGINS"):
        jwt_auth.cors_origins()


def test_cors_wildcard_is_refused_in_jwt_mode(monkeypatch):
    monkeypatch.setenv("PULSETWIN_AUTH_MODE", "jwt")
    monkeypatch.setenv("PULSETWIN_JWT_SECRET", SECRET)
    monkeypatch.setenv("PULSETWIN_CORS_ORIGINS", "https://a.example.com,*")
    with pytest.raises(RuntimeError, match="refused in jwt mode"):
        jwt_auth.cors_origins()


def test_cors_list_is_parsed_and_normalised(monkeypatch):
    monkeypatch.setenv("PULSETWIN_AUTH_MODE", "jwt")
    monkeypatch.setenv("PULSETWIN_JWT_SECRET", SECRET)
    monkeypatch.setenv(
        "PULSETWIN_CORS_ORIGINS", " https://a.example.com/ , https://b.example.com "
    )
    assert jwt_auth.cors_origins() == ["https://a.example.com", "https://b.example.com"]


# ------------------------------------------------------------------------------- JWKS


def test_jwks_url_must_be_https(monkeypatch):
    monkeypatch.setenv("PULSETWIN_AUTH_MODE", "jwt")
    monkeypatch.setenv("PULSETWIN_JWT_JWKS_URL", "http://idp.example.com/keys")
    monkeypatch.delenv("PULSETWIN_JWT_SECRET", raising=False)
    jwt_auth._clear_jwks_cache()
    with pytest.raises(RuntimeError, match="must be https"):
        jwt_auth._fetch_jwks("http://idp.example.com/keys")


def test_no_vendor_is_hardcoded():
    """Vendor neutrality is a design claim; keep it true.

    Providers ARE named in the module docstring, deliberately -- saying which ones fit is
    the useful part. What must not happen is a provider name reaching executable code: a
    hardcoded issuer host, a vendor-specific claim name, a special case. So strip every
    docstring via the AST and inspect what is left, rather than filtering lines, which is
    what the first version of this test did wrong.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(jwt_auth.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]

    code = ast.unparse(tree).lower()  # comments are already gone -- the AST drops them
    for vendor in ("okta", "auth0", "azure", "entra", "cognito", "keycloak"):
        assert vendor not in code, f"{vendor} appears in executable code, not just prose"
