"""LOOP criterion G: the AI stage track is derived from the persisted record only."""

from serve.incident_story import stages

INC = {"source": "watch", "station_id": 19}


def _by_key(rows):
    return {r["key"]: r["status"] for r in rows}


def test_waiting_for_telemetry():
    inv = {"status": "awaiting_telemetry", "state": {"phase": "awaiting_telemetry"}}
    s = _by_key(stages(INC, inv, None, []))
    assert s == {"detect": "done", "investigate": "active", "hypothesize": "pending",
                 "simulate": "pending", "recommend": "pending", "approve": "pending",
                 "verify": "pending"}


def test_twin_running_then_awaiting_manager_then_verifying():
    hyps = [{"status": "supported"}, {"status": "rejected"}]
    inv = {"status": "awaiting_simulation",
           "state": {"phase": "awaiting_simulation", "evidence": [1, 2], "hypotheses": hyps,
                     "experiments": [{"status": "running"}]}}
    s = _by_key(stages(INC, inv, None, []))
    assert (s["investigate"], s["hypothesize"], s["simulate"]) == ("done", "done", "active")

    inv = {"status": "completed",
           "state": {"phase": "completed", "evidence": [1], "hypotheses": hyps,
                     "experiments": [{"status": "completed", "baseline_uph": 55.6}]}}
    s = _by_key(stages(INC, inv, {"status": "proposed"}, []))
    assert (s["simulate"], s["recommend"], s["approve"], s["verify"]) == ("done", "done", "active", "pending")

    acts = [{"outcome": {"status": "observing"}}]
    s = _by_key(stages(INC, inv, {"status": "executed", "decided_by_role": "manager"}, acts))
    assert (s["approve"], s["verify"]) == ("done", "active")
    acts = [{"outcome": {"status": "partially_successful"}}]
    assert _by_key(stages(INC, inv, {"status": "executed"}, acts))["verify"] == "done"


def test_failed_twin_test_is_shown_as_skipped_not_done():
    inv = {"status": "completed", "state": {"phase": "completed", "evidence": [1],
                                            "hypotheses": [{"status": "supported"}],
                                            "experiments": [{"status": "failed"}]}}
    row = next(r for r in stages(INC, inv, {"status": "proposed"}, []) if r["key"] == "simulate")
    assert row["status"] == "skipped" and "did not complete" in row["detail"]


def test_track_never_moves_backwards_after_a_twin_test():
    """The commander returns to planning after a twin test; the track must not show
    Hypothesize as active again once Simulate is done (seen in the UI run)."""
    inv = {"status": "running",
           "state": {"phase": "plan", "evidence": [1], "hypotheses": [{"status": "supported"}],
                     "experiments": [{"status": "done", "baseline_uph": 36.3}]}}
    s = _by_key(stages(INC, inv, None, []))
    assert (s["investigate"], s["hypothesize"], s["simulate"]) == ("done", "done", "done")
