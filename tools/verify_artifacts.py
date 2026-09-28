"""Artifact provenance manifest and staleness detection.

    python tools/verify_artifacts.py            # verify; exit 1 if anything is stale
    python tools/verify_artifacts.py --write    # (re)generate artifacts/MANIFEST.json
    python tools/verify_artifacts.py --json     # machine-readable report

WHAT PROBLEM THIS SOLVES. Every headline number in this repository is read out of a
committed artifact. If the label generator, the feature code, the model trainer, a plant
config or a detector implementation changes and the artifact is NOT rebuilt, the numbers
in README.md and docs/MODEL_CARD.md silently start describing code that no longer exists.
That failure is invisible: the tests still pass, the API still serves, and the artifact
still parses. It has to be detected structurally.

WHY CONTENT HASHES AND NOT TIMESTAMPS. A `git checkout`, a clone, or a `touch` rewrites
mtimes without changing a byte, and an editor can rewrite a file to identical content.
Timestamps produce both false alarms and false silence. Every check here is SHA-256 over
file contents, so it answers the only question that matters: is the artifact on disk the
one this source tree produces?

WHAT IT CANNOT DO. It cannot prove the artifact is CORRECT, only that it is consistent
with the sources registered against it. An artifact built from this exact tree but with a
hand-edited row still passes. Correctness is what the test suite and the benchmarks are
for.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "artifacts" / "MANIFEST.json"
SCHEMA_VERSION = 1

# Source groups. Each is a list of repo-relative glob patterns; an artifact declares which
# groups it depends on. Keeping them named means a new artifact reuses a group rather than
# restating a file list that then drifts.
SOURCE_GROUPS: dict[str, list[str]] = {
    "config": ["configs/plant_a.yaml", "offline/config_schema.py"],
    "economics": ["configs/economics.yaml"],
    "simulator": ["offline/simulator/*.py"],
    "features": ["offline/ml/featurize.py"],
    "labels": ["offline/simulator/causal_engine.py", "offline/simulator/inspection.py"],
    "model": [
        "offline/ml/train.py",
        "offline/ml/defect_model.py",
        "offline/ml/thresholds.py",
        "offline/ml/validate.py",
    ],
    "soft_sensor": ["offline/ml/soft_sensor.py"],
    "bottleneck_detector": ["offline/ml/bottleneck.py"],
    "benchmark_bottleneck": ["offline/benchmarks/*.py"],
    "benchmark_sensor": ["offline/benchmarks/sensor_degradation.py"],
    "artifact_builder": ["offline/build_artifact.py", "offline/ml/scenarios.py"],
    "knowledge": ["offline/build_knowledge_index.py", "offline/knowledge/*.py"],
    "fallback": ["mlops/fallback.py"],
}


@dataclass(frozen=True)
class ArtifactSpec:
    path: str
    producer: str
    command: str
    groups: tuple[str, ...]
    seed_source: str
    required: bool = True
    note: str = ""


ARTIFACTS: tuple[ArtifactSpec, ...] = (
    ArtifactSpec(
        path="artifacts/pulsetwin.db",
        producer="offline/build_artifact.py",
        command="python -m offline.build_artifact --config configs/<plant>.yaml",
        groups=(
            "config", "economics", "simulator", "features", "labels", "model",
            "soft_sensor", "bottleneck_detector", "artifact_builder",
        ),
        seed_source="configs/<plant>.yaml :: simulation.seed",
        note="16 tables. Every metric quoted in README.md and MODEL_CARD.md reads from here.",
    ),
    ArtifactSpec(
        path="artifacts/model_plant_a.npz",
        producer="offline/ml/train.py",
        command="python -m offline.ml.train --config configs/plant_a.yaml",
        groups=("config", "simulator", "features", "labels", "model", "soft_sensor"),
        seed_source="configs/plant_a.yaml :: simulation.seed",
    ),
    ArtifactSpec(
        path="artifacts/stream_plant_a.jsonl",
        producer="offline/build_artifact.py",
        command="python -m offline.build_artifact --config configs/plant_a.yaml",
        groups=("config", "simulator", "features", "labels", "model", "artifact_builder"),
        seed_source="configs/plant_a.yaml :: simulation.seed",
    ),
    ArtifactSpec(
        path="artifacts/fallback_plant_a.npz",
        producer="mlops/fallback.py",
        command="python -m mlops.fallback --config configs/plant_a.yaml",
        groups=("config", "simulator", "features", "fallback"),
        seed_source="configs/plant_a.yaml :: simulation.seed",
    ),
    ArtifactSpec(
        path="artifacts/knowledge_index.npz",
        producer="offline/build_knowledge_index.py",
        command="python -m offline.build_knowledge_index",
        groups=("knowledge",),
        seed_source="n/a -- lexical TF-IDF, no sampling (ADR-046)",
    ),
    ArtifactSpec(
        path="artifacts/knowledge_corpus.json",
        producer="offline/build_knowledge_index.py",
        command="python -m offline.build_knowledge_index",
        groups=("knowledge",),
        seed_source="n/a -- lexical TF-IDF, no sampling (ADR-046)",
    ),
    ArtifactSpec(
        path="artifacts/benchmarks/bottleneck_summary.json",
        producer="offline/benchmarks/run_bottleneck_benchmark.py",
        command="python -m offline.benchmarks.run_bottleneck_benchmark --plant <plant>",
        groups=("config", "simulator", "benchmark_bottleneck"),
        seed_source="offline/benchmarks/bottleneck_ground_truth.py :: seed_list(n, start=1000)",
        required=False,
        note="Throughput-sensitivity ground truth + detector league table.",
    ),
    ArtifactSpec(
        path="artifacts/benchmarks/bottleneck_ground_truth.csv",
        producer="offline/benchmarks/run_bottleneck_benchmark.py",
        command="python -m offline.benchmarks.run_bottleneck_benchmark --plant <plant>",
        groups=("config", "simulator", "benchmark_bottleneck"),
        seed_source="offline/benchmarks/bottleneck_ground_truth.py :: seed_list(n, start=1000)",
        required=False,
    ),
    ArtifactSpec(
        path="artifacts/benchmarks/bottleneck_detector_results.csv",
        producer="offline/benchmarks/run_bottleneck_benchmark.py",
        command="python -m offline.benchmarks.run_bottleneck_benchmark --plant <plant>",
        groups=("config", "simulator", "benchmark_bottleneck"),
        seed_source="offline/benchmarks/bottleneck_ground_truth.py :: seed_list(n, start=1000)",
        required=False,
    ),
    ArtifactSpec(
        path="artifacts/benchmarks/sensor_degradation.csv",
        producer="offline/benchmarks/sensor_degradation.py",
        command="python -m offline.benchmarks.sensor_degradation --plant <plant>",
        groups=("config", "simulator", "soft_sensor", "benchmark_sensor"),
        seed_source="offline/benchmarks/sensor_degradation.py :: mask seeds",
        required=False,
    ),
    ArtifactSpec(
        path="artifacts/benchmarks/sensor_degradation_summary.json",
        producer="offline/benchmarks/sensor_degradation.py",
        command="python -m offline.benchmarks.sensor_degradation --plant <plant>",
        groups=("config", "simulator", "soft_sensor", "benchmark_sensor"),
        seed_source="offline/benchmarks/sensor_degradation.py :: mask seeds",
        required=False,
    ),
    ArtifactSpec(
        path="artifacts/metrics_summary.json",
        producer="tools/metrics_summary.py",
        command="python tools/metrics_summary.py --write",
        groups=(),
        seed_source="n/a -- derived from other artifacts",
        required=False,
        note="Derived. Its own freshness is checked by tools/metrics_summary.py --check.",
    ),
)


# ------------------------------------------------------------------------------ hashing


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """Raw-byte SHA-256. Used for the ARTIFACTS themselves, which are generated binaries
    (SQLite, npz) or generated text nobody hand-edits."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def sha256_source(path: Path) -> str:
    """SHA-256 of a SOURCE file with line endings normalised to LF.

    This repository is developed on Windows with `core.autocrlf=true` and carries no
    `.gitattributes`, so `git checkout` rewrites every text file's line endings on the way
    out of the index. A raw-byte hash therefore reports every Python file as "changed"
    after a fresh clone, and a Windows developer and Ubuntu CI never agree on a single
    hash -- which would make the whole staleness check cry wolf until people ignored it.

    Normalising CRLF to LF before hashing answers the question we actually mean: did the
    CONTENT of this source change? A line-ending flip is not a semantic change and must
    not invalidate an artifact. (Found by appending a line to featurize.py, reverting it
    with `git checkout`, and watching the hash fail to come back.)
    """
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def expand(patterns: list[str]) -> list[Path]:
    """Resolve glob patterns to a sorted, deduplicated file list.

    Sorted because the manifest is committed and a nondeterministic ordering would make
    every regeneration a diff.
    """
    found: set[Path] = set()
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            found.update(p for p in ROOT.glob(pattern) if p.is_file())
        else:
            candidate = ROOT / pattern
            if candidate.is_file():
                found.add(candidate)
    return sorted(found)


def source_hashes(groups: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for group in groups:
        patterns = SOURCE_GROUPS.get(group)
        if patterns is None:
            raise KeyError(f"unknown source group '{group}'")
        for path in expand(patterns):
            out[path.relative_to(ROOT).as_posix()] = sha256_source(path)
    return dict(sorted(out.items()))


def git_sha() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10
        )
        return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None
    except Exception:
        return None


def environment() -> dict:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "numpy": _version("numpy"),
        "pandas": _version("pandas"),
        "scikit_learn": _version("sklearn"),
    }


def _version(module: str) -> str | None:
    try:
        return __import__(module).__version__
    except Exception:
        return None


# ----------------------------------------------------------------------------- manifest


def build_manifest() -> dict:
    entries = []
    for spec in ARTIFACTS:
        path = ROOT / spec.path
        present = path.is_file()
        entries.append(
            {
                "name": spec.path,
                "present": present,
                "required": spec.required,
                "schema_version": SCHEMA_VERSION,
                "producer": spec.producer,
                "command": spec.command,
                "seed_source": spec.seed_source,
                "note": spec.note,
                "artifact_sha256": sha256_file(path) if present else None,
                "size_bytes": path.stat().st_size if present else None,
                "generated_at": (
                    datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
                    if present
                    else None
                ),
                "source_groups": list(spec.groups),
                "source_sha256": source_hashes(spec.groups),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "environment": environment(),
        "note": (
            "Regenerate with `python tools/verify_artifacts.py --write` AFTER rebuilding "
            "artifacts -- never to silence a staleness failure."
        ),
        "artifacts": entries,
    }


def verify(manifest: dict) -> list[dict]:
    """Compare the manifest against the working tree. Returns a list of findings."""
    findings: list[dict] = []
    recorded = {entry["name"]: entry for entry in manifest.get("artifacts", [])}

    for spec in ARTIFACTS:
        entry = recorded.get(spec.path)
        path = ROOT / spec.path
        present = path.is_file()

        if entry is None:
            findings.append(
                {
                    "artifact": spec.path,
                    "kind": "unregistered",
                    "detail": "present in the registry but absent from MANIFEST.json; "
                              "run --write",
                }
            )
            continue

        if not present:
            if spec.required:
                findings.append(
                    {
                        "artifact": spec.path,
                        "kind": "missing",
                        "detail": f"required artifact not on disk; rebuild with: {spec.command}",
                    }
                )
            continue

        if entry.get("artifact_sha256") and entry["artifact_sha256"] != sha256_file(path):
            findings.append(
                {
                    "artifact": spec.path,
                    "kind": "artifact_modified",
                    "detail": "the artifact's own bytes differ from the manifest; it was "
                              "rebuilt or edited without `--write`",
                }
            )

        current = source_hashes(spec.groups)
        previous = entry.get("source_sha256", {})
        changed = sorted(
            name for name in set(current) | set(previous)
            if current.get(name) != previous.get(name)
        )
        if changed:
            findings.append(
                {
                    "artifact": spec.path,
                    "kind": "stale",
                    "detail": (
                        f"{len(changed)} source file(s) changed since this artifact was "
                        f"built; rebuild with: {spec.command}"
                    ),
                    "changed_sources": changed,
                }
            )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="(re)generate artifacts/MANIFEST.json")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="report findings but exit 0 -- for informational CI steps only",
    )
    args = parser.parse_args(argv)

    if args.write:
        manifest = build_manifest()
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        present = sum(1 for a in manifest["artifacts"] if a["present"])
        print(f"wrote {MANIFEST_PATH.relative_to(ROOT)}: "
              f"{present}/{len(manifest['artifacts'])} artifacts present")
        return 0

    if not MANIFEST_PATH.exists():
        print("artifacts/MANIFEST.json does not exist; run: "
              "python tools/verify_artifacts.py --write", file=sys.stderr)
        return 1

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    findings = verify(manifest)

    if args.json:
        print(json.dumps({"ok": not findings, "findings": findings}, indent=2))
    elif not findings:
        print(f"artifact manifest OK ({len(ARTIFACTS)} artifacts checked, "
              f"manifest written {manifest.get('written_at')})")
    else:
        print(f"{len(findings)} artifact problem(s):\n", file=sys.stderr)
        for finding in findings:
            print(f"  [{finding['kind']}] {finding['artifact']}", file=sys.stderr)
            print(f"      {finding['detail']}", file=sys.stderr)
            for name in finding.get("changed_sources", [])[:12]:
                print(f"        changed: {name}", file=sys.stderr)
            extra = len(finding.get("changed_sources", [])) - 12
            if extra > 0:
                print(f"        ... and {extra} more", file=sys.stderr)
            print(file=sys.stderr)

    return 0 if (not findings or args.allow_stale) else 1


if __name__ == "__main__":
    sys.exit(main())
