"""A disk cache of the WARMED physical twin (ADR-064 CPU work).

`LiveTwinEngine.start()` simulates ~4.8 h of line plus calibration before the twin is
usable: ~13 s of CPU, and identical for every experiment and forecast on the same plant,
threshold and code. So it is computed once, pickled (~8 MB, 0.04 s) and loaded (0.02 s).

The key hashes everything that shapes the warm state -- simulator and twin sources, the
feature contract, the plant config and the champion model artifacts -- so any change to
them is a new key, never a stale hit. A corrupt or unreadable entry is rebuilt, not trusted.
`tests/test_warm_cache.py` pins that a cached twin's future is bit-identical to a fresh one.
"""

from __future__ import annotations

import hashlib
import os
import pickle
import tempfile
from pathlib import Path

from offline.benchmarks.simrun import with_horizon
from offline.config_schema import load_plant_config
from offline.live_twin.engine import LiveTwinEngine

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_SEED = 42
HORIZON_DAYS = 3.0
_SOURCES = ("offline/simulator/line_model.py", "offline/simulator/physics.py",
            "offline/simulator/causal_engine.py", "offline/live_twin/engine.py",
            "offline/live_twin/incidents.py", "offline/live_twin/signals.py",
            "offline/live_twin/causal.py", "feature_contract.py")


def _cache_dir() -> Path:
    base = Path(os.environ.get("PULSETWIN_JOBS_DIR") or tempfile.gettempdir())
    return base / "twin_cache"


def cache_key(plant_id: str, budget_threshold: float) -> str:
    h = hashlib.sha256(f"{plant_id}|{budget_threshold!r}|{SNAPSHOT_SEED}|{HORIZON_DAYS}".encode())
    for rel in _SOURCES + (f"configs/{plant_id}.yaml",):
        p = ROOT / rel
        h.update(p.read_bytes() if p.exists() else b"-")
    art = ROOT / "artifacts"
    for name in (f"model_{plant_id}.npz", f"model_selection_{plant_id}.json"):
        p = art / name
        h.update(p.read_bytes() if p.exists() else b"-")
    return h.hexdigest()[:20]


def build(plant_id: str, budget_threshold: float) -> LiveTwinEngine:
    cfg = with_horizon(load_plant_config(ROOT / "configs" / f"{plant_id}.yaml"), HORIZON_DAYS)
    twin = LiveTwinEngine(cfg, seed=SNAPSHOT_SEED, artifacts=ROOT / "artifacts",
                          budget_threshold=budget_threshold)
    twin.start()
    return twin


def warm_twin(plant_id: str, budget_threshold: float) -> LiveTwinEngine:
    """A freshly loaded warmed twin -- a private object the caller may mutate."""
    path = _cache_dir() / f"{plant_id}-{cache_key(plant_id, budget_threshold)}.pkl"
    try:
        with path.open("rb") as fh:
            return pickle.load(fh)
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ImportError):
        pass
    twin = build(plant_id, budget_threshold)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        with tmp.open("wb") as fh:
            pickle.dump(twin, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)          # a concurrent reader never sees half a file
    except OSError:
        pass                            # an unwritable cache only costs speed
    return twin
