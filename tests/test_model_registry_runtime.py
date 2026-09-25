"""serve/main.py's registry <-> runtime-scorer seam.

The committed seed-42 model is registered as champion at startup, so `model_registry` is
never silent about the model answering /api/predict/defect. Since ADR-053 removed
retraining and promotion, that committed model is the ONLY one served -- a registry row
can describe it but can no longer select a different artifact. Tested against the real
committed artifact, no mocks.
"""

from __future__ import annotations

import json
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


def test_a_stale_champion_row_cannot_change_the_served_model(isolated_ops_db):
    """ADR-053: with promotion gone, the committed seed-42 model is the only one served.
    A champion row left over from before -- e.g. in an ops DB carried across -- must not
    select a different artifact, and the model reported must be the committed one."""
    with TestClient(app):
        servedb.execute(
            app.state.ops,
            """INSERT INTO model_registry (model_id, model_type, version, status, plant_id,
                   trained_at, metrics, artifact_path) VALUES (?,?,?,?,?,?,?,?)""",
            ("plant_a_defect_v_stale", "defect_risk", 9, "champion", "plant_a",
             "2099-01-01T00:00:00Z", "{}", str(ROOT / "artifacts" / "model_plant_a.npz")),
        )
    with TestClient(app):
        assert app.state.model_info["plant_a"]["model_id"] == "plant_a_defect_risk_committed"
