"""The import wall.

`serve/` may import only the allowlist below. Everything heavy -- pandas, sklearn,
xgboost, shap, scipy, simpy -- exists solely in the offline pipeline and must never reach
the runtime image.

CRITICAL DETAIL (docs/AUDIT.md M19): this checks FIRST-PARTY imports only, by parsing
each file's AST. It must not walk the transitive dependency graph -- fastapi itself
imports starlette, anyio, sniffio and typing_extensions, so a transitive check would fail
on the first run and tempt someone to weaken the very test that protects the constraint.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SERVE = Path(__file__).resolve().parents[1] / "serve"

ALLOWED = {
    # third-party, deliberately small
    "fastapi", "uvicorn", "pydantic", "numpy", "orjson",
    # ADR-033: I/O drivers and bookkeeping, not compute -- Postgres, cache, JWT, metrics.
    "psycopg", "psycopg_pool", "redis", "jose", "prometheus_client",
    "prometheus_fastapi_instrumentator",
    # ADR-036: starlette is fastapi's own ASGI base (BaseHTTPMiddleware has no fastapi
    # wrapper) -- a direct import, not the transitive graph this test deliberately avoids
    # walking. subprocess spawns the retrain job as a detached process rather than hosting
    # mlops/ in serve/ -- process management, not compute.
    "starlette", "subprocess",
    # stdlib
    "sqlite3", "json", "math", "pathlib", "os", "sys", "time", "asyncio", "typing",
    "datetime", "contextlib", "dataclasses", "logging", "re", "collections", "functools",
    "itertools", "hashlib", "enum", "csv", "io", "tempfile", "threading", "secrets",
    "decimal", "__future__",
    # Complete-demo readiness performs dependency TCP/HTTP probes with stdlib only.
    "socket", "urllib",
    # first-party
    "serve",
}

BANNED = {"pandas", "sklearn", "xgboost", "lightgbm", "shap", "scipy", "simpy",
          "matplotlib", "seaborn", "torch", "tensorflow", "psutil", "yaml", "offline"}


def _py_files() -> list[Path]:
    return sorted(SERVE.rglob("*.py"))


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:      # relative import, first-party by construction
                continue
            if node.module:
                found.add(node.module.split(".")[0])
    return found


def test_serve_directory_exists() -> None:
    assert _py_files(), "no python files under serve/"


@pytest.mark.parametrize("path", _py_files(), ids=lambda p: p.name)
def test_only_allowed_imports(path: Path) -> None:
    used = _top_level_imports(path)
    illegal = used - ALLOWED
    assert not illegal, (
        f"{path.relative_to(SERVE.parent)} imports outside the wall: {sorted(illegal)}"
    )


@pytest.mark.parametrize("path", _py_files(), ids=lambda p: p.name)
def test_no_banned_imports(path: Path) -> None:
    used = _top_level_imports(path)
    banned = used & BANNED
    assert not banned, f"{path.name} imports a banned heavy library: {sorted(banned)}"


def test_no_blocking_sleep_in_serve() -> None:
    """ADR-009. `time.sleep` in an async handler freezes the whole event loop.

    Under `--workers 1` a single SSE client would make every other endpoint and browser
    tab unresponsive -- the failure that kills a live demo.
    """
    offenders = []
    for path in _py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "sleep"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "time"
            ):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"blocking time.sleep() found in serve/: {offenders}"


def test_scorer_imports_are_minimal() -> None:
    """The scorer is the piece most likely to drift back toward sklearn."""
    used = _top_level_imports(SERVE / "scorer.py")
    assert used <= {"numpy", "pathlib", "__future__"}, f"scorer.py imports {sorted(used)}"
