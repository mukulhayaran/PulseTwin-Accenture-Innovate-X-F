"""The ONLY file in the repo that imports the provider SDK.

Swapping providers touches this file and `genai/config.py::MODEL_ID`, and nothing else.
The verifier, the tool registry, the loop's recording discipline and the persistence
layer are all provider-independent by construction.
"""

from __future__ import annotations

import os

from genai import config


def credential_name() -> str | None:
    """Which environment variable actually holds the key, or None.

    The NAME is safe to report; the value never is.
    """
    for name in config.KEY_ENVS:
        if os.environ.get(name, "").strip():
            return name
    return None


def credential_value() -> str:
    name = credential_name()
    return os.environ[name].strip() if name else ""


def credential_present() -> bool:
    """True iff a provider key is configured. Reports PRESENCE only.

    Never returns, logs or persists the value, a prefix, or its length -- a run's
    transcript is stored in the ops DB and rendered in the Ops view.
    """
    return credential_name() is not None


def availability() -> dict[str, object]:
    """What `GET /api/triage/availability` reports to the UI."""
    if credential_present():
        return {"enabled": True, "reason": "ok", "provider": config.PROVIDER,
                "model": config.MODEL_ID, "mode": "agent",
                # The NAME of the variable in use, never its value -- useful when three
                # are supported and the operator needs to know which one was picked up.
                "credential_env": credential_name()}
    return {"enabled": False, "reason": "no_provider_credential", "provider": None,
            "model": None, "mode": "deterministic"}


def build_client():
    """Construct the provider client. Raises RuntimeError if no credential is set.

    Groq speaks the OpenAI wire protocol, so the `openai` package talks to it with only
    a base_url change (https://console.groq.com/docs/openai). What Groq does NOT
    guarantee is parameter parity -- strict json_schema, `seed` and strict tool schemas
    are all model-dependent and a rejection is an HTTP 400, not a soft ignore. That is
    handled by the degradation ladder in `genai/capabilities.py`, not here.
    """
    if not credential_present():
        raise RuntimeError(
            f"no provider credential -- set {config.API_KEY_ENV}, or run the "
            f"deterministic path (genai.offline_triage), which needs none."
        )
    import openai  # imported here so the module loads without the dependency installed

    return openai.OpenAI(
        api_key=credential_value(),
        base_url=config.BASE_URL,
        timeout=config.REQUEST_TIMEOUT_S,
        max_retries=0,          # retries are handled in the loop, with a hard bound
    )


def classify_error(exc: BaseException) -> str | None:
    """Map a provider exception to a stable reason code, most-specific first.

    APITimeoutError is checked BEFORE APIConnectionError because it subclasses it --
    reversing them silently swallows every timeout into "unreachable".
    Returns None if `exc` is not a provider error.
    """
    try:
        import openai
    except ImportError:
        return None

    if isinstance(exc, openai.RateLimitError):
        return "rate_limited"
    # A 400 whose body says `tool_use_failed` is NOT a malformed request from us -- it is
    # the provider rejecting a tool call the MODEL generated badly. Groq validates tool
    # syntax server-side and returns, verbatim, the text it could not parse:
    #
    #   {"code": "tool_use_failed", "failed_generation":
    #    "{\"name\": \"citaton\", \"arguments\": The current bottleneck in ..."}
    #
    # Distinguishing it matters because `genai/capabilities.py` degrades the REQUEST on a
    # 400, and no amount of degrading fixes a model that invented a tool name. Left
    # unclassified it burned a ladder rung per occurrence and reported a misleading
    # `capability_profile` against the run.
    if _is_tool_use_failure(exc):
        return "model_tool_call_invalid"
    if isinstance(exc, openai.APITimeoutError):
        return "provider_timeout"
    if isinstance(exc, openai.APIStatusError):
        return f"provider_status_{exc.status_code}"
    if isinstance(exc, openai.APIConnectionError):
        return "provider_unreachable"
    if isinstance(exc, openai.OpenAIError):
        return "provider_error"
    return None


def retry_after_s(exc: BaseException) -> float | None:
    """How long the provider says to wait, in seconds, or None.

    Reads `retry-after` first, then `x-ratelimit-reset-tokens` / `-requests`, which Groq
    returns as a duration string like `17.145s` or `8m38.4s` rather than a number. A
    fixed local guess is worse than the endpoint's own answer: measured on Groq's free
    tier the token budget resets in ~17s while the loop used to sleep 5 and give up.

    Never raises -- a malformed header must not end a run that was only rate limited.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None

    for name in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        raw = headers.get(name)
        if not raw:
            continue
        try:
            return _parse_duration(str(raw))
        except (TypeError, ValueError):
            continue
    return None


def _parse_duration(text: str) -> float:
    """`30`, `17.145s`, `8m38.4s`, `1h2m3s` -> seconds."""
    text = text.strip().lower()
    if not text:
        raise ValueError("empty duration")

    try:
        return float(text)          # bare seconds, e.g. Retry-After: 30
    except ValueError:
        pass

    total = 0.0
    number = ""
    matched = False
    for ch in text:
        if ch.isdigit() or ch == ".":
            number += ch
        elif ch in ("h", "m", "s"):
            if not number:
                raise ValueError(f"unit {ch!r} with no number in {text!r}")
            total += float(number) * {"h": 3600.0, "m": 60.0, "s": 1.0}[ch]
            number = ""
            matched = True
        else:
            raise ValueError(f"unexpected character {ch!r} in {text!r}")
    if number or not matched:
        raise ValueError(f"unparsed remainder in {text!r}")
    return total


def _is_tool_use_failure(exc: BaseException) -> bool:
    """True if a 400 is the provider rejecting the MODEL's tool call, not our request."""
    status = getattr(exc, "status_code", None)
    if status not in (400, 422):
        return False
    body = str(exc).lower()
    return "tool_use_failed" in body or "failed to parse tool call" in body


def failed_generation(exc: BaseException) -> str:
    """The text the provider could not parse as a tool call, for the run's error field.

    Worth persisting: "the model tried to call a tool named `citaton`" is a diagnosable
    fact, and `provider_status_400` is not.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            text = err.get("failed_generation")
            if isinstance(text, str):
                return text[:400]
    return ""
