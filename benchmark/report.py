"""JSON + Markdown benchmark reports, and the reproducibility manifest every run carries.

One report per run. Written to `benchmark/reports/<dataset>_<task>_<run_id>.{json,md}`.
No database table -- the frontend dashboard this would otherwise feed is explicitly out
of scope for this pass (see docs/benchmarking.md); a file is the honest-sized answer to
"expose results" when nothing renders them yet.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from benchmark.registry import REPORTS_DIR, DatasetConfig

SEED = 42


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


@dataclass
class BenchmarkReport:
    run_id: str
    dataset_id: str
    dataset_name: str
    task: str
    timestamp: str
    seed: int
    split_strategy: str
    n_rows: int
    n_train: int
    n_validate: int
    n_test: int
    n_features: int
    target_column: str
    target_prevalence: float
    model_name: str
    hyperparameters: dict[str, Any]
    metrics: dict[str, Any]
    threshold_metrics: dict[str, Any]
    feature_importance: list[dict[str, Any]] = field(default_factory=list)
    sensor_robustness: list[dict[str, Any]] | None = None
    limitations: list[str] = field(default_factory=list)
    git_commit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        m, t = self.metrics, self.threshold_metrics
        lines = [
            f"# Benchmark report: {self.dataset_name} / {self.task}",
            "",
            f"- run_id: `{self.run_id}`",
            f"- generated: {self.timestamp}",
            f"- seed: {self.seed}  ·  git commit: `{self.git_commit or 'unknown'}`",
            f"- split strategy: **{self.split_strategy}**",
            "",
            "## Dataset",
            "",
            "| rows | train | validate | test | features | target | prevalence |",
            "|---|---|---|---|---|---|---|",
            f"| {self.n_rows} | {self.n_train} | {self.n_validate} | {self.n_test} "
            f"| {self.n_features} | `{self.target_column}` "
            f"| {self.target_prevalence:.4f} |",
            "",
            "## Limitations of this dataset (read before quoting a number from below)",
            "",
            *[f"- {line}" for line in self.limitations],
            "",
            "## Model",
            "",
            f"- {self.model_name}",
            f"- hyperparameters: `{json.dumps(self.hyperparameters)}`",
            "",
            "## Metrics (never accuracy alone -- this target is imbalanced)",
            "",
            "| PR-AUC | ROC-AUC | precision | recall | F1 | false alarm rate |",
            "|---|---|---|---|---|---|",
            f"| {m.get('pr_auc', float('nan')):.4f} "
            f"[{m.get('pr_auc_ci_low', float('nan')):.4f}, "
            f"{m.get('pr_auc_ci_high', float('nan')):.4f}] "
            f"| {m.get('roc_auc', float('nan')):.4f} "
            f"| {t.get('precision', float('nan')):.4f} "
            f"| {t.get('recall', float('nan')):.4f} "
            f"| {t.get('f1', float('nan')):.4f} "
            f"| {t.get('false_alarm_rate', float('nan')):.4f} |",
            "",
            f"Confusion matrix at threshold {t.get('threshold', float('nan')):.4f} "
            f"(chosen on validation, never on test):",
            "",
            "```",
            f"{json.dumps(t.get('confusion_matrix', {}), indent=2)}",
            "```",
            "",
        ]
        if self.sensor_robustness:
            lines += ["## Sensor-coverage robustness", "",
                     "| coverage | strategy | PR-AUC |", "|---|---|---|"]
            for row in self.sensor_robustness:
                lines.append(
                    f"| {row['coverage_pct']}% | {row['strategy']} "
                    f"| {row['pr_auc']:.4f} |")
            lines.append("")
        if self.feature_importance:
            lines += ["## Top features by permutation PR-AUC drop", "",
                     "| feature | delta PR-AUC |", "|---|---|"]
            for row in self.feature_importance[:10]:
                lines.append(f"| `{row['feature']}` | {row['delta_pr_auc']:.4f} |")
            lines.append("")
        return "\n".join(lines)


def build_report(
    *, run_id: str, config: DatasetConfig, task: str, split_strategy: str,
    n_train: int, n_validate: int, n_test: int, n_features: int,
    target_prevalence: float, model_name: str, hyperparameters: dict,
    metrics: dict, threshold_metrics: dict, feature_importance: list[dict],
    sensor_robustness: list[dict] | None = None,
) -> BenchmarkReport:
    return BenchmarkReport(
        run_id=run_id, dataset_id=config.dataset_id, dataset_name=config.name, task=task,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), seed=SEED,
        split_strategy=split_strategy, n_rows=n_train + n_validate + n_test,
        n_train=n_train, n_validate=n_validate, n_test=n_test, n_features=n_features,
        target_column=config.target_column, target_prevalence=target_prevalence,
        model_name=model_name, hyperparameters=hyperparameters, metrics=metrics,
        threshold_metrics=threshold_metrics, feature_importance=feature_importance,
        sensor_robustness=sensor_robustness, limitations=config.limitations,
        git_commit=_git_commit(),
    )


def write_report(report: BenchmarkReport) -> tuple[Path, Path]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{report.dataset_id}_{report.task}_{report.run_id}"
    json_path = REPORTS_DIR / f"{stem}.json"
    md_path = REPORTS_DIR / f"{stem}.md"
    json_path.write_text(json.dumps(report.to_dict(), indent=2, default=str),
                         encoding="utf-8")
    md_path.write_text(report.to_markdown(), encoding="utf-8")
    return json_path, md_path
