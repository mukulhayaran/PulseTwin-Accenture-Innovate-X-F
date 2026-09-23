"""Pure-numpy TF-IDF retrieval over the engineering-knowledge corpus (ADR-046).

THE IMPORT WALL (CLAUDE.md §2). numpy and stdlib only -- the corpus, vocabulary and IDF
weights are baked offline by `offline/build_knowledge_index.py` into
`artifacts/knowledge_index.npz` + `artifacts/knowledge_corpus.json`; this module only
projects a query into that precomputed space and ranks by cosine similarity. Same split
as `serve/scorer.py`: heavy computation offline, thin numpy walk at request time.

`tokenize()` below MUST stay byte-identical to `offline/build_knowledge_index.py`'s
version -- a query tokenized differently than the corpus it searches would be projected
into a mismatched vocabulary and silently return nothing relevant.
`tests/test_knowledge_index.py::test_serve_and_offline_tokenizers_agree` pins this the
same way `tests/test_genai_progress.py::test_serve_and_genai_agree_on_the_phase_trail`
pins PHASE_ORDER -- `serve/` cannot import `offline/` (the import wall), so duplication
plus a cross-check test is the established pattern here, not an oversight.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import numpy as np

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    toks = _TOKEN_RE.findall(text.lower())
    out = list(toks)
    for tok in toks:
        if "_" in tok:
            out.extend(part for part in tok.split("_") if len(part) >= 2)
    return out


class KnowledgeIndex:
    """The corpus, its TF-IDF matrix, and search. Load once at startup."""

    def __init__(self, npz_path: str | Path, corpus_path: str | Path) -> None:
        z = np.load(npz_path)
        self.vocab = [str(v) for v in z["vocab"]]
        self.idf = z["idf"]
        self.matrix = z["matrix"]
        self._index = {tok: i for i, tok in enumerate(self.vocab)}
        self.rows = json.loads(Path(corpus_path).read_text(encoding="utf-8"))
        if len(self.rows) != self.matrix.shape[0]:
            raise ValueError(
                f"knowledge corpus/index row mismatch: {len(self.rows)} rows vs "
                f"{self.matrix.shape[0]} matrix rows -- rebuild with "
                f"`python -m offline.build_knowledge_index`")

    def _vector(self, query: str) -> np.ndarray:
        toks = tokenize(query)
        vec = np.zeros(len(self.vocab), dtype=np.float64)
        counts = Counter(t for t in toks if t in self._index)
        if not counts:
            return vec
        max_count = max(counts.values())
        for tok, c in counts.items():
            i = self._index[tok]
            tf = 0.5 + 0.5 * (c / max_count)
            vec[i] = tf * self.idf[i]
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    def search(self, query: str, limit: int = 3) -> list[dict]:
        """Top-`limit` chunks by cosine similarity, highest score first.

        Returns `[]` for an empty/no-match query rather than an arbitrary top-k of
        unrelated chunks -- a zero vector's dot product with everything is exactly 0.0,
        so this falls out of the ranking rather than needing a special case.
        """
        vec = self._vector(query)
        if not np.any(vec):
            return []
        scores = self.matrix @ vec
        order = np.argsort(-scores)[: max(1, limit)]
        return [
            {
                "doc_id": self.rows[i]["doc_id"],
                "doc_title": self.rows[i]["doc_title"],
                "section_id": self.rows[i]["section_id"],
                "heading": self.rows[i]["heading"],
                "text": self.rows[i]["text"],
                "score": round(float(scores[i]), 4),
            }
            for i in order if scores[i] > 0
        ]
