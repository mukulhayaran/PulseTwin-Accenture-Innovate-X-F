"""The p75 experiment knobs (hazard_cap, intensity_sharpening, strong_batch_factor) must
default to the shipped physics: regenerating plant_a has to reproduce the labels exactly.

The reference hash was captured from the unmodified simulator before the knobs existed.
"""

import hashlib
import os

import pandas as pd

os.environ.setdefault("OMP_NUM_THREADS", "1")

from offline.config_schema import load_plant_config  # noqa: E402
from offline.simulator import generate  # noqa: E402

# Recomputed 2026-09-26 after every tie-sensitive sort in the pipeline was made STABLE.
# The old value came from numpy's default (unstable, CPU/version-dependent) argsort: the
# committed simulator itself no longer reproduced it on this machine, so the test was
# failing for the unmodified code too. The knob defaults reproduce the pre-knob simulator
# bit-for-bit under the stable order.
PLANT_A_LABEL_HASH = "05e7077cc8054699"


def test_default_knobs_reproduce_shipped_labels(tmp_path):
    cfg = load_plant_config("plant_a.yaml")
    assert cfg.defect_physics.hazard_cap == 0.85
    assert cfg.defect_physics.intensity_sharpening == 3.0
    assert cfg.defect_physics.mode_a.strong_batch_factor == 0.06

    generate.run(cfg, out_root=tmp_path)
    t = pd.read_parquet(tmp_path / "plant_a" / "inspections.parquet")
    cols = t[["unit_seq", "propensity", "true_defect", "defect_detected"]].to_numpy()
    assert hashlib.sha256(cols.tobytes()).hexdigest()[:16] == PLANT_A_LABEL_HASH
    assert (tmp_path / "plant_a" / "risk_conditions.parquet").exists()
