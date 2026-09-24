"""Single machine-readable source for every headline metric, plus a README consistency gate.

    python tools/metrics_summary.py --write    # rebuild artifacts/metrics_summary.json
    python tools/metrics_summary.py --check    # fail if a documented number disagrees
    python tools/metrics_summary.py --list     # print every available key and value

WHY. README.md and docs/MODEL_CARD.md quote roughly thirty measured numbers. Those numbers
were hand-copied out of the artifact, and docs/STATE.md bug 25 records what that costs:
five documents disagreed about the headline metrics because the artifact was rebuilt and
only two of them were updated. A presenter read a stale number aloud to judges.

HOW THE GATE WORKS. A documented number carries an invisible marker naming the metric key
and a format spec:

    | PR-AUC | **0.256** <!--metric:plant_a.pr_auc:.3f--> |

`--check` finds every marker, looks the key up in artifacts/metrics_summary.json, formats
the stored value, and compares it against the last number written before the marker. A
mismatch is an error with both values printed. That makes the failure the brief asks for
-- README says 0.256, artifact says 0.241 -- a red CI check rather than a discovery during
a demo.

An unmarked number is NOT checked. Markers are added to the claims that matter
(docs/CLAIMS.md is the register of which those are); this tool never guesses which digits
in a sentence are a metric.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Run either as `python tools/metrics_summary.py` (script, no package context) or as
# `python -m tools.metrics_summary`. The first puts tools/ on sys.path but not the repo
# root, so importing its sibling would fail without this.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ARTIFACTS = ROOT / "artifacts"
DB_PATH = ARTIFACTS / "pulsetwin.db"
SUMMARY_PATH = ARTIFACTS / "metrics_summary.json"
BENCH_DIR = ARTIFACTS / "benchmarks"
EXTERNAL_REPORTS = ROOT / "benchmark" / "reports"

SCHEMA_VERSION = 1

# Files scanned by --check. Add a file here and its markers become CI-enforced.
DOCUMENTED_FILES = (
    "README.md",
    "docs/MODEL_CARD.md",
    "docs/BOTTLENECK_BENCHMARK.md",
    "docs/SENSOR_ROBUSTNESS.md",
    "docs/CLAIMS.md",
)

MARKER = re.compile(r"<!--\s*metric:([A-Za-z0-9_.\[\]-]+):([^->]+?)\s*-->")
# The last number appearing before the marker: optional sign, digits, optional decimal,
# optional trailing percent. Markdown emphasis and pipes are skipped by the search itself.
TRAILING_NUMBER = re.compile(r"(-?\d+(?:\.\d+)?)\s*%?\s*(?:\*\*|__|\*|`|\]|\))*\s*$")

NOT_RUN = "NOT RUN"


# ------------------------------------------------------------------------------- build


def _db_metrics() -> dict:
    """Per-plant scalar metrics from the committed artifact's `metrics` table."""
    if not DB_PATH.exists():
        return {}
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT plant_id, key, value FROM metrics").fetchall()
    finally:
        con.close()
    out: dict[str, dict] = {}
    for plant_id, key, value in rows:
        out.setdefault(plant_id, {})[key] = value
    return out


def _bottleneck_benchmark() -> dict:
    path = BENCH_DIR / "bottleneck_summary.json"
    if not path.exists():
        return {"status": NOT_RUN}
    data = json.loads(path.read_text(encoding="utf-8"))
    league = {entry["detector"]: entry for entry in data.get("league_table", [])}
    scenarios = {}
    for plant_id, run in data.get("runs", {}).items():
        for key, scenario in run.get("scenarios", {}).items():
            scenarios[f"{plant_id}.{key}"] = scenario
    return {
        "status": "OK",
        "league": league,
        "scenarios": scenarios,
        "runs": {
            plant_id: {
                k: run.get(k)
                for k in ("profile", "n_seeds", "horizon_days", "relative_perturbation",
                          "git_sha", "generated_at", "elapsed_s")
            }
            for plant_id, run in data.get("runs", {}).items()
        },
    }


def _sensor_degradation() -> dict:
    path = BENCH_DIR / "sensor_degradation_summary.json"
    if not path.exists():
        return {"status": NOT_RUN}
    return json.loads(path.read_text(encoding="utf-8"))


def _external_benchmark() -> dict:
    """Most recent AI4I report per task. Kept deliberately separate from PulseTwin's own
    numbers: it validates the pipeline on data PulseTwin did not generate, and it says
    nothing about automotive defect prediction (docs/CLAIMS.md)."""
    if not EXTERNAL_REPORTS.exists():
        return {"status": NOT_RUN}
    latest: dict[str, dict] = {}
    for path in sorted(EXTERNAL_REPORTS.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        task = data.get("task")
        if not task:
            continue
        stamp = data.get("timestamp", "")
        if task not in latest or stamp > latest[task].get("timestamp", ""):
            latest[task] = data
    if not latest:
        return {"status": NOT_RUN}
    return {
        "status": "OK",
        "tasks": {
            task: {
                "dataset_id": data.get("dataset_id"),
                "dataset_name": data.get("dataset_name"),
                "run_id": data.get("run_id"),
                "timestamp": data.get("timestamp"),
                "model_name": data.get("model_name"),
                "split_strategy": data.get("split_strategy"),
                "n_rows": data.get("n_rows"),
                "target_prevalence": data.get("target_prevalence"),
                "metrics": data.get("metrics", {}),
            }
            for task, data in latest.items()
        },
    }


def build() -> dict:
    from tools.verify_artifacts import git_sha  # local import: tools is not a package dep

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "generated_by": "tools/metrics_summary.py",
        "note": (
            "THE source for every documented metric. Do not hand-edit. Regenerate with "
            "`python tools/metrics_summary.py --write` after rebuilding artifacts."
        ),
        "plants": _db_metrics(),
        "bottleneck_benchmark": _bottleneck_benchmark(),
        "sensor_degradation": _sensor_degradation(),
        "external_benchmark": _external_benchmark(),
    }


# ------------------------------------------------------------------------------ lookup


def flatten(node, prefix: str = "") -> dict[str, object]:
    """Dotted-key view of the summary, so a marker can name any leaf."""
    out: dict[str, object] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(flatten(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            out.update(flatten(value, f"{prefix}[{i}]"))
    else:
        out[prefix] = node
    return out


def load_summary() -> dict:
    if not SUMMARY_PATH.exists():
        raise FileNotFoundError(
            "artifacts/metrics_summary.json does not exist; run: "
            "python tools/metrics_summary.py --write"
        )
    return json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))


def resolve(flat: dict[str, object], key: str) -> object:
    """Look a marker key up, accepting both the fully-qualified and the `plants.`-implied
    form so README markers read as `plant_a.pr_auc` rather than `plants.plant_a.pr_auc`."""
    for candidate in (key, f"plants.{key}", f"bottleneck_benchmark.{key}",
                      f"external_benchmark.{key}"):
        if candidate in flat:
            return flat[candidate]
    raise KeyError(key)


# ------------------------------------------------------------------------------- check


def check(paths: list[str] | None = None) -> list[str]:
    """Verify every marked number in the documentation. Returns a list of error strings."""
    summary = load_summary()
    flat = flatten(summary)
    errors: list[str] = []
    checked = 0

    for rel in paths or DOCUMENTED_FILES:
        path = ROOT / rel
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in MARKER.finditer(line):
                key, fmt = match.group(1), match.group(2).strip()
                where = f"{rel}:{lineno}"

                try:
                    value = resolve(flat, key)
                except KeyError:
                    errors.append(f"{where}: unknown metric key '{key}'")
                    continue

                if value is None:
                    errors.append(f"{where}: metric '{key}' is null in metrics_summary.json")
                    continue

                try:
                    expected = format(value, fmt) if fmt else str(value)
                except (ValueError, TypeError) as exc:
                    errors.append(f"{where}: cannot format '{key}' ({value!r}) as '{fmt}': {exc}")
                    continue

                before = line[: match.start()]
                found = TRAILING_NUMBER.search(before.rstrip())
                if not found:
                    errors.append(
                        f"{where}: marker for '{key}' has no number before it "
                        f"(expected {expected})"
                    )
                    continue

                checked += 1
                written = found.group(1)
                expected_number = expected.rstrip("%")
                if written != expected_number:
                    errors.append(
                        f"{where}: '{key}' documented as {written} but "
                        f"artifacts/metrics_summary.json says {expected_number} "
                        f"(format '{fmt}') -- rebuild the artifact or fix the document"
                    )

    if not errors:
        print(f"metric consistency OK ({checked} documented numbers checked against "
              f"artifacts/metrics_summary.json)")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="rebuild metrics_summary.json")
    parser.add_argument("--check", action="store_true", help="verify documented numbers")
    parser.add_argument("--list", action="store_true", help="print every key and value")
    parser.add_argument("--grep", default=None, help="with --list, filter keys by substring")
    args = parser.parse_args(argv)

    if args.write:
        summary = build()
        SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
        SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        plants = ", ".join(sorted(summary["plants"])) or "none"
        print(
            f"wrote {SUMMARY_PATH.relative_to(ROOT)}\n"
            f"  plants: {plants}\n"
            f"  bottleneck benchmark: {summary['bottleneck_benchmark'].get('status')}\n"
            f"  sensor degradation:   {summary['sensor_degradation'].get('status')}\n"
            f"  external benchmark:   {summary['external_benchmark'].get('status')}"
        )
        return 0

    if args.list:
        flat = flatten(load_summary())
        for key in sorted(flat):
            if args.grep and args.grep not in key:
                continue
            print(f"{key} = {flat[key]}")
        return 0

    if args.check or True:
        errors = check()
        for error in errors:
            print(f"ERROR {error}", file=sys.stderr)
        return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
