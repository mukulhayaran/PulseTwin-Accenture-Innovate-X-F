"""GET /api/knowledge/search -- engineering SOP / fault-code retrieval (ADR-046).

Same division of labour `serve/routers/intelligence.py`'s docstring states for the other
evidence endpoints: this file decides which chunks are most relevant (deterministic,
testable TF-IDF ranking -- `serve/knowledge_index.py`); genai/ decides how, or whether, to
use them, and every quoted chunk of text a Finding cites is re-checked against the exact
bytes this endpoint returned (`genai/verifier.py`'s knowledge-citation check). If a
language model were trusted to recall or paraphrase a procedure from memory instead of
citing a chunk this endpoint actually served, there would be nothing to verify it against.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/knowledge/search")
def search(request: Request, query: str, limit: int = 3) -> dict[str, Any]:
    index = request.app.state.knowledge_index
    limit = max(1, min(int(limit), 8))
    if index is None:
        # Graceful degradation, same posture as a missing model artifact elsewhere in
        # this file's family: report unavailable rather than 500, and never crash a run
        # over a corpus that was never built (e.g. a fresh checkout before
        # `python -m offline.build_knowledge_index` has run).
        return {"query": query, "results": [], "available": False}
    return {"query": query, "results": index.search(query, limit=limit), "available": True}
