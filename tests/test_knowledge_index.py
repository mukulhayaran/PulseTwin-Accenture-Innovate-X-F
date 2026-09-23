"""The engineering-knowledge TF-IDF index: offline build, and serve/'s runtime search
(ADR-046).

The tokenizer is duplicated between `offline/build_knowledge_index.py` (which cannot be
imported by `serve/`, the import wall) and `serve/knowledge_index.py` -- the first thing
this file pins is that the two never drift, the same way
`tests/test_genai_progress.py::test_serve_and_genai_agree_on_the_phase_trail` pins
PHASE_ORDER's mirrored constant.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]

from offline.build_knowledge_index import build as offline_build
from offline.build_knowledge_index import tokenize as offline_tokenize
from offline.knowledge.corpus import CORPUS
from serve.knowledge_index import KnowledgeIndex
from serve.knowledge_index import tokenize as serve_tokenize


# --------------------------------------------------------------------- tokenizer parity
@pytest.mark.parametrize("text", [
    "",
    "weld_current_a outside its validated range",
    "Torque Calibration Procedure",
    "S14 dwell exceeded 480.0s",
    "FC-301: weld_current_a anomaly, see SOP-01.",
])
def test_serve_and_offline_tokenizers_agree(text: str) -> None:
    assert serve_tokenize(text) == offline_tokenize(text)


def test_compound_identifiers_are_indexed_both_whole_and_split() -> None:
    """The bug this fixes, pinned directly: a query using plain English words must find
    a chunk that only ever writes the compound sensor identifier, and vice versa."""
    toks = offline_tokenize("weld_current_a")
    assert "weld_current_a" in toks
    assert "weld" in toks
    assert "current" in toks


# ------------------------------------------------------------------------- offline build
def test_offline_build_produces_one_row_per_section() -> None:
    stats = offline_build()
    expected_chunks = sum(len(d.sections) for d in CORPUS)
    assert stats["documents"] == len(CORPUS)
    assert stats["chunks"] == expected_chunks
    assert stats["vocab_size"] > 0

    npz = np.load(ROOT / "artifacts" / "knowledge_index.npz")
    assert npz["matrix"].shape == (expected_chunks, stats["vocab_size"])
    assert len(npz["vocab"]) == stats["vocab_size"]
    assert len(npz["idf"]) == stats["vocab_size"]


def test_every_chunk_vector_is_unit_normalized_or_zero() -> None:
    """Cosine similarity via a plain dot product depends on this -- a matrix row that
    was not normalized would silently outrank shorter, more relevant chunks."""
    npz = np.load(ROOT / "artifacts" / "knowledge_index.npz")
    norms = np.linalg.norm(npz["matrix"], axis=1)
    assert np.all((np.abs(norms - 1.0) < 1e-6) | (norms == 0))


# --------------------------------------------------------------------------- KnowledgeIndex
@pytest.fixture(scope="module")
def index() -> KnowledgeIndex:
    offline_build()
    return KnowledgeIndex(
        ROOT / "artifacts" / "knowledge_index.npz",
        ROOT / "artifacts" / "knowledge_corpus.json",
    )


def test_a_fault_code_query_ranks_its_own_named_document_first(index: KnowledgeIndex) -> None:
    """The regression this whole module exists to prevent: FC-301's own heading is
    literally "Weld current anomaly", and it must not lose to an unrelated document that
    happens to phrase a hyphenated variant of the same words."""
    results = index.search("weld current anomaly", limit=3)
    assert results, "expected at least one result"
    assert results[0]["doc_id"] == "FC-01"
    assert results[0]["section_id"] == "FC-01-4"


def test_results_are_sorted_descending_by_score(index: KnowledgeIndex) -> None:
    results = index.search("torque vibration equipment degradation", limit=5)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_limit_is_respected_and_bounded(index: KnowledgeIndex) -> None:
    assert len(index.search("torque", limit=1)) <= 1
    assert len(index.search("torque", limit=100)) <= len(index.rows)


def test_an_empty_or_nonmatching_query_returns_no_results(index: KnowledgeIndex) -> None:
    assert index.search("", limit=3) == []
    assert index.search("zzz qqq nonexistent xkcd", limit=3) == []


def test_every_result_carries_a_citable_text_field(index: KnowledgeIndex) -> None:
    """genai/verifier.py's knowledge-citation check resolves a claim's quoted_text
    against this exact field -- it must always be present and be the real chunk body,
    not the heading or title."""
    for r in index.search("paint buffer dwell", limit=3):
        assert isinstance(r["text"], str) and r["text"]
        assert r["doc_id"] and r["section_id"]
