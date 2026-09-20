"""The ONLY file in the repo that imports the provider SDK.

Swapping providers touches this file and `genai/config.py::MODEL_ID`, and nothing else.
The verifier, the tool registry, the loop's recording discipline and the persistence
layer are all provider-independent by construction.
"""

from __future__ import annotations

import os

from genai import config


def credential_present() -> bool:
    """True iff a provider key is configured. Reports PRESENCE only.

    Never returns, logs or persists the value, a prefix, or its length -- a run's
    transcript is stored in the ops DB and rendered in the Ops view.
    """
    return bool(os.environ.get(config.API_KEY_ENV, "").strip())


def availability() -> dict[str, object]:
    """What `GET /api/triage/availability` reports to the UI."""
    if credential_present():
        return {"enabled": True, "reason": "ok", "provider": config.PROVIDER,
                "model": config.MODEL_ID, "mode": "agent"}
    return {"enabled": False, "reason": "no_provider_credential", "provider": None,
            "model": None, "mode": "deterministic"}


def build_client():
    """Construct the provider client. Raises RuntimeError if no credential is set.

    xAI Grok speaks the OpenAI wire protocol, so the `openai` package talks to it with
    only a base_url change (https://docs.x.ai/docs/overview).
    """
    if not credential_present():
        raise RuntimeError(
            f"{config.API_KEY_ENV} is not set -- run the deterministic path instead "
            f"(genai.offline_triage), which needs no credential."
        )
    import openai  # imported here so the module loads without the dependency installed

    return openai.OpenAI(
        api_key=os.environ[config.API_KEY_ENV],
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
    if isinstance(exc, openai.APITimeoutError):
        return "provider_timeout"
    if isinstance(exc, openai.APIStatusError):
        return f"provider_status_{exc.status_code}"
    if isinstance(exc, openai.APIConnectionError):
        return "provider_unreachable"
    if isinstance(exc, openai.OpenAIError):
        return "provider_error"
    return None
