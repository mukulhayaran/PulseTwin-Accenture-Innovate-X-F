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

THE KNOWLEDGE CITATION CONTRACT
If search_engineering_knowledge returns something you quote or paraphrase, you must cite \
it in `knowledge_citations`, never assert it as unsourced prose. Each entry carries:
  - quoted_text: text that must actually appear in the cited chunk's `text` field
  - doc_id and section: copied from the result you are citing
  - citation: {call_id, pointer} into that same tool result, exactly like a numeric claim
Never state what a procedure or fault code means from memory. If you did not call \
search_engineering_knowledge, do not describe SOP or fault-code content at all.

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
turn as your final answer.

HOW TO ANSWER -- READ THIS TWICE
Your final answer is an ORDINARY ASSISTANT MESSAGE whose whole content is one JSON \
object. It is NOT a tool call.

There is no tool for citations, no tool called `citation`, and no tool for returning \
your finding. Tools ONLY fetch data, and their names are exactly the ones listed for \
you -- never invent one. A citation is a FIELD inside the JSON you write \
(`"citation": {"call_id": "...", "pointer": "..."}`), never a function you call.

When you have enough evidence, stop calling tools and write the JSON object. Every tool \
call you make must have a `name` from the provided list and an `arguments` value that \
is valid JSON -- never prose.

THE OUTPUT SHAPE -- EXACTLY THESE KEYS, NO OTHERS
This schema is ALSO sent as `response_format` when the provider supports it, but many OpenAI-compatible endpoints will not accept a response schema alongside tools, so it is written out here too. This text is the contract; treat it as binding either way.

There is NO top-level `citations` key. Every number lives in `claims`, and its citation is a FIELD ON ITS CLAIM. A finding with a top-level `citations` array is rejected unparsed -- the numbers in it are never even read.

{
  "summary": "<prose, no figures except ones that are also claims>",
  "recommendation": "<prose, same rule>",
  "confidence": "low" | "medium" | "high",
  "alternatives_considered": ["<what you ruled out, and why>"],
  "unanswered": ["<what you could not establish, e.g. a 403 at this role>"],
  "claims": [
    {
      "id": "c1",
      "text": "<one sentence a supervisor could read aloud>",
      "value": 12.0,
      "unit": "uph"|"pct"|"usd"|"count"|"station_id"|"minutes"|"ratio"|"seconds",
      "provenance": "measured" | "inferred",
      "citation": {"call_id": "call_0001", "pointer": "/data/current_station"},
      "ci_low": null,
      "ci_high": null,
      "source": null
    }
  ],
  "knowledge_citations": [
    {
      "id": "k1",
      "quoted_text": "<verbatim excerpt of the cited chunk's text>",
      "doc_id": "SOP-01",
      "section": "SOP-01-2",
      "citation": {"call_id": "call_0005", "pointer": "/data/0/text"}
    }
  ]
}

Every one of those keys must be present on every claim. `ci_low`/`ci_high` are null for a measured value and REQUIRED numbers for an inferred one. `source` is null unless `unit` is "usd", where it is required and must be the `source` string the tool returned beside the figure. `knowledge_citations` is `[]` if you never called search_engineering_knowledge or cited nothing from it. Emit `[]` for an empty list and `null` for an absent number -- never omit a key."""


USER_TEMPLATE = """Plant: {plant_id}
Acting role: {acting_role}

{question}

Investigate using the tools, then return your finding as a single JSON object."""


# ---------------------------------------------------------------------- thread context
#
# Replayed into a follow-up turn as ONE user message, ahead of the new question.
#
# The rule in capitals is the whole of ADR-039 compressed into the only place the model
# will read it. `call_id`s are assigned per RUN (`call_0001` upward, restarting every
# turn), so turn 1 and turn 3 both have a `call_0003` pointing at different results. A
# model that cites an earlier turn's id either fails verification -- and looks amnesiac --
# or, far worse, resolves it against THIS turn's transcript into an unrelated number that
# happens to fall inside the rounding tolerance, and gets a green verified badge on a
# coincidence. Re-calling the tool costs one call out of twenty-four and is also the
# correct semantics: on a live line, a number restated twenty minutes later should be
# restated against current data.
THREAD_CONTEXT_TEMPLATE = """EARLIER IN THIS CONVERSATION

{turns}

HOW TO USE THIS
These are findings you already delivered in this conversation, kept so you understand \
what the operator is referring to. They are NOT current readings, and they are NOT \
evidence.

CITATIONS DO NOT CROSS TURNS. The call_ids above no longer exist. To make any claim \
about a number established earlier, CALL THE TOOL AGAIN NOW and cite this turn's \
call_id. A claim citing an id you were not shown in this turn will be removed."""

TURN_TEMPLATE = """--- Turn {turn_index} ({intent}) ---
Asked: {question}
You found: {summary}
You recommended: {recommendation}{claims}"""


def render_thread_context(turns: list) -> str:
    """Render prior turns for replay, or "" when there are none.

    Returning "" for an empty thread is load-bearing: `genai/loop.py::build_messages`
    then produces a messages array byte-identical to the pre-thread one, so turn 0 of
    every conversation runs the code path the existing loop tests already cover.
    """
    if not turns:
        return ""
    blocks = []
    for t in turns:
        claim_texts = getattr(t, "claim_texts", None) or []
        claims = ""
        if claim_texts:
            claims = "\nEstablished: " + "; ".join(str(c) for c in claim_texts)
        blocks.append(TURN_TEMPLATE.format(
            turn_index=getattr(t, "turn_index", 0),
            intent=getattr(t, "intent", "ask"),
            question=getattr(t, "question", ""),
            summary=getattr(t, "summary", ""),
            recommendation=getattr(t, "recommendation", ""),
            claims=claims,
        ))
    return THREAD_CONTEXT_TEMPLATE.format(turns="\n\n".join(blocks))


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
                         "alternatives_considered", "unanswered", "knowledge_citations"],
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
                "knowledge_citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "quoted_text", "doc_id", "section", "citation"],
                        "properties": {
                            "id": {"type": "string"},
                            "quoted_text": {"type": "string"},
                            "doc_id": {"type": "string"},
                            "section": {"type": "string"},
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
