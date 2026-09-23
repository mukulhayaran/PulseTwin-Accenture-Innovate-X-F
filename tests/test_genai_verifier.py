"""Adversarial fixtures for the deterministic gate.

This is the most important test file in the GenAI tier. The anti-hallucination claim is
only worth as much as these cases -- without them it is a prompt instruction with extra
steps. Every test here runs with NO API key and no network.

Each case asserts a SPECIFIC reason code, not merely "it failed", so a verifier that
starts rejecting everything for the wrong reason is caught too.
"""

from __future__ import annotations

import json

import pytest

from genai import verifier as V
from genai.finding import (
    Citation, Claim, Finding, KnowledgeCitation, ParseError, parse_finding,
)

# A transcript shaped like the real thing: station_stats rows carry their own
# `provenance` column, which is what makes the C4 cross-check possible.
TRANSCRIPT = {
    "call_0001": [
        {"station_id": 12, "util": 0.81, "blocked": 0.18, "starved": 0.031,
         "lost_uph": 13.0, "provenance": "measured"},
        {"station_id": 19, "util": 0.93, "blocked": 0.02, "starved": 0.245,
         "lost_uph": 6.4, "provenance": "measured"},
        {"station_id": 15, "util": 0.73, "blocked": 0.135, "starved": 0.133,
         "lost_uph": 21.4, "provenance": "inferred",
         "ci_low": 18.2, "ci_high": 24.6},
    ],
    "call_0002": {
        "current_station": 19, "previous_station": 12, "confidence": 0.86,
    },
    "call_0003": {
        "components": [
            {"label": "Scrap avoided", "value": 6100000.0,
             "source": "configs/economics.yaml costs.scrap_cost_per_unit"},
        ],
        "net_value": 10270000.0,
    },
    # search_engineering_knowledge's dispatched shape (ADR-046): {"ok": True, "data":
    # {"results": [...]}}, matching T.dispatch()'s wrapping of every tool response.
    "call_0004": {
        "ok": True,
        "data": {
            "query": "torque drift",
            "results": [
                {"doc_id": "SOP-01", "section_id": "SOP-01-2",
                 "heading": "Nominal range and drift",
                 "text": "Nominal torque for a standard M8 body fastener is 80-92 Nm. "
                         "A reading trending upward across consecutive cycles indicates "
                         "tool wear or a calibration drift."},
            ],
        },
    },
}

STATIONS = {12, 15, 19, 30}


def _claim(**kw) -> Claim:
    base = dict(
        id="c1", text="S19 was starved 24.5% of the window", value=0.245,
        unit="pct", provenance="measured",
        citation=Citation(call_id="call_0001", pointer="/1/starved"),
    )
    base.update(kw)
    return Claim(**base)


def _kc(**kw) -> KnowledgeCitation:
    base = dict(
        id="k1", quoted_text="tool wear or a calibration drift",
        doc_id="SOP-01", section="SOP-01-2",
        citation=Citation(call_id="call_0004", pointer="/data/results/0/text"),
    )
    base.update(kw)
    return KnowledgeCitation(**base)


def _finding(claims, summary="The constraint moved.", reco="Add feed buffer.",
            knowledge_citations=()) -> Finding:
    return Finding(question="q", summary=summary, claims=list(claims),
                   recommendation=reco, knowledge_citations=list(knowledge_citations))


def _verify(finding):
    return V.verify(finding, TRANSCRIPT, station_ids=STATIONS)


# ------------------------------------------------------------------ C1: resolvable
def test_happy_path_verifies() -> None:
    ver, served = _verify(_finding([_claim()]))
    assert ver.status == V.VERIFIED
    assert ver.claims_verified == 1
    assert served.claims[0].text.startswith("S19")


def test_unknown_call_id_is_caught() -> None:
    c = _claim(citation=Citation(call_id="call_9999", pointer="/1/starved"))
    ver, _ = _verify(_finding([c]))
    assert ver.claim_verdicts[0].code == V.UNKNOWN_CALL


def test_pointer_past_end_of_array_is_caught() -> None:
    c = _claim(citation=Citation(call_id="call_0001", pointer="/17/starved"))
    ver, _ = _verify(_finding([c]))
    assert ver.claim_verdicts[0].code == V.UNRESOLVABLE_POINTER


def test_pointer_to_non_numeric_is_caught() -> None:
    c = _claim(citation=Citation(call_id="call_0001", pointer="/1/provenance"))
    ver, _ = _verify(_finding([c]))
    assert ver.claim_verdicts[0].code == V.NON_NUMERIC_TARGET


# ------------------------------------------------------------------- C2: faithful
def test_value_mismatch_is_caught() -> None:
    c = _claim(text="S19 lost 47.3 UPH", value=47.3, unit="uph",
               citation=Citation(call_id="call_0001", pointer="/1/lost_uph"))
    ver, _ = _verify(_finding([c]))
    assert ver.claim_verdicts[0].code == V.VALUE_MISMATCH


def test_legitimate_rounding_passes() -> None:
    """41.8 printed against 41.7996 measured is rounding, not drift."""
    t = dict(TRANSCRIPT)
    t["call_0004"] = {"x": 41.7996}
    c = _claim(text="throughput was 41.8 UPH", value=41.8, unit="uph",
               citation=Citation(call_id="call_0004", pointer="/x"))
    ver, _ = V.verify(_finding([c]), t, station_ids=STATIONS)
    assert ver.status == V.VERIFIED, ver.claim_verdicts


def test_integer_precision_rejects_half_unit_drift() -> None:
    """42 carries tolerance 0.5, so 42.6 must fail."""
    t = dict(TRANSCRIPT)
    t["call_0004"] = {"x": 42.6}
    c = _claim(text="42 units were held", value=42.0, unit="count",
               citation=Citation(call_id="call_0004", pointer="/x"))
    ver, _ = V.verify(_finding([c]), t, station_ids=STATIONS)
    assert ver.claim_verdicts[0].code == V.VALUE_MISMATCH


def test_derived_tolerance_scales_with_printed_precision() -> None:
    assert V.derive_tolerance(_claim(text="0.256 PR-AUC", value=0.256)) == pytest.approx(0.0005)
    assert V.derive_tolerance(_claim(text="41.8 UPH", value=41.8)) == pytest.approx(0.05)
    assert V.derive_tolerance(_claim(text="42 units", value=42.0)) == pytest.approx(0.5)


# ----------------------------------------------------------------- C3: exhaustive
def test_uncited_number_in_prose_redacts_the_sentence() -> None:
    f = _finding([_claim()],
                 summary="The constraint moved. This lifted throughput roughly 11.7%.")
    ver, served = _verify(f)
    assert ver.status == V.VERIFIED_WITH_REDACTIONS
    assert any(V.UNCITED_NUMBER in i for i in ver.prose_issues)
    assert V.SENTENCE_MARKER in served.summary
    assert "11.7" not in served.summary


def test_station_id_in_prose_is_exempt() -> None:
    f = _finding([_claim()], summary="The constraint moved from S12 to S19.")
    ver, served = _verify(f)
    assert ver.status == V.VERIFIED, ver.prose_issues
    assert "S19" in served.summary


def test_number_present_in_tool_results_is_exempt() -> None:
    """0.86 is never cited by a claim but does appear in call_0002."""
    f = _finding([_claim()], summary="Detector confidence was 0.86.")
    ver, _ = _verify(f)
    assert ver.status == V.VERIFIED, ver.prose_issues


# ------------------------------------------------------------------ C4: contracts
def test_dollar_figure_without_source_is_rejected() -> None:
    c = _claim(text="worth $6,100,000 a year", value=6100000.0, unit="usd",
               source=None,
               citation=Citation(call_id="call_0003", pointer="/components/0/value"))
    ver, served = _verify(_finding([c]))
    assert ver.status == V.REJECTED
    assert ver.reject_reason.startswith(V.UNSOURCED_CURRENCY)
    assert served.claims == []


def test_dollar_figure_with_source_passes() -> None:
    c = _claim(text="worth $6,100,000 a year", value=6100000.0, unit="usd",
               source="configs/economics.yaml costs.scrap_cost_per_unit",
               citation=Citation(call_id="call_0003", pointer="/components/0/value"))
    ver, _ = _verify(_finding([c], summary="Value is real."))
    assert ver.status == V.VERIFIED, ver.claim_verdicts


def test_inferred_without_interval_is_rejected() -> None:
    c = _claim(text="S15 lost 21.4 UPH", value=21.4, unit="uph",
               provenance="inferred", ci_low=None, ci_high=None,
               citation=Citation(call_id="call_0001", pointer="/2/lost_uph"))
    ver, _ = _verify(_finding([c]))
    assert ver.status == V.REJECTED
    assert ver.reject_reason.startswith(V.MISSING_INTERVAL)


def test_provenance_laundering_is_rejected() -> None:
    """The row says inferred; the claim says measured. Contract #1, broken by machine."""
    c = _claim(text="S15 lost 21.4 UPH", value=21.4, unit="uph",
               provenance="measured",
               citation=Citation(call_id="call_0001", pointer="/2/lost_uph"))
    ver, _ = _verify(_finding([c]))
    assert ver.status == V.REJECTED
    assert ver.reject_reason.startswith(V.PROVENANCE_MISMATCH)


def test_correct_inferred_claim_passes() -> None:
    c = _claim(text="S15 lost 21.4 UPH", value=21.4, unit="uph",
               provenance="inferred", ci_low=18.2, ci_high=24.6,
               citation=Citation(call_id="call_0001", pointer="/2/lost_uph"))
    ver, _ = _verify(_finding([c], summary="Inferred, stated as such."))
    assert ver.status == V.VERIFIED, ver.claim_verdicts


@pytest.mark.parametrize("leak", ["operator_id 4471", "operator 88 rushed it", "OP-1183"])
def test_operator_identity_anywhere_is_rejected(leak: str) -> None:
    ver, served = _verify(_finding([_claim()], summary=f"Cause: {leak}."))
    assert ver.status == V.REJECTED
    assert ver.reject_reason.startswith(V.OPERATOR_IDENTITY)
    assert served.summary == ""


def test_operator_group_bucket_is_legal() -> None:
    f = _finding([_claim()], summary="Shared factor was operator_group_B.")
    ver, _ = _verify(f)
    assert ver.status == V.VERIFIED, ver.reject_reason


# ------------------------------------------------------------- ADR-046: knowledge citations
def test_a_real_quote_verifies() -> None:
    f = _finding([_claim()], summary="Torque drift can indicate tool wear.",
                 knowledge_citations=[_kc()])
    ver, served = _verify(f)
    assert ver.status == V.VERIFIED, (ver.reject_reason, ver.claim_verdicts)
    assert ver.knowledge_citations_total == ver.knowledge_citations_verified == 1
    assert served.knowledge_citations == [_kc()]


def test_a_fabricated_quote_is_rejected() -> None:
    """The model claims the chunk says something it does not -- the whole point of the
    substring check, and the case that would matter most in a real incident."""
    bad = _kc(quoted_text="replace the fastener immediately without further checks")
    ver, served = _verify(_finding([_claim()], knowledge_citations=[bad]))
    assert ver.status == V.REJECTED
    assert ver.reject_reason.startswith(V.KNOWLEDGE_TEXT_MISMATCH)
    assert served.claims == [] and served.knowledge_citations == []


def test_an_empty_quote_is_rejected() -> None:
    """`"" in anything` is True in Python -- a naive substring check would let an empty
    quote pass trivially. Guarded explicitly rather than relying on that never happening."""
    ver, _ = _verify(_finding([_claim()], knowledge_citations=[_kc(quoted_text="")]))
    assert ver.status == V.REJECTED
    assert ver.reject_reason.startswith(V.KNOWLEDGE_TEXT_MISMATCH)


def test_an_unresolvable_knowledge_pointer_is_rejected() -> None:
    bad = _kc(citation=Citation(call_id="call_0004", pointer="/data/results/0/nope"))
    ver, _ = _verify(_finding([_claim()], knowledge_citations=[bad]))
    assert ver.status == V.REJECTED
    assert ver.reject_reason.startswith(V.UNRESOLVABLE_KNOWLEDGE_POINTER)


def test_an_unknown_knowledge_call_id_is_rejected() -> None:
    bad = _kc(citation=Citation(call_id="call_9999", pointer="/data/results/0/text"))
    ver, _ = _verify(_finding([_claim()], knowledge_citations=[bad]))
    assert ver.status == V.REJECTED
    assert ver.reject_reason.startswith(V.UNKNOWN_KNOWLEDGE_CALL)


def test_a_pointer_to_a_non_text_field_is_rejected() -> None:
    """A model that cites the wrong pointer -- e.g. a numeric field -- inside an
    otherwise-real search_engineering_knowledge result."""
    bad = _kc(citation=Citation(call_id="call_0004", pointer="/ok"))  # a bool, not text
    ver, _ = _verify(_finding([_claim()], knowledge_citations=[bad]))
    assert ver.status == V.REJECTED
    assert ver.reject_reason.startswith(V.NON_TEXT_KNOWLEDGE_TARGET)


def test_a_rewrapped_quote_with_different_whitespace_still_verifies() -> None:
    """A model may re-flow a quote across its own output without changing its meaning --
    the check should not fail over that, only over an actually different sentence."""
    kc = _kc(quoted_text="tool  wear\nor a calibration   drift")
    ver, _ = _verify(_finding([_claim()], knowledge_citations=[kc]))
    assert ver.status == V.VERIFIED, ver.reject_reason


def test_a_knowledge_citation_never_inflates_claims_verified() -> None:
    """The bug this design specifically avoids: a knowledge verdict must not be able to
    push claims_verified past claims_total via a shared id/verdict pool."""
    ver, _ = _verify(_finding([_claim()], knowledge_citations=[_kc()]))
    assert ver.claims_total == ver.claims_verified == 1
    assert ver.claims_verified <= ver.claims_total


def test_absent_knowledge_citations_is_unaffected() -> None:
    """Every Finding produced before ADR-046 has none -- must verify exactly as before."""
    ver, served = _verify(_finding([_claim()]))
    assert ver.status == V.VERIFIED
    assert ver.knowledge_citations_total == 0
    assert served.knowledge_citations == []


# ---------------------------------------------------------------------- outcomes
def test_no_claims_is_rejected() -> None:
    ver, _ = _verify(_finding([]))
    assert ver.status == V.REJECTED
    assert ver.reject_reason == "no_claims"


def test_too_many_unverified_rejects_rather_than_redacts() -> None:
    bad = [_claim(id=f"c{i}",
                  citation=Citation(call_id="call_9999", pointer="/x"))
           for i in range(3)]
    ver, served = _verify(_finding([_claim(id="ok")] + bad))
    assert ver.status == V.REJECTED
    assert ver.reject_reason.startswith("too_many_unverified")
    assert served.claims == []


def test_single_failure_redacts_the_claim_but_serves_the_rest() -> None:
    good = [_claim(id=f"g{i}") for i in range(4)]
    bad = _claim(id="bad", citation=Citation(call_id="call_9999", pointer="/x"))
    ver, served = _verify(_finding(good + [bad]))
    assert ver.status == V.VERIFIED_WITH_REDACTIONS
    assert ver.claims_verified == 4
    redacted = [c for c in served.claims if c.id == "bad"][0]
    assert "claim removed" in redacted.text and V.UNKNOWN_CALL in redacted.text


# -------------------------------------------------------------------- determinism
def test_verifier_is_deterministic() -> None:
    """Re-running the gate over stored bytes must give a byte-identical verdict.

    The model is not reproducible; the verifier is, and the verifier is what carries
    the credibility claim.
    """
    f = _finding([_claim(), _claim(id="c2", citation=Citation("call_9999", "/x"))],
                 summary="Moved. Lifted throughput 11.7%.")
    first = json.dumps(_verify(f)[0].to_dict(), sort_keys=True)
    for _ in range(50):
        assert json.dumps(_verify(f)[0].to_dict(), sort_keys=True) == first


# ------------------------------------------------------------------------ parsing
def test_parses_prose_wrapped_json() -> None:
    raw = (
        'Here is my analysis:\n```json\n'
        '{"summary":"s","recommendation":"r","claims":[{"id":"c1","text":"t",'
        '"value":1.0,"unit":"uph","provenance":"measured",'
        '"citation":{"call_id":"call_0001","pointer":"/1/lost_uph"}}]}\n```\nDone.'
    )
    f = parse_finding(raw, question="q")
    assert f.claims[0].citation.call_id == "call_0001"


def test_parses_json_containing_braces_in_strings() -> None:
    raw = ('{"summary":"uses {braces} inside","recommendation":"r","claims":'
           '[{"id":"c1","text":"t","value":1.0,"unit":"uph","provenance":"measured",'
           '"citation":{"call_id":"call_0001","pointer":"/1/lost_uph"}}]}')
    assert parse_finding(raw, question="q").summary == "uses {braces} inside"


@pytest.mark.parametrize("raw", ["", "no json at all", "{unbalanced", '{"summary":"s"}'])
def test_unparseable_output_raises(raw: str) -> None:
    with pytest.raises(ParseError):
        parse_finding(raw, question="q")


def test_reject_unparseable_serves_nothing() -> None:
    ver, served = V.reject_unparseable("q", "no json at all")
    assert ver.status == V.REJECTED
    assert ver.reject_reason.startswith("unparseable_finding")
    assert served.claims == []
