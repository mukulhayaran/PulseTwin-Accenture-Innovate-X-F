"""Conversation threads, and the one concession they required (ADR-039).

The rule this file defends, in one line: **a citation is scoped to one run; only a
VERIFIED VALUE may cross a turn, and only as a prose exemption.**

`genai/loop.py` assigns `call_id` per run (`call_0001` upward, restarting every turn), so
turn 1 and turn 3 both have a `call_0003` pointing at different tool results. If an
earlier turn's citation were replayed as usable, a claim could resolve against THIS
turn's transcript into an unrelated number that happens to land inside the verifier's
rounding tolerance -- and be served with a green `verified` badge on a coincidence. That
is the worst failure this feature can produce and it is silently reachable, so the tests
below pin both halves: claims never cross, prose values do.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("fastapi")

if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from genai import prompts  # noqa: E402
from genai import store  # noqa: E402
from genai import verifier as V  # noqa: E402
from genai.finding import Citation, Claim, Finding  # noqa: E402

TRANSCRIPT = {"call_0001": {"ok": True, "data": {"throughput_uph": 52.4}}}


def _finding(summary: str, claims: list[Claim], recommendation: str = "Act.") -> Finding:
    return Finding(question="q", summary=summary, claims=claims,
                   recommendation=recommendation)


def _good_claim() -> Claim:
    return Claim(id="c1", text="Throughput is 52.4 UPH", value=52.4, unit="uph",
                 provenance="measured",
                 citation=Citation("call_0001", "/data/throughput_uph"))


# ----------------------------------------------------- carried values exempt PROSE only
def test_an_earlier_value_rescues_a_sentence() -> None:
    """Without this, every follow-up turn is visibly shredded: "the 41.8 UPH we
    established is unchanged" loses its whole sentence to the C3 sweep."""
    finding = _finding(
        "The 41.8 UPH we established earlier is unchanged.", [_good_claim()])

    ver, served = V.verify(finding, TRANSCRIPT, carried_values={41.8})
    assert ver.status == V.VERIFIED
    assert "[sentence removed" not in served.summary
    assert ver.carried_values_used == 1


def test_without_the_carried_set_that_sentence_is_still_removed() -> None:
    finding = _finding(
        "The 41.8 UPH we established earlier is unchanged.", [_good_claim()])

    ver, served = V.verify(finding, TRANSCRIPT)
    assert "[sentence removed" in served.summary
    assert ver.carried_values_used == 0


def test_a_carried_value_can_never_verify_a_CLAIM() -> None:
    """The load-bearing half. A claim citing nothing in THIS run's transcript must fail
    however familiar its number is -- `_verify_claim` is untouched on purpose."""
    stale = Claim(id="c1", text="Throughput is 41.8 UPH", value=41.8, unit="uph",
                  provenance="measured",
                  citation=Citation("call_0003", "/data/throughput_uph"))
    ver, _served = V.verify(_finding("Summary.", [stale]), TRANSCRIPT,
                            carried_values={41.8})

    assert ver.status == V.REJECTED
    assert ver.claim_verdicts[0].code == V.UNKNOWN_CALL


def test_a_number_from_no_turn_at_all_is_still_removed() -> None:
    finding = _finding("Savings reached 99.7 units.", [_good_claim()])
    ver, served = V.verify(finding, TRANSCRIPT, carried_values={41.8})
    assert "[sentence removed" in served.summary
    assert any("99.7" in issue for issue in ver.prose_issues)


def test_carrying_does_not_weaken_the_contract_checks() -> None:
    """C4 is categorical. A carried value cannot buy an unsourced dollar figure."""
    money = Claim(id="c1", text="Saves 52.4 dollars", value=52.4, unit="usd",
                  provenance="measured",
                  citation=Citation("call_0001", "/data/throughput_uph"), source=None)
    ver, _ = V.verify(_finding("s", [money]), TRANSCRIPT, carried_values={52.4})
    assert ver.status == V.REJECTED
    assert ver.reject_reason.startswith(V.UNSOURCED_CURRENCY)


def test_carrying_does_not_weaken_operator_privacy() -> None:
    ver, _ = V.verify(
        _finding("operator_id 4471 was involved.", [_good_claim()]),
        TRANSCRIPT, carried_values={4471.0})
    assert ver.status == V.REJECTED


# --------------------------------------------------------- building the carried set
class _FakeCon:
    """Minimal stand-in for a db handle: serve.db.query_all just runs .execute."""

    def __init__(self, rows):
        self.rows = rows


@pytest.fixture
def patched_query(monkeypatch):
    captured = {}

    def fake_query_all(con, sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return con.rows

    monkeypatch.setattr(store.db, "query_all", fake_query_all)
    return captured


def _row(turn, status, claims, summary="s", reco="r"):
    return {
        "turn_index": turn, "question": f"q{turn}", "intent": "ask", "status": status,
        "finding": json.dumps({"summary": summary, "recommendation": reco,
                               "claims": claims}),
    }


def test_only_verified_turns_contribute(patched_query) -> None:
    """A rejected turn served nothing, so it has nothing to carry. The SQL says so; this
    asserts the SQL says so."""
    store.load_thread_context(_FakeCon([]), "t1", before_turn=3)
    assert "verified" in patched_query["sql"]
    assert "verified_with_redactions" in patched_query["sql"]
    assert "rejected" not in patched_query["sql"]


def test_redacted_claims_do_not_contribute_their_placeholder_zero(patched_query) -> None:
    """A redacted claim carries `value: 0.0`. Admitting it would exempt EVERY sentence
    containing a zero in every later turn -- the easiest possible way to accidentally
    disarm the uncited-number sweep."""
    con = _FakeCon([_row(0, "verified_with_redactions", [
        {"text": "[claim removed: value_mismatch]", "value": 0.0},
        {"text": "Throughput is 52.4 UPH", "value": 52.4},
    ])])
    ctx = store.load_thread_context(con, "t1", before_turn=1)

    assert ctx.carried_values == [52.4]
    assert 0.0 not in ctx.carried_values
    assert ctx.turns[0].claim_texts == ["Throughput is 52.4 UPH"]


def test_the_carried_set_is_bounded(patched_query) -> None:
    """A thread must not grow into a 40k-token prompt: turn 12 should cost what turn 2
    cost."""
    from genai import config

    many = [{"text": f"v{i}", "value": float(i)} for i in range(50)]
    ctx = store.load_thread_context(_FakeCon([_row(0, "verified", many)]),
                                    "t1", before_turn=1)
    assert len(ctx.turns[0].claim_texts) == config.MAX_THREAD_CLAIMS_PER_TURN
    assert patched_query["params"][-1] == config.MAX_THREAD_TURNS


def test_a_corrupt_finding_blob_is_skipped_not_fatal(patched_query) -> None:
    con = _FakeCon([{"turn_index": 0, "question": "q", "intent": "ask",
                     "status": "verified", "finding": "{not json"}])
    ctx = store.load_thread_context(con, "t1", before_turn=1)
    assert ctx.turns == [] and ctx.carried_values == []


def test_booleans_are_not_carried_as_numbers(patched_query) -> None:
    con = _FakeCon([_row(0, "verified", [{"text": "flag", "value": True}])])
    assert store.load_thread_context(con, "t1", before_turn=1).carried_values == []


# ------------------------------------------------------------------ the replayed prompt
def test_an_empty_thread_renders_nothing() -> None:
    """Load-bearing: with no context, loop.build_messages produces a messages array
    byte-identical to the pre-thread one, so turn 0 runs the path the existing loop tests
    already cover."""
    assert prompts.render_thread_context([]) == ""


def test_the_replay_forbids_cross_turn_citations_in_words() -> None:
    turn = store.ThreadTurn(1, "why?", "ask", "verified", "S12 is the constraint.",
                            "Add buffer.", ["S12 loses 41.8 UPH"])
    text = prompts.render_thread_context([turn])

    assert "CITATIONS DO NOT CROSS TURNS" in text
    assert "CALL THE TOOL AGAIN NOW" in text
    # And it must not present itself as evidence.
    assert "NOT evidence" in text


def test_turn_zero_is_byte_identical_to_the_one_shot_prompt() -> None:
    from genai import loop as L

    before = L.build_messages(plant_id="plant_a", question="q", acting_role="manager")
    after = L.build_messages(plant_id="plant_a", question="q", acting_role="manager",
                             thread_context="", intent_guidance="")
    assert before == after
    assert len(before) == 2 and before[0]["role"] == "system"


def test_context_is_a_separate_message_from_the_question() -> None:
    from genai import loop as L

    msgs = L.build_messages(plant_id="plant_a", question="q", acting_role="manager",
                            thread_context="EARLIER", intent_guidance="GUIDE")
    assert [m["role"] for m in msgs] == ["system", "user", "user"]
    assert msgs[1]["content"] == "EARLIER"
    assert "GUIDE" in msgs[2]["content"] and "q" in msgs[2]["content"]


# ------------------------------------------------------------------ audit replay
#
# ADR-039 claims `python -m genai.verify_cli --run-id X` reproduces a turn's verdict
# byte-identically. Two things had to be true for that, and neither was:
#
#   * the CLI has to read `carried_values` back, or a threaded turn re-verifies under
#     different rules than it was served under and reports itself non-deterministic;
#   * its station-id set has to be the real one. It read
#     `SELECT DISTINCT station_id FROM sensor_health`, a CHANGE LOG that held 1 of 30
#     stations on plant_a and is frequently empty -- so every other station id in a
#     finding's prose failed the uncited-number sweep on replay but not when served.


def test_verify_cli_reads_the_real_station_set(monkeypatch, tmp_path) -> None:
    from genai import verify_cli
    from serve import db

    con, _ = db.open_ops_only()
    stations = verify_cli._station_ids(db, con, "plant_a")

    # The artifact has 30 stations on plant_a; sensor_health has had transitions for a
    # handful at most. Anything in single digits means the old query is back.
    assert len(stations) >= 20, (
        f"only {len(stations)} station ids available to the replay -- this is the "
        f"sensor_health bug, and it makes the verifier look non-deterministic"
    )


def test_a_missing_artifact_does_not_crash_the_replay(monkeypatch) -> None:
    """A replay that cannot find the config should still run -- stricter about station
    ids than the original, never an exception."""
    from genai import verify_cli
    from serve import db

    monkeypatch.setenv("PULSETWIN_ARTIFACTS", "/nonexistent/path")

    class _NoTable:
        pass

    def boom(*_a, **_k):
        raise RuntimeError("no such table: station_config")

    monkeypatch.setattr(db, "query_all", boom)
    assert verify_cli._station_ids(db, _NoTable(), "plant_a") == set()


def test_carried_values_survive_the_round_trip(monkeypatch, tmp_path) -> None:
    """What is written is what comes back, typed as floats and with no bools."""
    monkeypatch.setenv("PULSETWIN_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from genai import store
    from serve import db

    con = db.open_ops_db()
    store.create_run(con, run_id="rt1", plant_id="plant_a", question="q",
                     requested_by="manager", acting_role="manager", mode="deterministic",
                     thread_id="th1", turn_index=1, intent="ask")
    store.record_carried_values(con, "rt1", [41.8, 52.4, 3.0])

    row = db.query_one(con, "SELECT carried_values FROM triage_runs WHERE run_id=?",
                       ("rt1",))
    stored = json.loads(row["carried_values"]) if isinstance(row["carried_values"], str) \
        else row["carried_values"]
    assert sorted(float(v) for v in stored) == [3.0, 41.8, 52.4]
