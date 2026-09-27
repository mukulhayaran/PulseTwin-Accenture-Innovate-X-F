"""ADR-064: when the AI provider is UNAVAILABLE, a run that permits it falls back to the
verified deterministic plan -- and says so on the row and in the finding. An explicit
`agent` request, and a credential problem, still fail visibly."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from genai import loop as L
from genai import store, triage as TG
from genai.api_client import PulseTwinAPI
from genai.api_client import testclient_transport as _tc_transport
from serve import db
from serve.main import app


@pytest.fixture()
def worker(monkeypatch, tmp_path):
    monkeypatch.setenv("PULSETWIN_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("PULSETWIN_JOBS_DIR", str(tmp_path / "jobs"))
    with TestClient(app) as c:
        monkeypatch.setattr(TG, "PulseTwinAPI", lambda base, role, run_id: PulseTwinAPI(
            "http://testserver", role, run_id, transport=_tc_transport(c)))
        monkeypatch.setattr(TG.provider, "credential_present", lambda: True)
        monkeypatch.setattr(TG.provider, "build_client", lambda: object())
        yield c


def _unavailable(error: str):
    def fake_loop(*a, **kw):
        return L.LoopResult(status="failed", content="", error=error)
    return fake_loop


def _run(run_id: str, **kw):
    con, _ = db.open_ops_only()
    store.create_run(con, run_id=run_id, plant_id="plant_a", question="Generate shift handover",
                     requested_by="manager", acting_role="manager", mode="agent",
                     thread_id=None, turn_index=0, intent="handover", station_id=None)
    out = TG.run(plant_id="plant_a", question="Generate shift handover", run_id=run_id,
                 api_base="http://testserver", acting_role="manager", requested_by="manager",
                 intent="handover", **kw)
    row = db.query_one(con, "SELECT * FROM triage_runs WHERE run_id=?", (run_id,))
    return out, row


def test_a_rate_limited_provider_falls_back_to_the_deterministic_plan(worker, monkeypatch):
    monkeypatch.setattr(TG.L, "run_loop", _unavailable("rate_limited"))
    out, row = _run("run-fb-1", mode="agent", allow_fallback=True)
    assert out["status"] in ("verified", "verified_with_redactions")
    assert row["mode"] == "deterministic"
    finding = json.loads(row["finding"]) if isinstance(row["finding"], str) else row["finding"]
    assert "DETERMINISTIC FALLBACK" in finding["unanswered"][0]
    assert "rate_limited" in finding["unanswered"][0]


def test_an_explicit_agent_request_still_fails_visibly(worker, monkeypatch):
    monkeypatch.setattr(TG.L, "run_loop", _unavailable("rate_limited"))
    out, row = _run("run-fb-2", mode="agent", allow_fallback=False)
    assert out["status"] == "rejected" and out["reject_reason"] == "rate_limited"
    assert row["mode"] == "agent"


def test_a_credential_error_is_not_papered_over(worker, monkeypatch):
    monkeypatch.setattr(TG.L, "run_loop", _unavailable("provider_status_401"))
    out, row = _run("run-fb-3", mode="agent", allow_fallback=True)
    assert out["status"] == "rejected" and row["mode"] == "agent"
