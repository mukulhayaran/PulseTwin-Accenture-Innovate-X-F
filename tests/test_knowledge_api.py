"""GET /api/knowledge/search (ADR-046).

Run against the real committed knowledge artifact, no mocks -- same convention as
tests/test_intelligence_api.py for the other deterministic evidence endpoints.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("fastapi")

if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from serve.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_search_returns_ranked_results(client) -> None:
    r = client.get("/api/knowledge/search",
                   params={"query": "weld current anomaly", "limit": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["results"], "expected at least one result for a query the corpus covers"
    assert body["results"][0]["doc_id"] == "FC-01"


def test_search_result_shape_carries_everything_a_citation_needs(client) -> None:
    r = client.get("/api/knowledge/search", params={"query": "torque drift"})
    top = r.json()["results"][0]
    for key in ("doc_id", "doc_title", "section_id", "heading", "text", "score"):
        assert key in top


def test_limit_is_clamped_not_rejected(client) -> None:
    r = client.get("/api/knowledge/search", params={"query": "torque", "limit": 999})
    assert r.status_code == 200
    assert len(r.json()["results"]) <= 8  # serve/routers/knowledge.py's clamp


def test_no_role_gate_on_a_non_sensitive_reference_endpoint(client) -> None:
    """Engineering reference content carries no operator, cost, or model-health data --
    unlike every other tool-backing endpoint, it needs no role header at all."""
    r = client.get("/api/knowledge/search", params={"query": "torque"})
    assert r.status_code == 200


def test_an_unmatched_query_returns_an_empty_list_not_an_error(client) -> None:
    r = client.get("/api/knowledge/search", params={"query": "zzz nonexistent qqq"})
    assert r.status_code == 200
    assert r.json()["results"] == []
