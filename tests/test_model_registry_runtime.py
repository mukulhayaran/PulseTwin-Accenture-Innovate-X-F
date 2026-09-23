"""serve/main.py's registry <-> runtime-scorer seam (ADR-041).

Before this fix, `lifespan()` always loaded `artifacts/model_<plant>.npz` and never
consulted `model_registry` -- so `admin.py::_activate_scorer`'s promotion hot-swap lived
only in that one process's memory, and a restart silently reverted to the pre-promotion
model while the registry kept reporting the promoted challenger as champion. This is the
one open finding docs/STATE.md calls out as "the model that serves traffic is not in the
registry" -- and, worse than that finding states, a *promoted* model didn't survive a
restart either. Both directions are tested here against the real committed artifact, no
mocks.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]

if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from serve import db as servedb  # noqa: E402
from serve import main as servemain  # noqa: E402
from serve.main import app  # noqa: E402


@pytest.fixture()
def isolated_ops_db(tmp_path, monkeypatch):
    """A model-registry test must not read the shared dev tempdir ops DB -- STATE.md's
    documented test-isolation weakness (bugs #69/#70): that file is long-lived and never
    rebuilt, so a prior manual run's rows leak into whichever test runs next."""
    monkeypatch.setenv("PULSETWIN_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    yield tmp_path


def test_startup_bootstraps_a_registry_row_for_the_committed_model(isolated_ops_db):
    with TestClient(app):
        row = servedb.query_one(
            app.state.ops,
            "SELECT * FROM model_registry WHERE model_id='plant_a_defect_risk_committed'",
        )
        assert row is not None
        assert row["status"] == "champion"
        assert row["plant_id"] == "plant_a"
        assert row["artifact_path"]
        metrics = json.loads(row["metrics"])
        # Real, measured values from the metrics table -- not fabricated placeholders.
        assert metrics["pr_auc"] == pytest.approx(0.2559, abs=0.01)
        assert "plant_a" in app.state.scorers
        fallback = servedb.query_one(
            app.state.ops,
            "SELECT * FROM model_registry WHERE model_id='plant_a_spc_fallback'",
        )
        assert fallback is not None
        assert fallback["model_type"] == "spc_fallback"


def test_bootstrap_is_idempotent_across_restarts(isolated_ops_db):
    """A restart against the same ops DB must not double-insert the bootstrap row --
    it would collide on the PRIMARY KEY the moment a second plant's row also existed,
    exactly the UNIQUE-constraint failure mode STATE.md bug #70 already found once."""
    with TestClient(app):
        pass
    with TestClient(app):
        rows = servedb.query_all(
            app.state.ops,
            "SELECT model_id FROM model_registry WHERE model_id='plant_a_defect_risk_committed'",
        )
        assert len(rows) == 1


def test_resolve_champion_prefers_a_registry_row_over_the_committed_default(isolated_ops_db, tmp_path):
    """The exact seam: once a champion row exists (as it does after any promotion --
    admin.py writes one, mlops.retrainer.run() writes one), startup must load ITS
    artifact_path, not silently fall back to the committed file, or a promotion would
    not survive a restart."""
    with TestClient(app):
        pass  # first boot bootstraps plant_a/plant_b as champions of the committed artifact

    alt_artifact = tmp_path / "model_plant_a_alt.npz"
    shutil.copy(ROOT / "artifacts" / "model_plant_a.npz", alt_artifact)

    con, ops, _backend = servedb.open_database(ROOT / "artifacts" / "pulsetwin.db")
    servedb.execute(
        ops,
        "UPDATE model_registry SET status='retired', retired_at='2026-09-21T00:00:00Z' "
        "WHERE plant_id='plant_a' AND status='champion'",
    )
    servedb.execute(
        ops,
        """INSERT INTO model_registry
               (model_id, model_type, version, status, plant_id, trained_at,
                training_data_range, metrics, artifact_path, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("plant_a_defect_risk_test_challenger", "defect_risk", 2, "champion", "plant_a",
         "2026-09-21T00:00:00Z", "test", "{}", str(alt_artifact), "test-injected champion"),
    )
    con.close()

    # Exercise the resolver directly against this connection -- precise, and avoids
    # needing the Scorer to expose which file it was loaded from just to assert on it.
    servemain.STATE["ops"] = ops
    try:
        resolved_path, champion_id = servemain._resolve_champion_artifact(
            "plant_a", ROOT / "artifacts" / "model_plant_a.npz"
        )
    finally:
        ops.close()

    assert champion_id == "plant_a_defect_risk_test_challenger"
    assert resolved_path == alt_artifact


def test_promotion_via_the_real_api_survives_a_restart(isolated_ops_db):
    """The full path an operator actually drives: POST /api/admin/promote-model, then a
    restart. Manually reproduced once against a real `docker kill` + restart of the
    backend container in docker-compose.prod.yml against real Postgres -- this is the same
    scenario against SQLite and the TestClient, so it runs in the fast suite too.

    Also pins `persists_across_restart` in the response, which was hardcoded `False` in
    `admin.py::_activate_scorer` until this pass -- true before ADR-041, silently wrong
    after it, and nothing had asserted on it either way.
    """
    with TestClient(app):
        pass  # bootstrap plant_a as champion of the committed artifact

    con, ops, _backend = servedb.open_database(ROOT / "artifacts" / "pulsetwin.db")
    servedb.execute(
        ops,
        """INSERT INTO model_registry
               (model_id, model_type, version, status, plant_id, trained_at,
                training_data_range, metrics, artifact_path, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("plant_a_defect_risk_promo_test", "defect_risk", 2, "challenger", "plant_a",
         "2026-09-21T00:00:00Z", "test", "{}",
         str(ROOT / "artifacts" / "model_plant_a.npz"), "promotion round-trip test"),
    )
    con.close()

    with TestClient(app, headers={"X-PulseTwin-Role": "system"}) as client:
        resp = client.post("/api/admin/promote-model",
                            json={"model_id": "plant_a_defect_risk_promo_test"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["promoted"] == "plant_a_defect_risk_promo_test"
        assert body["serving"]["swapped"] is True
        assert body["serving"]["persists_across_restart"] is True

    # "Restart": exit and re-enter the lifespan context, same as the process ending and a
    # new one starting -- nothing here is a mock of that boundary.
    with TestClient(app) as client:
        row = servedb.query_one(
            app.state.ops,
            "SELECT model_id FROM model_registry WHERE plant_id='plant_a' AND status='champion'",
        )
        assert row["model_id"] == "plant_a_defect_risk_promo_test"
        pred = client.post("/api/predict/defect",
                            json={"plant_id": "plant_a", "unit_id": "U030733"},
                            headers={"X-PulseTwin-Role": "supervisor"})
        assert pred.status_code == 200, pred.text


def test_resolve_champion_falls_back_when_the_registry_artifact_is_missing(isolated_ops_db):
    """A registry row surviving a copy to a machine without the challenger .npz files must
    not leave a plant unscored -- fall back to the committed artifact rather than crash."""
    with TestClient(app):
        pass

    con, ops, _backend = servedb.open_database(ROOT / "artifacts" / "pulsetwin.db")
    servedb.execute(
        ops,
        "UPDATE model_registry SET artifact_path='/nonexistent/model_ghost.npz' "
        "WHERE model_id='plant_a_defect_risk_committed'",
    )

    servemain.STATE["ops"] = ops
    committed = ROOT / "artifacts" / "model_plant_a.npz"
    try:
        resolved_path, champion_id = servemain._resolve_champion_artifact("plant_a", committed)
    finally:
        ops.close()
        con.close()

    assert resolved_path == committed
    assert champion_id is None
