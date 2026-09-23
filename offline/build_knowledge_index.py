"""Bake the engineering-knowledge corpus into a retrievable TF-IDF artifact (ADR-046).

Mirrors `offline/build_artifact.py`'s split: the corpus and its vocabulary/IDF weights
are computed HERE, offline, with numpy free to be as heavy as it likes. What `serve/`
loads at runtime is a dense matrix and a vocabulary array -- pure numpy dot products, no
sklearn, no embedding model, no network call. "Precompute heavy, serve thin," applied to
retrieval instead of invented for it.

Deterministic TF-IDF over lexical tokens, not dense embeddings, by design: this is a
demo-scale synthetic corpus (26 chunks) where exact domain terminology (torque_nm,
paint_buffer_dwell_s, station ids) matters more than semantic paraphrase matching, and it
keeps the whole knowledge tier reproducible with no model weights to download and no
runtime dependency beyond what `serve/`'s import wall already allows.

    python -m offline.build_knowledge_index
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

import numpy as np

from offline.knowledge.corpus import CORPUS

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"

# Same tokenizer used at query time (serve/knowledge_index.py) -- the two MUST agree, or
# a query and the corpus it searches would be projected into different vocabularies.
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    """Word tokens, PLUS the underscore-split parts of any compound identifier.

    Measured, not assumed: a first pass indexed only the raw regex match, and
    "weld current anomaly" ranked the wrong document -- FC-01's own text writes the
    signal as the single token `weld_current_a`, so a query using the separate English
    words "weld" and "current" got zero credit for it, while a document that happened to
    write "weld-current" in prose (splitting on the hyphen) won on a coincidence. Emitting
    both the compound token AND its parts (`weld_current_a` -> also `weld`, `current`)
    means a sensor name matches whichever way a question happens to phrase it.
    """
    toks = _TOKEN_RE.findall(text.lower())
    out = list(toks)
    for tok in toks:
        if "_" in tok:
            out.extend(part for part in tok.split("_") if len(part) >= 2)
    return out


def _chunks() -> list[dict]:
    """Flatten the corpus into one row per section, in a stable, deterministic order."""
    rows = []
    for doc in CORPUS:
        for section in doc.sections:
            rows.append({
                "doc_id": doc.doc_id,
                "doc_title": doc.title,
                "section_id": section.section_id,
                "heading": section.heading,
                "text": section.text,
            })
    return rows


def build() -> dict:
    rows = _chunks()
    if not rows:
        raise ValueError("corpus is empty")

    # Index the doc title and section heading alongside the body -- a query naming a
    # concept that appears in the TITLE ("calibration procedure") but not verbatim in a
    # given section's body must still be able to find it. `r["text"]` (returned to the
    # caller and cited by the verifier) stays the body alone; only the indexed tokens
    # include the title/heading.
    tokenized = [
        tokenize(f"{r['doc_title']} {r['heading']} {r['text']}") for r in rows
    ]
    vocab = sorted({tok for toks in tokenized for tok in toks})
    index = {tok: i for i, tok in enumerate(vocab)}
    n_docs, n_vocab = len(rows), len(vocab)

    # Document frequency -> IDF, smoothed (+1) so a term in every chunk still gets a
    # small positive weight rather than exactly zero.
    df = np.zeros(n_vocab, dtype=np.float64)
    for toks in tokenized:
        for tok in set(toks):
            df[index[tok]] += 1
    idf = np.log((1.0 + n_docs) / (1.0 + df)) + 1.0

    matrix = np.zeros((n_docs, n_vocab), dtype=np.float64)
    for row_i, toks in enumerate(tokenized):
        if not toks:
            continue
        counts = Counter(toks)
        max_count = max(counts.values())
        for tok, c in counts.items():
            # Augmented term frequency (0.5 + 0.5 * c/max_count) -- keeps a chunk's own
            # vector from being dominated by whichever single word repeats most, which
            # matters here because these chunks are short (often under 60 tokens).
            tf = 0.5 + 0.5 * (c / max_count)
            matrix[row_i, index[tok]] = tf * idf[index[tok]]

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix = matrix / norms

    ARTIFACTS.mkdir(exist_ok=True)
    np.savez_compressed(
        ARTIFACTS / "knowledge_index.npz",
        # A plain `np.array(list_of_str)` infers a fixed-width unicode dtype rather than
        # `object`, so this loads with numpy's default `allow_pickle=False` -- no pickle
        # anywhere in this artifact, unlike model_<plant>.npz's state-bucket arrays.
        vocab=np.array(vocab),
        idf=idf.astype(np.float32),
        matrix=matrix.astype(np.float32),
    )
    (ARTIFACTS / "knowledge_corpus.json").write_text(
        json.dumps(rows, indent=None), encoding="utf-8")

    return {"documents": len(CORPUS), "chunks": n_docs, "vocab_size": n_vocab}


def main() -> int:
    stats = build()
    print(f"[knowledge] {stats['documents']} documents, {stats['chunks']} chunks, "
          f"{stats['vocab_size']} vocabulary terms -> "
          f"{ARTIFACTS / 'knowledge_index.npz'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
