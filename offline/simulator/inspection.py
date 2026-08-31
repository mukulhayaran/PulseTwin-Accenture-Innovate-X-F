"""End-of-line inspection: the noisy label the model actually trains on.

ADR-001 and a semantic decision that follows from it.

The original spec paired `inspection_false_reject_rate: 0.02` with a claim of "~3% label
noise". At a 3% base rate that made 41% of positive labels wrong (see docs/AUDIT.md B1).
Dropping the false-reject rate to 0.002 fixes the arithmetic, but only if the separate
"3% label noise" is not then applied symmetrically on top -- a symmetric 3% flip across
all units would push ~291 good units per 10,000 into the positive class and reintroduce
exactly the contamination we just removed.

So `label_noise_rate` is defined here as ASYMMETRIC: it is the probability that a genuine
detection is lost before it reaches the record -- misrouted rework, a missed transcription,
a unit released before the paperwork caught up. That is realistic, it is the direction
plant data actually errs, and it degrades recall without poisoning the negative class.

Resulting label quality per 10,000 units at a 3% base rate:
    true defects            300
    detected (92%)          276
    lost to noise (3%)       -8   -> 268 recorded true positives
    false rejects (0.2%)     19   (of 9,700 good units)
    ---------------------------
    positive labels         287, of which 6.8% are wrong

That 6.8% is a defensible label-noise figure and leaves the PR-AUC ceiling of 0.75-0.88
reachable. `true_defect` is retained separately for offline validation and is NEVER
exposed to featurisation, training, or any API response.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from offline.config_schema import PlantConfig


@dataclass
class InspectionResult:
    defect_detected: np.ndarray   # the noisy training label
    true_defect: np.ndarray       # ground truth, offline validation only
    escaped: np.ndarray           # true defect inspection missed -> warranty exposure
    false_reject: np.ndarray      # good unit wrongly rejected

    def summary(self) -> dict[str, float]:
        pos = self.defect_detected.sum()
        return {
            "true_defect_rate": float(self.true_defect.mean()),
            "detected_rate": float(self.defect_detected.mean()),
            "positive_labels": int(pos),
            "false_positive_share": float(self.false_reject.sum() / pos) if pos else 0.0,
            "escape_rate": float(self.escaped.sum() / max(1, self.true_defect.sum())),
        }


def inspect(
    cfg: PlantConfig, true_defect: np.ndarray, rng: np.random.Generator
) -> InspectionResult:
    dp = cfg.defect_physics
    n = len(true_defect)
    is_defect = true_defect.astype(bool)

    detected = is_defect & (rng.random(n) < dp.inspection_detection_rate)

    # Asymmetric label noise: a real detection fails to reach the record.
    lost = detected & (rng.random(n) < dp.label_noise_rate)
    recorded_true = detected & ~lost

    false_reject = (~is_defect) & (rng.random(n) < dp.inspection_false_reject_rate)

    observed = (recorded_true | false_reject).astype(np.int8)
    escaped = (is_defect & ~recorded_true).astype(np.int8)

    return InspectionResult(
        defect_detected=observed,
        true_defect=true_defect.astype(np.int8),
        escaped=escaped,
        false_reject=false_reject.astype(np.int8),
    )
