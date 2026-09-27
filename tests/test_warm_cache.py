"""ADR-064 CPU work: the warmed twin comes from a content-keyed disk cache.

A cache is only acceptable if it is invisible: a cached twin's future must be
bit-identical to a freshly warmed one, and a bad entry must be rebuilt, not trusted."""

from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")

from offline.counterfactual import _reseed  # noqa: E402
from offline.live_twin import warm_cache as W  # noqa: E402

PLANT, THRESHOLD = "plant_a", 0.406


def _future(twin, seed=5):
    _reseed(twin, seed)
    twin.advance(1800.0)
    return (twin.now, twin.units_completed, twin.alerts_opened, twin.events_processed)


def test_a_cached_twin_is_indistinguishable_from_a_fresh_one(monkeypatch, tmp_path):
    monkeypatch.setenv("PULSETWIN_JOBS_DIR", str(tmp_path))
    first = W.warm_twin(PLANT, THRESHOLD)                    # cold: builds and writes
    files = list((tmp_path / "twin_cache").glob(f"{PLANT}-*.pkl"))
    assert len(files) == 1
    cached = W.warm_twin(PLANT, THRESHOLD)                   # warm: loads
    assert cached is not first                               # a private object each time
    fresh = W.build(PLANT, THRESHOLD)
    assert _future(cached) == _future(fresh) == _future(first)


def test_a_corrupt_entry_is_rebuilt_and_the_key_tracks_its_inputs(monkeypatch, tmp_path):
    monkeypatch.setenv("PULSETWIN_JOBS_DIR", str(tmp_path))
    W.warm_twin(PLANT, THRESHOLD)
    entry = next((tmp_path / "twin_cache").glob("*.pkl"))
    entry.write_bytes(b"not a pickle")
    twin = W.warm_twin(PLANT, THRESHOLD)
    assert twin.units_completed > 0                          # rebuilt, not trusted
    assert W.cache_key(PLANT, THRESHOLD) != W.cache_key(PLANT, THRESHOLD + 0.01)
