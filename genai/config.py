"""Every tunable constant for the GenAI tier, in one reviewable file.

No secrets live here -- the provider credential is read from the environment by
`genai/client.py` and never persisted, logged, or written to a transcript.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------- provider
#
# xAI Grok, whose API is OpenAI-compatible, so the `openai` client talks to it directly.
# Verified against https://docs.x.ai/docs/models : grok-4.6 is the current recommended
# model for code/chat, 500k context. The ID lives HERE and nowhere else -- swapping
# providers touches this constant and `genai/client.py`, and nothing else in the package.
BASE_URL = os.environ.get("PULSETWIN_GENAI_BASE_URL", "https://api.x.ai/v1")
MODEL_ID = os.environ.get("PULSETWIN_GENAI_MODEL", "grok-4.6")
PROVIDER = "xai"
API_KEY_ENV = "XAI_API_KEY"

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
MAX_ITERATIONS = 12
MAX_TOOL_CALLS = 24

# A single tool result must not be able to blow the context or the bill.
# `get_bottleneck_history` can return 700 rows.
MAX_TOOL_RESULT_BYTES = 24_576

REQUEST_TIMEOUT_S = 90.0
RUN_WALLCLOCK_CAP_S = 300.0

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
    "MAX_ITERATIONS", "MAX_TOOL_CALLS", "MAX_TOOL_RESULT_BYTES",
    "REQUEST_TIMEOUT_S", "RUN_WALLCLOCK_CAP_S", "TEMPERATURE", "SEED",
    "MAX_REDACTED_CLAIM_RATIO",
]
