"""ADR-052: a deployment with PULSETWIN_REQUIRE_POSTGRES=1 and no DATABASE_URL must fail at
startup, never fall back to the read-only SQLite build artifact."""

from __future__ import annotations

from pathlib import Path

import pytest

from serve import db

ARTIFACT = Path(__file__).resolve().parents[1] / "artifacts" / "pulsetwin.db"


@pytest.fixture
def no_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_open_database_refuses_sqlite_when_postgres_is_required(monkeypatch, no_database_url):
    monkeypatch.setenv("PULSETWIN_REQUIRE_POSTGRES", "1")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        db.open_database(ARTIFACT)


def test_open_ops_only_refuses_sqlite_when_postgres_is_required(monkeypatch, no_database_url):
    monkeypatch.setenv("PULSETWIN_REQUIRE_POSTGRES", "1")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        db.open_ops_only()


def test_sqlite_stays_available_when_postgres_is_not_required(monkeypatch, tmp_path,
                                                              no_database_url):
    """Build and test runs never set the flag: SQLite must keep working for them."""
    if not ARTIFACT.exists():
        pytest.skip("artifacts not built")
    monkeypatch.delenv("PULSETWIN_REQUIRE_POSTGRES", raising=False)
    monkeypatch.setenv("PULSETWIN_OPS_DB", str(tmp_path / "ops.db"))
    _main, _ops, backend = db.open_database(ARTIFACT)
    assert backend == "sqlite"
