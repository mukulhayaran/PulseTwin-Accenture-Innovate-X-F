"""A triage worker that raises must mark its run failed, not leave it `running`."""

from __future__ import annotations

from genai import triage


def test_an_exception_in_the_worker_marks_the_run_failed(monkeypatch) -> None:
    finished: dict = {}
    beats: list[dict] = []

    def boom(**_kw):
        raise ModuleNotFoundError("No module named 'openai'")

    monkeypatch.setattr(triage, "run", boom)
    monkeypatch.setattr(triage, "db_handle", lambda: (None, None))
    monkeypatch.setattr(triage.store, "finish_run",
                        lambda con, run_id, **kw: finished.update(run_id=run_id, **kw))
    monkeypatch.setattr(triage, "_heartbeat", lambda run_id, payload: beats.append(payload))

    code = triage.main(["--plant", "plant_a", "--intent", "handover", "--run-id", "r1"])

    assert code == 1
    assert finished["run_id"] == "r1" and finished["status"] == "failed"
    assert "ModuleNotFoundError" in finished["error"]
    assert beats and beats[-1]["done"] is True and beats[-1]["status"] == "failed"
