"""pyproject.toml's groups must not drift from the per-deployable requirements files.

Both exist on purpose (see pyproject.toml's header): the requirements files are what
Docker installs, and serve/requirements.txt is what enforces the import wall at the image
level -- pandas is not merely unimported in the runtime image, it is not installed. The
lock file is what CI installs. If the two descriptions of the same environment drift, one
of them is lying, and the one that lies is whichever you did not run last.

These tests compare PACKAGE NAMES, not version specifiers. Requiring identical specifiers
would fail on `numpy>=2.1` vs `numpy>=2.1` written with different whitespace and teach
people to skip the check; a package appearing in one place and not the other is the
failure that actually matters.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# requirements file -> the pyproject group (or None for project.dependencies) it mirrors
MIRRORS = {
    "serve/requirements.txt": None,
    "requirements-dev.txt": "ml",
    "serve/requirements-jobs.txt": "ml",
    "genai/requirements.txt": "genai",
    "ingestion/requirements.txt": "ingestion",
}

# Packages allowed to appear in a requirements file without appearing in its mirrored
# group, each with the reason. Anything not listed here is a drift failure.
EXEMPT: dict[str, set[str]] = {
    # requirements-dev.txt carries the test runner and the shared numeric core; those
    # live in the `dev` group and in project.dependencies respectively, not in `ml`.
    "requirements-dev.txt": {"pytest", "numpy", "pydantic"},
    # The jobs image reuses the ML group plus the GenAI client, which has its own group.
    "serve/requirements-jobs.txt": {"openai"},
    # The ingestion deployable pins its own copies of the shared runtime drivers.
    "ingestion/requirements.txt": {"psycopg", "numpy", "orjson"},
}

_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _normalise(name: str) -> str:
    return name.lower().replace("_", "-")


def parse_requirements(path: Path) -> set[str]:
    names = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = _REQ_LINE.match(line)
        if match:
            names.add(_normalise(match.group(1)))
    return names


def parse_group(pyproject: dict, group: str | None) -> set[str]:
    entries = (
        pyproject["project"]["dependencies"]
        if group is None
        else pyproject["dependency-groups"][group]
    )
    names = set()
    for entry in entries:
        match = _REQ_LINE.match(entry)
        if match:
            names.add(_normalise(match.group(1)))
    return names


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("req_file,group", sorted(MIRRORS.items(), key=lambda kv: kv[0]))
def test_requirements_file_is_mirrored_in_pyproject(pyproject, req_file, group):
    path = ROOT / req_file
    assert path.exists(), f"{req_file} is referenced by a Dockerfile and must exist"

    declared = parse_requirements(path)
    mirrored = parse_group(pyproject, group)
    exempt = {_normalise(n) for n in EXEMPT.get(req_file, set())}

    missing = declared - mirrored - exempt
    target = "project.dependencies" if group is None else f"dependency-groups.{group}"
    assert not missing, (
        f"{req_file} requires {sorted(missing)} but pyproject.toml's {target} does not. "
        f"Add them there and re-run `uv lock`, or add them to EXEMPT with a reason."
    )


def test_lockfile_exists_and_covers_the_runtime(pyproject):
    lock = ROOT / "uv.lock"
    assert lock.exists(), "uv.lock is missing; run `uv lock`"
    text = lock.read_text(encoding="utf-8")
    for name in parse_group(pyproject, None):
        assert f'name = "{name}"' in text, (
            f"runtime dependency '{name}' is not in uv.lock; run `uv lock`"
        )


def test_import_wall_packages_are_absent_from_the_runtime_group(pyproject):
    """The wall, restated as a dependency assertion.

    CLAUDE.md section 2 bans these from serve/. Banning the IMPORT is enforced by
    tests/test_serve_imports.py; banning the INSTALL is what keeps the runtime image
    small and makes an accidental import fail loudly in Docker rather than work locally
    and crash in production.
    """
    banned = {
        "pandas", "scikit-learn", "sklearn", "scipy", "xgboost", "shap", "simpy",
        "psutil", "openai", "anthropic", "groq", "matplotlib", "torch",
    }
    runtime = parse_group(pyproject, None)
    overlap = runtime & banned
    assert not overlap, (
        f"{sorted(overlap)} is in project.dependencies, which mirrors serve/"
        f"requirements.txt. The import wall (CLAUDE.md section 2) bans it from the "
        f"runtime image."
    )
    serve_req = parse_requirements(ROOT / "serve" / "requirements.txt")
    overlap = serve_req & banned
    assert not overlap, f"{sorted(overlap)} is in serve/requirements.txt; see ADR-033"


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib needs 3.11")
def test_requires_python_covers_the_docker_base_image(pyproject):
    """serve/Dockerfile builds on python:3.11-slim. A requires-python above that would
    make the lock uninstallable in the image nobody notices until a deploy."""
    assert pyproject["project"]["requires-python"] == ">=3.11"
    dockerfile = (ROOT / "serve" / "Dockerfile").read_text(encoding="utf-8")
    assert "python:3.11" in dockerfile, (
        "serve/Dockerfile's base image changed; update requires-python to match"
    )
