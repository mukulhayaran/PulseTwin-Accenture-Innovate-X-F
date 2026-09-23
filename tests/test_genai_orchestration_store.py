"""Persistence for multi-agent orchestration (ADR-045).

Specialist attribution must survive the move from the live heartbeat to the persisted
ledger, or an orchestrated run's tool-call trail loses which agent gathered which fact the
instant it stops being "in flight". These tests exercise the real SQLite ops DB, same
pattern as tests/test_genai_threads.py::test_carried_values_survive_the_round_trip.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


def test_record_tool_calls_persists_the_agent_column(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PULSETWIN_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from genai import store
    from serve import db

    @dataclass
    class _Rec:
        call_id: str
        seq: int
        name: str
        arguments: dict
        result: dict
        duration_ms: float
        phase: str = ""
        agent: str = ""

    con = db.open_ops_db()
    store.create_run(con, run_id="ag1", plant_id="plant_a", question="q",
                     requested_by="manager", acting_role="manager", mode="agent",
                     intent="incident")
    store.record_tool_calls(con, "ag1", [
        _Rec("call_0001", 1, "get_system_health", {}, {"ok": True}, 5.0,
             phase="Examining risk signals", agent="reliability"),
        _Rec("call_0002", 2, "get_bottleneck_current", {}, {"ok": True}, 7.0,
             phase="Checking production state", agent="production"),
    ])

    rows = db.query_all(
        con, "SELECT call_id, agent FROM triage_tool_calls WHERE run_id=? ORDER BY seq",
        ("ag1",))
    assert [(r["call_id"], r["agent"]) for r in rows] == [
        ("call_0001", "reliability"), ("call_0002", "production"),
    ]


def test_record_tool_calls_leaves_agent_null_for_a_single_agent_run(
    monkeypatch, tmp_path
) -> None:
    """A non-orchestrated run's records carry agent="" (ToolRecord's default) -- this
    must persist as NULL, not the literal empty string, so a query for "which runs have
    agent attribution" can use IS NOT NULL cleanly."""
    monkeypatch.setenv("PULSETWIN_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from genai import store
    from serve import db

    @dataclass
    class _Rec:
        call_id: str
        seq: int
        name: str
        arguments: dict
        result: dict
        duration_ms: float
        phase: str = ""
        agent: str = ""

    con = db.open_ops_db()
    store.create_run(con, run_id="sa1", plant_id="plant_a", question="q",
                     requested_by="manager", acting_role="manager", mode="agent",
                     intent="ask")
    store.record_tool_calls(con, "sa1", [_Rec("call_0001", 1, "get_metrics", {}, {}, 1.0)])

    row = db.query_one(con, "SELECT agent FROM triage_tool_calls WHERE run_id=?", ("sa1",))
    assert row["agent"] is None


def test_record_agents_used_sets_orchestrated_and_the_roster(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PULSETWIN_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from genai import store
    from serve import db

    con = db.open_ops_db()
    store.create_run(con, run_id="ag2", plant_id="plant_a", question="q",
                     requested_by="manager", acting_role="manager", mode="agent",
                     intent="incident")
    agents = [
        {"key": "reliability", "label": "Reliability", "status": "ok", "tool_calls": 2},
        {"key": "production", "label": "Production", "status": "ok", "tool_calls": 3},
        {"key": "risk", "label": "Risk", "status": "partial", "tool_calls": 1},
    ]
    store.record_agents_used(con, "ag2", agents)

    row = db.query_one(
        con, "SELECT orchestrated, agents_used FROM triage_runs WHERE run_id=?", ("ag2",))
    assert row["orchestrated"] in (1, True)
    stored = json.loads(row["agents_used"]) if isinstance(row["agents_used"], str) \
        else row["agents_used"]
    assert stored == agents


def test_a_run_with_no_agents_used_call_stays_unorchestrated(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PULSETWIN_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from genai import store
    from serve import db

    con = db.open_ops_db()
    store.create_run(con, run_id="sa2", plant_id="plant_a", question="q",
                     requested_by="manager", acting_role="manager", mode="agent",
                     intent="ask")

    row = db.query_one(
        con, "SELECT orchestrated, agents_used FROM triage_runs WHERE run_id=?", ("sa2",))
    assert row["orchestrated"] in (0, False, None)
    assert row["agents_used"] is None
