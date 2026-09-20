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
from dataclasses import asdict, dataclass, field
from typing import Any

from genai import config
from genai.finding import Claim, Finding

# ----------------------------------------------------------------------- verdict codes
OK = "ok"
UNKNOWN_CALL = "unknown_call"
UNRESOLVABLE_POINTER = "unresolvable_pointer"
NON_NUMERIC_TARGET = "non_numeric_target"
VALUE_MISMATCH = "value_mismatch"
UNSOURCED_CURRENCY = "unsourced_currency"
MISSING_INTERVAL = "missing_interval"
PROVENANCE_MISMATCH = "provenance_mismatch"
OPERATOR_IDENTITY = "operator_identity"
UNCITED_NUMBER = "uncited_number"

# C4 failures are categorical: one is fatal regardless of how small a share of claims
# it represents. C1/C2 failures are graded against MAX_REDACTED_CLAIM_RATIO.
FATAL_CODES = frozenset({UNSOURCED_CURRENCY, MISSING_INTERVAL,
                         PROVENANCE_MISMATCH, OPERATOR_IDENTITY})

VERIFIED = "verified"
VERIFIED_WITH_REDACTIONS = "verified_with_redactions"
REJECTED = "rejected"

REDACTION_MARKER = "[claim removed: {reason}]"
SENTENCE_MARKER = "[sentence removed: uncited figure]"

# An operator id is never legal in output. The ONLY legal operator token is a group
# bucket (CLAUDE.md §3). Matches `operator_id`, `operator 4471`, `OP-1183`, etc., while
# deliberately NOT matching `operator_group_B`.
_OPERATOR_PATTERNS = (
    re.compile(r"operator[_\s-]*(?:id|#)?[_\s-]*\d+", re.I),
    re.compile(r"\bop[-_]\d{2,}\b", re.I),
    re.compile(r"operator[_\s-]*id", re.I),
)
_LEGAL_OPERATOR = re.compile(r"^operator_group_[A-Z0-9]+$")

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "claims_total": self.claims_total,
            "claims_verified": self.claims_verified,
            "claim_verdicts": [asdict(v) for v in self.claim_verdicts],
            "prose_issues": list(self.prose_issues),
            "reject_reason": self.reject_reason,
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
    for token in _NUMBER_RE.findall(text):
        cleaned = token.replace("$", "").replace(",", "").replace("%", "")
        if not cleaned or cleaned in ("-", "."):
            continue
        try:
            out.append((token, float(cleaned)))
        except ValueError:
            continue
    return out


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
    if _LEGAL_OPERATOR.match(text.strip()):
        return False
    return any(p.search(text) for p in _OPERATOR_PATTERNS)


# ------------------------------------------------------------------------------ verify
def verify(
    finding: Finding,
    transcript: dict[str, Any],
    *,
    station_ids: set[int] | None = None,
) -> tuple[Verification, Finding]:
    """Check a finding against the tool results the model actually saw.

    `transcript` maps our runner-assigned call_id -> the RECORDED (already truncated)
    result. Truncated, not full: recording the full result while having sent a truncated
    one would let a citation resolve into data the model never received, which inverts
    the entire guarantee.

    Returns (verification, served_finding). The served finding has failing claims and
    uncited prose sentences removed -- on REJECTED nothing is served at all.
    """
    station_ids = station_ids or set()
    verdicts: list[ClaimVerdict] = []

    # Every number anywhere in the recorded results, for the C3 sweep.
    corpus: set[float] = set()
    for result in transcript.values():
        _flatten_numbers(result, corpus)

    for claim in finding.claims:
        verdicts.append(_verify_claim(claim, transcript))

    # ---- C4 on prose as well as claims: operator identity may not appear anywhere
    for label, text in (("summary", finding.summary),
                        ("recommendation", finding.recommendation)):
        if _contains_operator_identity(text):
            verdicts.append(ClaimVerdict(f"<{label}>", OPERATOR_IDENTITY,
                                         "operator identity in prose"))

    ok_ids = {v.claim_id for v in verdicts if v.ok}
    cited_values = {c.value for c in finding.claims if c.id in ok_ids}

    prose_issues, clean_summary = _sweep_prose(
        finding.summary, cited_values, corpus, station_ids)
    more_issues, clean_reco = _sweep_prose(
        finding.recommendation, cited_values, corpus, station_ids)
    prose_issues.extend(more_issues)

    # ---- outcome
    total = len(finding.claims)
    verified = len(ok_ids)
    fatal = [v for v in verdicts if v.code in FATAL_CODES]

    if not finding.claims:
        return _reject(verdicts, prose_issues, "no_claims", total, verified)
    if fatal:
        return _reject(verdicts, prose_issues,
                       f"{fatal[0].code}:{fatal[0].claim_id}", total, verified)

    failed = total - verified
    if failed / total > config.MAX_REDACTED_CLAIM_RATIO:
        return _reject(verdicts, prose_issues,
                       f"too_many_unverified:{failed}/{total}", total, verified)

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

    served = Finding(
        question=finding.question,
        summary=clean_summary,
        claims=served_claims,
        recommendation=clean_reco,
        alternatives_considered=finding.alternatives_considered,
        confidence=finding.confidence,
        unanswered=finding.unanswered,
    )
    return (
        Verification(status=status, claims_total=total, claims_verified=verified,
                     claim_verdicts=verdicts, prose_issues=prose_issues),
        served,
    )


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

    # ---- C2 faithful
    tol = derive_tolerance(claim)
    if abs(float(target) - claim.value) > tol:
        return ClaimVerdict(claim.id, VALUE_MISMATCH,
                            f"claim {claim.value} vs result {target} (tol {tol})",
                            float(target))

    return ClaimVerdict(claim.id, OK, resolved_value=float(target))


def _sweep_prose(
    text: str,
    cited: set[float],
    corpus: set[float],
    station_ids: set[int],
) -> tuple[list[str], str]:
    """C3. Drop any sentence carrying a number that is not accounted for.

    Exemptions, narrow and explicit: a value that equals a verified claim, a value that
    appears verbatim somewhere in the recorded tool results, and a station id that is a
    real station in this plant's config.
    """
    issues: list[str] = []
    kept: list[str] = []

    for sentence in _SENTENCE_SPLIT.split(text.strip()) if text.strip() else []:
        bad: list[str] = []
        for token, value in _numbers_in(sentence):
            if any(abs(value - c) < 1e-6 for c in cited):
                continue
            if any(abs(value - c) < 1e-6 for c in corpus):
                continue
            if value.is_integer() and int(value) in station_ids:
                continue
            bad.append(token)
        if bad:
            issues.append(f"{UNCITED_NUMBER}: {', '.join(bad)} in {sentence.strip()!r}")
            kept.append(SENTENCE_MARKER)
        else:
            kept.append(sentence)

    return issues, " ".join(kept).strip()


def _reject(
    verdicts: list[ClaimVerdict],
    prose_issues: list[str],
    reason: str,
    total: int,
    verified: int,
) -> tuple[Verification, Finding]:
    """A rejected run serves NO finding -- only the evidence and the reason.

    On-thesis, and deliberately so: "the agent produced an unverifiable answer and we
    discarded it" is the same posture as the published negative results in ADR-030/031.
    """
    return (
        Verification(status=REJECTED, claims_total=total, claims_verified=verified,
                     claim_verdicts=verdicts, prose_issues=prose_issues,
                     reject_reason=reason),
        Finding(question="", summary="", claims=[], recommendation=""),
    )


def reject_unparseable(question: str, detail: str) -> tuple[Verification, Finding]:
    """Entry point for the loop when the model's output was not a Finding at all."""
    return (
        Verification(status=REJECTED, claims_total=0, claims_verified=0,
                     reject_reason=f"unparseable_finding:{detail}"),
        Finding(question=question, summary="", claims=[], recommendation=""),
    )
