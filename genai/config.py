"""Every tunable constant for the GenAI tier, in one reviewable file.

No secrets live here -- the provider credential is read from the environment by
`genai/client.py` and never persisted, logged, or written to a transcript.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------- provider
#
# OpenRouter, whose API is OpenAI-compatible, so the `openai` client talks to it with
# only a base_url change. The ID lives HERE and nowhere else -- swapping providers
# touches this constant and `genai/client.py`, and nothing else in the package. ADR-038
# records why the provider changed off the original one and why no second SDK was added:
# every endpoint here speaks the same wire protocol, so the import wall's ban on a
# second provider dependency holds unchanged across all of these swaps.
#
# These are DEFAULTS, and they matter more than defaults usually do: nothing in this repo
# auto-loads a .env file, so an operator who exports nothing runs on exactly these.
#
# TWO measured properties of `nvidia/nemotron-3-ultra-550b-a55b:free`, both of which cost
# a debugging session on 2026-09-21 and are handled elsewhere rather than here:
#   * It ACCEPTS `response_format` alongside `tools` with HTTP 200 and then emits no tool
#     call at all. See `capabilities.suppresses_tool_calls` -- the ladder's 400-driven
#     degradation never fires on this endpoint, so a soft probe does it instead.
#   * The `:free` variant is capped at 50 requests PER DAY across all free models on
#     OpenRouter, and one agent run spends 7-12 of them. Four or five runs exhaust a day.
#     Drop the `:free` suffix (or fund the account) for a demo that must not run dry;
#     `genai/offline_triage.py` needs no provider at all and passes the same verifier.
#
# `serve/routers/triage.py::API_KEY_ENV` MIRRORS the name below by hand (the import wall
# forbids importing this module into serve/). Changing one without the other is a bug.
BASE_URL = os.environ.get("PULSETWIN_GENAI_BASE_URL", "https://openrouter.ai/api/v1")
MODEL_ID = os.environ.get(
    "PULSETWIN_GENAI_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
PROVIDER = os.environ.get("PULSETWIN_GENAI_PROVIDER", "openrouter")

# The credential variable is PROVIDER-NEUTRAL on purpose.
#
# A provider is a base_url plus a model id. Naming the credential after one of them made
# every swap a code edit in two files -- here and the hand-maintained mirror in
# `serve/routers/triage.py` -- for no gain. With this name, moving between any two
# OpenAI-compatible endpoints is three environment variables and no diff.
#
# LEGACY_KEY_ENVS is read as a fallback so an existing setup that exported a
# provider-named variable keeps working. Order is irrelevant: the first one SET wins, and
# having two set at once is a configuration the operator chose.
API_KEY_ENV = "PULSETWIN_GENAI_API_KEY"
# ORDER MATTERS AND IS A TRAP. `client.credential_name()` takes the first name that is
# SET, so with both GROQ_API_KEY and OPENROUTER_API_KEY exported the Groq key is sent to
# OpenRouter and every run dies on a bare `provider_status_401` with no hint as to why.
# Set API_KEY_ENV above -- it is checked before all of these -- rather than relying on
# which legacy name happens to come first.
LEGACY_KEY_ENVS = ("GROQ_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY")
KEY_ENVS = (API_KEY_ENV, *LEGACY_KEY_ENVS)

# ------------------------------------------------------------------------------- loop
#
# TWO caps, because they fail differently and on purpose:
#   MAX_ITERATIONS  -- model round trips. Hitting it is TERMINAL and rejects the run.
#   MAX_TOOL_CALLS  -- total tool executions (one assistant turn may request several in
#                      parallel). Hitting it returns a budget error TO THE MODEL, which
#                      can then write its finding from the evidence it already has.
#
# Deliberately NOT "ask the model to summarise what it has" on an iteration cap: a
# summary produced under duress is the likeliest place an uncited number appears.
#
# MAX_ITERATIONS must stay ABOVE MAX_TOOL_CALLS, not below it. The nudge above only fires
# if the run is still going when it's reached -- a model that requests one tool call per
# round trip (measured: `nvidia/nemotron-3-ultra-550b-a55b:free` at L4, 2026-09-21, a
# "why is S12 elevated" question that hit the iteration cap at exactly 12/12 tool calls
# with zero claims) burns one iteration per call and NEVER reaches a 24-call budget at a
# 12-iteration cap. The nudge is unreachable and every broad question dies cold. 26 gives
# room for 24 one-at-a-time tool calls plus the round trip the nudge asks for plus one
# spare, and still finishes well inside RUN_WALLCLOCK_CAP_S (measured ~4.8s/iteration).
MAX_ITERATIONS = 26
MAX_TOOL_CALLS = 24

# A single tool result must not be able to blow the context or the bill.
# `get_bottleneck_history` can return 700 rows.
MAX_TOOL_RESULT_BYTES = 24_576

# How many earlier turns of a thread are replayed into a new turn's prompt, and how many
# claim texts each contributes. Bounded so a long conversation cannot slowly grow into a
# 40k-token prompt -- and so the cost of turn 12 is the cost of turn 2.
MAX_THREAD_TURNS = 4
MAX_THREAD_CLAIMS_PER_TURN = 8

REQUEST_TIMEOUT_S = 90.0
RUN_WALLCLOCK_CAP_S = 300.0

# Rate-limit backoff.
#
# MEASURED against Groq's free tier on 2026-09-21: `openai/gpt-oss-120b` allows 8,000
# TOKENS per minute, and one round trip of this agent costs ~3,200 tokens before any tool
# result is appended (a 2,853-char system prompt plus ~10,000 chars of tool schemas, both
# re-sent every turn). Two or three iterations therefore exhaust the budget mid-run.
#
# The old behaviour -- one retry, a flat 5-second sleep -- could not recover from that:
# the response header said the token budget resets in 17.1s. Now the loop reads the
# provider's own hint (`retry-after`, else `x-ratelimit-reset-tokens`) and waits that
# long, up to a cap, for a bounded number of attempts.
#
# Everything here is still bounded by RUN_WALLCLOCK_CAP_S, which is checked at the top of
# every iteration, so a provider that says "come back in an hour" ends the run rather
# than parking a detached worker for an hour.
# A model that emits an unparseable tool call has made a stochastic mistake, not a
# structural one. Retrying the same conversation can produce a valid call, so it gets a
# small bounded number of attempts -- but only a small one: if the model keeps inventing
# tool names, the prompt is wrong and a retry loop hides that.
MAX_TOOL_CALL_RETRIES = 2

MAX_RATE_LIMIT_RETRIES = 3
RATE_LIMIT_BACKOFF_S = 5.0        # fallback when the provider gives no hint
MAX_RATE_LIMIT_WAIT_S = 45.0      # never honour a hint longer than this

# temperature=0 and a fixed seed REDUCE variance; they do not make a hosted model
# reproducible, and this package never claims they do. What IS deterministic, and is
# asserted so by tests: the verifier, the tool dispatcher, and offline_triage.
TEMPERATURE = 0.0
SEED = 42

# ---------------------------------------------------------------------- verifier gate
#
# Transcription errors are graded; contract violations are categorical. A single C4
# failure rejects the whole run no matter how small a share of claims it is.
MAX_REDACTED_CLAIM_RATIO = 0.30

__all__ = [
    "BASE_URL", "MODEL_ID", "PROVIDER", "API_KEY_ENV",
    "LEGACY_KEY_ENVS", "KEY_ENVS",
    "MAX_ITERATIONS", "MAX_TOOL_CALLS", "MAX_TOOL_RESULT_BYTES",
    "MAX_THREAD_TURNS", "MAX_THREAD_CLAIMS_PER_TURN",
    "REQUEST_TIMEOUT_S", "RUN_WALLCLOCK_CAP_S", "TEMPERATURE", "SEED",
    "MAX_TOOL_CALL_RETRIES", "MAX_RATE_LIMIT_RETRIES", "RATE_LIMIT_BACKOFF_S", "MAX_RATE_LIMIT_WAIT_S",
    "MAX_REDACTED_CLAIM_RATIO",
]
