"""The deterministic gate.

Everything else in PulseTwin is measured. A language model is the one component that
cannot be regenerated deterministically, so it is not trusted -- it is checked, by this
module, with no model in the loop.

Four checks, because "every claim cites a tool result" catches one failure mode out of
four:

  C1 resolvable  -- the (call_id, pointer) exists in the RECORDED transcript
  C2 faithful    -- the cited number matches, within a tolerance DERIVED from the
                    claim's own printed precision (not a fixed epsilon, which either
                    rejects legitimate rounding or waves through a 5% error)
  C3 exhaustive  -- every number in the PROSE is accounted for. This is where
                    hallucination actually lives: the claim table is the part a model
                    tries hard on, the summary is where it relaxes and writes
                    "which lifted throughput roughly 11%"
  C4 contracts   -- CLAUDE.md §3, enforced rather than requested

The consequence of failure is redaction or rejection, never a flag. A flagged number is
still a rendered number and a reader will read it.

This module imports no provider SDK, opens no socket, reads no clock and draws no
randomness. `python -m genai.verify_cli --run-id X` re-runs it over stored bytes and
must produce a byte-identical verdict -- that is the seed-42 convention applied where it
can actually hold.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from genai import config
from genai.finding import ACTION_TYPES, Claim, Finding, KnowledgeCitation, ProposedAction
from serve.privacy import contains_operator_identity

# ----------------------------------------------------------------------- verdict codes
OK = "ok"
UNKNOWN_CALL = "unknown_call"
UNRESOLVABLE_POINTER = "unresolvable_pointer"
NON_NUMERIC_TARGET = "non_numeric_target"
VALUE_MISMATCH = "value_mismatch"
# ADR-064: a claim carrying an interval that is not the interval the cited result carries.
# Before experiments existed the gate only checked that an inferred claim HAD an interval.
INTERVAL_MISMATCH = "interval_mismatch"
UNSOURCED_CURRENCY = "unsourced_currency"
MISSING_INTERVAL = "missing_interval"
PROVENANCE_MISMATCH = "provenance_mismatch"
OPERATOR_IDENTITY = "operator_identity"
UNCITED_NUMBER = "uncited_number"

# ADR-046: the text-shaped counterpart to C1/C2, for search_engineering_knowledge
# citations. Unlike numeric claims, these are not graded against
# MAX_REDACTED_CLAIM_RATIO -- there is no partial credit for a fabricated procedure
# quote, so every one of these codes is fatal (see FATAL_CODES below).
UNKNOWN_KNOWLEDGE_CALL = "unknown_knowledge_call"
UNRESOLVABLE_KNOWLEDGE_POINTER = "unresolvable_knowledge_pointer"
NON_TEXT_KNOWLEDGE_TARGET = "non_text_knowledge_target"
KNOWLEDGE_TEXT_MISMATCH = "knowledge_text_mismatch"

# C4 failures are categorical: one is fatal regardless of how small a share of claims
# it represents. C1/C2 failures are graded against MAX_REDACTED_CLAIM_RATIO. Every
# knowledge-citation code is fatal for the same reason C4 is: inventing what a
# maintenance procedure says is a comparable trust violation to inventing a dollar
# figure, and there is no meaningful "70% of this quote was real" partial credit.
FATAL_CODES = frozenset({
    UNSOURCED_CURRENCY, MISSING_INTERVAL, PROVENANCE_MISMATCH, OPERATOR_IDENTITY,
    UNKNOWN_KNOWLEDGE_CALL, UNRESOLVABLE_KNOWLEDGE_POINTER, NON_TEXT_KNOWLEDGE_TARGET,
    KNOWLEDGE_TEXT_MISMATCH,
})

VERIFIED = "verified"
VERIFIED_WITH_REDACTIONS = "verified_with_redactions"
REJECTED = "rejected"

REDACTION_MARKER = "[claim removed: {reason}]"
SENTENCE_MARKER = "[sentence removed: uncited figure]"

# Numbers in prose, including $1,234.56 / 12.5% / -3.2
_NUMBER_RE = re.compile(r"-?\$?\d[\d,]*\.?\d*%?")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass
class ClaimVerdict:
    claim_id: str
    code: str
    detail: str = ""
    resolved_value: float | None = None

    @property
    def ok(self) -> bool:
        return self.code == OK


@dataclass
class Verification:
    status: str
    claims_total: int
    claims_verified: int
    claim_verdicts: list[ClaimVerdict] = field(default_factory=list)
    prose_issues: list[str] = field(default_factory=list)
    reject_reason: str = ""
    # How many values from EARLIER TURNS of this thread rescued a sentence in the C3
    # sweep (ADR-039). Surfaced rather than silent: a verdict that leaned on evidence
    # from outside this run should not look identical to one that did not.
    carried_values_used: int = 0
    # ADR-046. Kept SEPARATE from claims_total/claims_verified rather than folded in --
    # every knowledge-citation code is fatal, so a knowledge verdict must never be able to
    # inflate `claims_verified` past `claims_total` and corrupt the C1/C2 redaction ratio.
    knowledge_citations_total: int = 0
    knowledge_citations_verified: int = 0
    # ADR-051. Proposed actions are graded like numbers: served only if every claim they
    # rest on verified. Dropping one is never fatal -- it is a proposal, not evidence.
    actions_total: int = 0
    actions_served: int = 0
    action_issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "claims_total": self.claims_total,
            "claims_verified": self.claims_verified,
            "claim_verdicts": [asdict(v) for v in self.claim_verdicts],
            "prose_issues": list(self.prose_issues),
            "reject_reason": self.reject_reason,
            "carried_values_used": self.carried_values_used,
            "knowledge_citations_total": self.knowledge_citations_total,
            "knowledge_citations_verified": self.knowledge_citations_verified,
            "actions_total": self.actions_total,
            "actions_served": self.actions_served,
            "action_issues": list(self.action_issues),
        }


# --------------------------------------------------------------------------- RFC 6901
def resolve_pointer(doc: Any, pointer: str) -> Any:
    """Resolve an RFC 6901 JSON Pointer. Raises KeyError if it does not resolve."""
    if pointer in ("", "/"):
        return doc
    if not pointer.startswith("/"):
        raise KeyError(f"pointer must start with '/': {pointer!r}")

    node = doc
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict):
            if token not in node:
                raise KeyError(f"no key {token!r}")
            node = node[token]
        elif isinstance(node, list):
            if not token.lstrip("-").isdigit():
                raise KeyError(f"non-integer index {token!r}")
            idx = int(token)
            if idx < 0 or idx >= len(node):
                raise KeyError(f"index {idx} out of range (len {len(node)})")
            node = node[idx]
        else:
            raise KeyError(f"cannot index {type(node).__name__} with {token!r}")
    return node


def _parent_of(doc: Any, pointer: str) -> Any | None:
    if "/" not in pointer.lstrip("/"):
        return None
    try:
        return resolve_pointer(doc, pointer.rsplit("/", 1)[0])
    except KeyError:
        return None


# ------------------------------------------------------------------------- tolerance
def _decimals(text: str) -> int | None:
    if "." not in text:
        return 0
    frac = text.split(".", 1)[1]
    digits = "".join(c for c in frac if c.isdigit())
    return len(digits) if digits else 0


def derive_tolerance(claim: Claim) -> float:
    """Half a unit in the last printed place.

    41.8 -> 0.05, 42 -> 0.5, 0.256 -> 0.0005. The model is allowed to round for
    readability and is not allowed to drift. Precision is read from how the number is
    written in the claim TEXT where possible -- that is what a human actually reads --
    and falls back to the parsed value's own repr.
    """
    for token in _NUMBER_RE.findall(claim.text):
        cleaned = token.replace("$", "").replace(",", "").replace("%", "")
        try:
            if abs(float(cleaned) - claim.value) < 1e-9:
                d = _decimals(cleaned)
                if d is not None:
                    return 0.5 * (10.0 ** -d)
        except ValueError:
            continue
    d = _decimals(repr(claim.value))
    return 0.5 * (10.0 ** -(d if d is not None else 3))


# ------------------------------------------------------------------- number extraction
def _numbers_in(text: str) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for m in _NUMBER_RE.finditer(text):
        token = m.group()
        # "101-125s" is a RANGE, not 101 and minus 125: a hyphen glued to a preceding
        # digit is not a sign. Measured live: the phantom -125 cost a correct sentence.
        if token.startswith("-") and m.start() > 0 and text[m.start() - 1].isdigit():
            token = token[1:]
        cleaned = token.replace("$", "").replace(",", "").replace("%", "")
        if not cleaned or cleaned in ("-", "."):
            continue
        try:
            out.append((token, float(cleaned)))
        except ValueError:
            continue
    return out


def _printed_matches(token: str, value: float, pool: set[float]) -> float | None:
    """The pool value this printed number stands for, or None.

    The SAME rounding rule claims get (derive_tolerance: half a unit in the last printed
    place), applied to prose -- before, a claim could say 41.8 for 41.77 but the sentence
    beside it could not. Integers stay exact: "3" must not absorb every value in [2.5,3.5].
    A percentage may also stand for a RATIO the API returned (85.9% for 0.8586), at the
    ratio's equivalent precision -- a unit conversion, not a looser match.
    """
    cleaned = token.replace("$", "").replace(",", "").replace("%", "")
    d = _decimals(cleaned) or 0
    tol = 0.5 * 10.0 ** -d if d else 1e-6
    for c in pool:
        if abs(value - c) <= tol:
            return c
        if token.endswith("%") and abs(value / 100.0 - c) <= max(tol / 100.0, 1e-9):
            return c
    return None


def _flatten_numbers(node: Any, acc: set[float]) -> None:
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        acc.add(float(node))
    elif isinstance(node, dict):
        for v in node.values():
            _flatten_numbers(v, acc)
    elif isinstance(node, list):
        for v in node:
            _flatten_numbers(v, acc)
    elif isinstance(node, str):
        for _, v in _numbers_in(node):
            acc.add(v)


def _contains_operator_identity(text: str) -> bool:
    # Kept as the verifier's public contract hook; the runtime uses the same predicate
    # for human-entered notes so the two tiers cannot drift to different definitions.
    return contains_operator_identity(text)


def _normalize_text(text: str) -> str:
    """Lowercase, whitespace-collapsed -- a model may re-wrap or re-space a quote without
    changing its meaning, and this check should not fail over that. It should still fail
    over a genuinely different sentence, which whitespace normalization cannot hide."""
    return " ".join(text.lower().split())


# ------------------------------------------------------------------------------ verify
def verify(
    finding: Finding,
    transcript: dict[str, Any],
    *,
    station_ids: set[int] | None = None,
    carried_values: set[float] | None = None,
) -> tuple[Verification, Finding]:
    """Check a finding against the tool results the model actually saw.

    `transcript` maps our runner-assigned call_id -> the RECORDED (already truncated)
    result. Truncated, not full: recording the full result while having sent a truncated
    one would let a citation resolve into data the model never received, which inverts
    the entire guarantee.

    `carried_values` (ADR-039) is the ONE concession threads required, and its scope is
    deliberately tiny: numbers a PREVIOUS turn of the same thread already verified,
    admitted as a fourth exemption in the C3 prose sweep AND NOWHERE ELSE. Without it,
    "the 41.8 UPH we established is unchanged" loses its sentence on every follow-up and
    a conversation reads as visibly shredded. With it, the guarantee weakens from
    "traceable to a tool result in this run" to "traceable to a tool result in this
    thread" -- and the earlier turn's results are in `triage_tool_calls` permanently, so
    the weaker claim is still auditable.

    A CLAIM is never verified by a carried value. Do not "fix" that: `_verify_claim` is
    untouched on purpose, and a claim that resolves against nothing in THIS run's
    transcript must still fail.

    Returns (verification, served_finding). The served finding has failing claims and
    uncited prose sentences removed -- on REJECTED nothing is served at all.
    """
    station_ids = station_ids or set()
    carried = carried_values or set()
    verdicts: list[ClaimVerdict] = []

    # Every number anywhere in the recorded results, for the C3 sweep.
    corpus: set[float] = set()
    for result in transcript.values():
        _flatten_numbers(result, corpus)

    for claim in finding.claims:
        verdicts.append(_verify_claim(claim, transcript))

    # ADR-046. SEPARATE from `verdicts`: every one of these codes is fatal (see
    # FATAL_CODES), so a knowledge verdict must never be counted into `ok_ids`/`verified`
    # below -- that number is claims_total/claims_verified's contract with C1/C2's
    # graded ratio, and a knowledge citation is not part of that population.
    knowledge_verdicts: list[ClaimVerdict] = [
        _verify_knowledge_citation(kc, transcript) for kc in finding.knowledge_citations
    ]

    # ---- C4 on prose as well as claims: operator identity may not appear anywhere
    for label, text in (("summary", finding.summary),
                        ("recommendation", finding.recommendation)):
        if _contains_operator_identity(text):
            verdicts.append(ClaimVerdict(f"<{label}>", OPERATOR_IDENTITY,
                                         "operator identity in prose"))

    ok_ids = {v.claim_id for v in verdicts if v.ok}
    cited_values = {c.value for c in finding.claims if c.id in ok_ids}

    prose_issues, clean_summary, used_summary = _sweep_prose(
        finding.summary, cited_values, corpus, station_ids, carried)
    more_issues, clean_reco, used_reco = _sweep_prose(
        finding.recommendation, cited_values, corpus, station_ids, carried)
    prose_issues.extend(more_issues)
    carried_used = used_summary | used_reco
    # ADR-064: hypothesis statements are prose too, under exactly the same sweep.
    served_hypotheses = []
    for h in finding.hypotheses:
        if _contains_operator_identity(h.statement):
            verdicts.append(ClaimVerdict(f"<{h.id}>", OPERATOR_IDENTITY,
                                         "operator identity in a hypothesis"))
            continue
        h_issues, clean, used = _sweep_prose(h.statement, cited_values, corpus,
                                             station_ids, carried)
        prose_issues.extend(h_issues)
        carried_used |= used
        served_hypotheses.append(replace(h, statement=clean,
                                         claim_ids=[c for c in h.claim_ids if c in ok_ids]))

    # ---- outcome
    total = len(finding.claims)
    verified = len(ok_ids)
    kc_total = len(finding.knowledge_citations)
    kc_verified = sum(1 for v in knowledge_verdicts if v.ok)
    fatal = [v for v in verdicts if v.code in FATAL_CODES] + [
        v for v in knowledge_verdicts if v.code in FATAL_CODES
    ]

    if not finding.claims:
        return _reject(verdicts, prose_issues, "no_claims", total, verified,
                       kc_total, kc_verified)
    if fatal:
        return _reject(verdicts, prose_issues,
                       f"{fatal[0].code}:{fatal[0].claim_id}", total, verified,
                       kc_total, kc_verified)

    failed = total - verified
    if failed / total > config.MAX_REDACTED_CLAIM_RATIO:
        return _reject(verdicts, prose_issues,
                       f"too_many_unverified:{failed}/{total}", total, verified,
                       kc_total, kc_verified)

    status = VERIFIED if (failed == 0 and not prose_issues) else VERIFIED_WITH_REDACTIONS

    served_claims: list[Claim] = []
    by_id = {v.claim_id: v for v in verdicts}
    for claim in finding.claims:
        v = by_id.get(claim.id)
        if v is not None and v.ok:
            served_claims.append(claim)
        else:
            reason = v.code if v else "unknown"
            served_claims.append(
                Claim(
                    id=claim.id,
                    text=REDACTION_MARKER.format(reason=reason),
                    value=0.0,
                    unit="count",
                    provenance=claim.provenance,
                    citation=claim.citation,
                )
            )

    served_actions, action_issues = _grade_actions(
        finding, ok_ids, cited_values, corpus, station_ids, carried)

    served = Finding(
        question=finding.question,
        summary=clean_summary,
        claims=served_claims,
        recommendation=clean_reco,
        alternatives_considered=finding.alternatives_considered,
        confidence=finding.confidence,
        unanswered=finding.unanswered,
        # Reaching here means `fatal` was empty, and every knowledge-citation code is
        # fatal -- so every citation in finding.knowledge_citations already verified OK.
        # Nothing to redact, unlike claims: there is no "REDACTED knowledge citation"
        # marker because a run with even one bad one never reaches this line.
        knowledge_citations=finding.knowledge_citations,
        proposed_actions=served_actions,
        decision=finding.decision,
        hypotheses=served_hypotheses,
    )
    return (
        Verification(status=status, claims_total=total, claims_verified=verified,
                     claim_verdicts=verdicts, prose_issues=prose_issues,
                     carried_values_used=len(carried_used),
                     knowledge_citations_total=kc_total,
                     knowledge_citations_verified=kc_verified,
                     actions_total=len(finding.proposed_actions),
                     actions_served=len(served_actions),
                     action_issues=action_issues),
        served,
    )


def _grade_actions(
    finding: Finding,
    ok_ids: set[str],
    cited_values: set[float],
    corpus: set[float],
    station_ids: set[int],
    carried: set[float],
) -> tuple[list[ProposedAction], list[str]]:
    """ADR-051: serve an action only if its type is in the closed catalogue, its required
    params are present, and it rests on at least one claim -- ALL of which verified. Its
    rationale is prose and gets the same C3 sweep and C4 privacy check as the summary."""
    served: list[ProposedAction] = []
    issues: list[str] = []
    for action in finding.proposed_actions:
        required = ACTION_TYPES.get(action.action_type)
        if required is None:
            issues.append(f"{action.id}: unknown action_type {action.action_type!r}")
            continue
        missing = [p for p in required if p not in action.params]
        if missing:
            issues.append(f"{action.id}: missing params {missing}")
            continue
        if not action.claim_ids:
            issues.append(f"{action.id}: cites no claim")
            continue
        unverified = [c for c in action.claim_ids if c not in ok_ids]
        if unverified:
            issues.append(f"{action.id}: rests on unverified claims {unverified}")
            continue
        if _contains_operator_identity(action.rationale):
            issues.append(f"{action.id}: operator identity in rationale")
            continue
        _, clean, _ = _sweep_prose(action.rationale, cited_values, corpus,
                                   station_ids, carried)
        served.append(ProposedAction(id=action.id, action_type=action.action_type,
                                     params=dict(action.params), rationale=clean,
                                     claim_ids=list(action.claim_ids)))
    return served, issues


def _verify_claim(claim: Claim, transcript: dict[str, Any]) -> ClaimVerdict:
    # ---- C4 first: contract violations are fatal, so establish them before anything else
    if _contains_operator_identity(claim.text) or (
        claim.source and _contains_operator_identity(claim.source)
    ):
        return ClaimVerdict(claim.id, OPERATOR_IDENTITY, "operator identity in claim")

    if claim.unit == "usd" and not (claim.source and claim.source.strip()):
        return ClaimVerdict(claim.id, UNSOURCED_CURRENCY,
                            "dollar figure without a source (CLAUDE.md §3)")

    if claim.provenance == "inferred" and (claim.ci_low is None or claim.ci_high is None):
        return ClaimVerdict(claim.id, MISSING_INTERVAL,
                            "inferred value without ci_low/ci_high")

    # ---- C1 resolvable
    call_id = claim.citation.call_id
    if call_id not in transcript:
        return ClaimVerdict(claim.id, UNKNOWN_CALL, f"no such call_id: {call_id}")

    doc = transcript[call_id]
    try:
        target = resolve_pointer(doc, claim.citation.pointer)
    except KeyError as exc:
        return ClaimVerdict(claim.id, UNRESOLVABLE_POINTER,
                            f"{claim.citation.pointer}: {exc}")

    if isinstance(target, bool) or not isinstance(target, (int, float)):
        return ClaimVerdict(claim.id, NON_NUMERIC_TARGET,
                            f"{claim.citation.pointer} -> {type(target).__name__}")

    # ---- C4 provenance cross-check: where the cited row carries its own provenance
    # column, the claim must MATCH it. A free, real check rather than a self-report --
    # and the only thing standing between this feature and contract #1 being broken by
    # a machine, at scale, in the view leadership reads.
    parent = _parent_of(doc, claim.citation.pointer)
    if isinstance(parent, dict) and isinstance(parent.get("provenance"), str):
        row_prov = parent["provenance"]
        if row_prov in ("measured", "inferred") and row_prov != claim.provenance:
            return ClaimVerdict(claim.id, PROVENANCE_MISMATCH,
                                f"row says {row_prov}, claim says {claim.provenance}")

    # ---- C2 faithful. A claim written as a percentage of a ratio the API returned
    # (85.9% for 0.8586) is the same number in another unit, compared at its printed
    # precision -- the live run's commonest false rejection.
    tol = derive_tolerance(claim)
    as_percent = claim.unit in ("percent", "pct", "%") or any(
        t.endswith("%") and _numbers_in(t) and abs(_numbers_in(t)[0][1] - claim.value) < 1e-9
        for t in _NUMBER_RE.findall(claim.text))
    if as_percent and abs(float(target) * 100.0 - claim.value) <= tol:
        return ClaimVerdict(claim.id, OK, resolved_value=float(target))
    # ---- the interval, when the cited result carries one beside the value
    if (isinstance(parent, dict) and claim.ci_low is not None and claim.ci_high is not None
            and isinstance(parent.get("ci_low"), (int, float))
            and isinstance(parent.get("ci_high"), (int, float))
            and abs(float(target) - claim.value) <= tol):
        for side in ("ci_low", "ci_high"):
            got = float(getattr(claim, side))
            want = float(parent[side])
            d = _decimals(repr(got))
            side_tol = max(tol, 0.5 * 10.0 ** -(d if d is not None else 3))
            if abs(got - want) > side_tol:
                return ClaimVerdict(claim.id, INTERVAL_MISMATCH,
                                    f"{side} {got} vs result {want} (tol {side_tol})",
                                    float(target))
    if abs(float(target) - claim.value) > tol:
        return ClaimVerdict(claim.id, VALUE_MISMATCH,
                            f"claim {claim.value} vs result {target} (tol {tol})",
                            float(target))

    return ClaimVerdict(claim.id, OK, resolved_value=float(target))


def _verify_knowledge_citation(
    kc: KnowledgeCitation, transcript: dict[str, Any]
) -> ClaimVerdict:
    """The text-shaped counterpart to `_verify_claim` (ADR-046): C1/C2 resolvability and
    faithfulness, applied to a quoted excerpt instead of a number. Re-derives the ACTUAL
    chunk text from the recorded transcript and checks `quoted_text` is really in it --
    the model is never trusted to have recalled a procedure correctly on its own."""
    if not kc.quoted_text.strip():
        return ClaimVerdict(kc.id, KNOWLEDGE_TEXT_MISMATCH, "quoted_text is empty")

    call_id = kc.citation.call_id
    if call_id not in transcript:
        return ClaimVerdict(kc.id, UNKNOWN_KNOWLEDGE_CALL, f"no such call_id: {call_id}")

    doc = transcript[call_id]
    try:
        target = resolve_pointer(doc, kc.citation.pointer)
    except KeyError as exc:
        return ClaimVerdict(kc.id, UNRESOLVABLE_KNOWLEDGE_POINTER,
                            f"{kc.citation.pointer}: {exc}")

    if not isinstance(target, str):
        return ClaimVerdict(kc.id, NON_TEXT_KNOWLEDGE_TARGET,
                            f"{kc.citation.pointer} -> {type(target).__name__}")

    if _normalize_text(kc.quoted_text) not in _normalize_text(target):
        return ClaimVerdict(kc.id, KNOWLEDGE_TEXT_MISMATCH,
                            "quoted_text is not a substring of the cited chunk")

    return ClaimVerdict(kc.id, OK)


def _sweep_prose(
    text: str,
    cited: set[float],
    corpus: set[float],
    station_ids: set[int],
    carried: set[float] | None = None,
) -> tuple[list[str], str, set[float]]:
    """C3. Drop any sentence carrying a number that is not accounted for.

    Exemptions, narrow and explicit, in the order they are tried:
      1. the value equals a claim verified in THIS run;
      2. the value appears verbatim somewhere in THIS run's recorded tool results;
      3. the value is a station id that is real in this plant's config;
      4. the value was verified in an EARLIER TURN of the same thread (ADR-039).

    Exemption 4 is the only one that reaches outside this run, which is why the third
    return value reports exactly which carried values were used: a verdict that leans on
    it says so, rather than looking identical to one that did not.
    """
    carried = carried or set()
    issues: list[str] = []
    kept: list[str] = []
    used: set[float] = set()

    for sentence in _SENTENCE_SPLIT.split(text.strip()) if text.strip() else []:
        bad: list[str] = []
        for token, value in _numbers_in(sentence):
            if _printed_matches(token, value, cited) is not None:
                continue
            if _printed_matches(token, value, corpus) is not None:
                continue
            if value.is_integer() and int(value) in station_ids:
                continue
            match = _printed_matches(token, value, carried)
            if match is not None:
                used.add(match)
                continue
            bad.append(token)
        if bad:
            issues.append(f"{UNCITED_NUMBER}: {', '.join(bad)} in {sentence.strip()!r}")
            kept.append(SENTENCE_MARKER)
        else:
            kept.append(sentence)

    return issues, " ".join(kept).strip(), used


def _reject(
    verdicts: list[ClaimVerdict],
    prose_issues: list[str],
    reason: str,
    total: int,
    verified: int,
    kc_total: int = 0,
    kc_verified: int = 0,
) -> tuple[Verification, Finding]:
    """A rejected run serves NO finding -- only the evidence and the reason.

    On-thesis, and deliberately so: "the agent produced an unverifiable answer and we
    discarded it" is the same posture as the published negative results in ADR-030/031.
    """
    return (
        Verification(status=REJECTED, claims_total=total, claims_verified=verified,
                     claim_verdicts=verdicts, prose_issues=prose_issues,
                     reject_reason=reason, knowledge_citations_total=kc_total,
                     knowledge_citations_verified=kc_verified),
        Finding(question="", summary="", claims=[], recommendation=""),
    )


def reject_unparseable(question: str, detail: str) -> tuple[Verification, Finding]:
    """Entry point for the loop when the model's output was not a Finding at all."""
    return (
        Verification(status=REJECTED, claims_total=0, claims_verified=0,
                     reject_reason=f"unparseable_finding:{detail}"),
        Finding(question=question, summary="", claims=[], recommendation=""),
    )
