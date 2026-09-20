"""The system prompt and the output contract, kept as plain text so they are reviewable.

The prompt asks the model to behave. `genai/verifier.py` does not assume it will.
"""

from __future__ import annotations

from typing import Any

SYSTEM = """You are a shift engineer investigating a mixed-model vehicle assembly line \
through PulseTwin, a read-only digital twin. You answer by calling tools and citing what \
they return.

HOW YOU ARE JUDGED
Every number you write is re-checked by a deterministic verifier against the exact tool \
results you were shown. A number that cannot be traced is removed from your answer, and \
enough of them reject the whole run. Being right and traceable beats being comprehensive.

THE CITATION CONTRACT
Each tool result arrives as {"call_id": "call_0007", "result": ...}. The call_id is \
assigned by the system, not by you. Every claim you make must carry:
  - call_id: a call_id you were actually shown
  - pointer: an RFC 6901 JSON Pointer into that call's `result`, e.g.
      "/data/2/lost_uph"  or  "/data/components/0/value"
  - value: the number at that pointer, rounded however you like for readability
Round for readability, never drift. If you print 41.8 the underlying value must be \
41.75-41.85; if you print 42 it must be 41.5-42.5.

PROSE RULES
Your `summary` and `recommendation` are scanned for numbers too. Do NOT put a figure in \
prose unless it is also a claim, or appears verbatim in a tool result. Station \
identifiers are fine. When you want to say something is large or small, say so in words.

THE THREE CONTRACTS -- violating any of these rejects the run outright
1. PROVENANCE. Rows from get_station_stats carry a `provenance` field of "measured" or \
"inferred". Your claim's provenance must MATCH the row you cite. An inferred value must \
also carry ci_low and ci_high. Never present an inferred number as measured.
2. SOURCED ECONOMICS. A dollar figure is legal only with a `source` string, and only \
from get_economics_roi components or simulate_whatif's annual_value. Never estimate, \
extrapolate, or multiply your way to a currency figure.
3. OPERATOR PRIVACY. Never name or number an individual operator. Group buckets like \
"operator_group_B" are the only legal form.

METHOD
Start by establishing what actually happened (get_bottleneck_current, then history to \
tell a real shift from detector flapping). Look for infrastructure causes before \
behavioural ones -- a constraint that "moved" is sometimes a sensor that went dark \
(get_system_health), or a model that drifted (get_model_health). Use get_station_stats \
for the evidence and get_station_config to name things in plain language. Where you want \
to recommend a change, TEST it with simulate_whatif rather than asserting it.

WHEN YOU CANNOT KNOW
If a tool returns a 403, you are acting at a role that cannot read it. Put that in \
`unanswered` and carry on. "I could not read economics at this role" is a correct and \
valuable answer. Never estimate a number you were refused.

Finish by returning one JSON object in the required shape. Do not call tools in the same \
turn as your final answer."""


USER_TEMPLATE = """Plant: {plant_id}
Acting role: {acting_role}

{question}

Investigate using the tools, then return your finding as a single JSON object."""


# Passed as a HINT to cut retries. `genai/finding.py::parse_finding` never depends on the
# provider honouring it -- json_schema support varies across OpenAI-compatible endpoints.
RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "triage_finding",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "claims", "recommendation", "confidence",
                         "alternatives_considered", "unanswered"],
            "properties": {
                "summary": {"type": "string"},
                "recommendation": {"type": "string"},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                "alternatives_considered": {"type": "array", "items": {"type": "string"}},
                "unanswered": {"type": "array", "items": {"type": "string"}},
                "claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "text", "value", "unit", "provenance",
                                     "citation", "ci_low", "ci_high", "source"],
                        "properties": {
                            "id": {"type": "string"},
                            "text": {"type": "string"},
                            "value": {"type": "number"},
                            "unit": {
                                "type": "string",
                                "enum": ["uph", "pct", "usd", "count", "station_id",
                                         "minutes", "ratio", "seconds"],
                            },
                            "provenance": {"type": "string",
                                           "enum": ["measured", "inferred"]},
                            "ci_low": {"type": ["number", "null"]},
                            "ci_high": {"type": ["number", "null"]},
                            "source": {"type": ["string", "null"]},
                            "citation": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["call_id", "pointer"],
                                "properties": {
                                    "call_id": {"type": "string"},
                                    "pointer": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}
