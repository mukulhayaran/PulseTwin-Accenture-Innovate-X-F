"""The XGBoost challenger (ADR-061): split discipline, tuning determinism, calibration,
tie-aware thresholds, diversity, runtime round trip, API contract, promotion safety.

Most tests use a small synthetic chronological dataset so they run in seconds; the API
tests use the committed artifacts, as the rest of the suite does.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("xgboost")

from offline.ml import xgb_challenger as XC  # noqa: E402
from serve.xgb_scorer import XGBScorer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"


def _synthetic(n_per_day: int = 120, seed: int = 0):
    """28 days in the champion's own split layout, with a learnable signal and NaNs."""
    rng = np.random.default_rng(seed)
    days = np.repeat(np.arange(1, 29), n_per_day)
    n = len(days)
    X = pd.DataFrame(rng.normal(size=(n, 6)), columns=[f"f{i}" for i in range(6)],
                     dtype=np.float32)
    logit = -3.2 + 1.4 * X["f0"] - 0.9 * X["f1"] + 0.6 * (X["f2"] > 1)
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(np.int8)
    X.loc[rng.random(n) < 0.08, "f3"] = np.nan
    split = np.where(days <= 14, "train", np.where(days <= 21, "validate", "test"))
    meta = pd.DataFrame({"day": days, "split": split})
    return X, y, meta


QUICK = {"n_estimators": 60, "learning_rate": 0.1, "max_depth": 3, "min_child_weight": 1.0,
         "subsample": 0.9, "colsample_bytree": 0.9, "gamma": 0.0, "reg_alpha": 0.0,
         "reg_lambda": 1.0, "scale_pos_weight": 1.0}


# ------------------------------------------------------------------ chronology

def test_splits_are_chronological_and_test_is_never_in_pre_test():
    X, y, meta = _synthetic()
    parts = XC.pre_test(X, y, meta)
    assert set(parts) == {"train", "validate"}
    assert parts["train"].day.max() < parts["validate"].day.min()
    (f1_tr, f1_ev), (f2_tr, f2_ev) = XC.walk_forward_folds(parts)
    assert f1_tr.day.max() < f1_ev.day.min() <= f1_ev.day.max() <= 14
    assert f2_tr.day.max() < f2_ev.day.min()
    te = XC.test_rows(X, y, meta)
    assert te.day.min() > max(f2_ev.day.max(), f1_ev.day.max())


def test_tuning_never_reads_the_test_period():
    """Corrupt every test label and feature: the search must select the same model with
    the same validation metrics, because tune() only ever receives pre-test rows."""
    X, y, meta = _synthetic()
    kw = dict(n_trials=2, shifts_per_day=3, max_per_shift=6.0, recall_floor=0.2,
              n_jobs=1, log=lambda *_: None)
    a = XC.tune(X, y, meta, **kw)["best"]
    Xc, yc = X.copy(), y.copy()
    te = meta["split"].to_numpy() == "test"
    Xc.loc[te, :] = np.nan
    yc[te] = 1 - yc[te]
    b = XC.tune(Xc, yc, meta, **kw)["best"]
    assert a["params"] == b["params"] and a["trial"] == b["trial"]
    assert a["validation"]["precision"] == b["validation"]["precision"]
    assert a["validation"]["threshold"] == b["validation"]["threshold"]


# ---------------------------------------------------------------- search space

def test_search_is_deterministic_with_seed_42_and_bounded():
    a = XC.candidate_params(20, pos_weight=39.0)
    assert a == XC.candidate_params(20, pos_weight=39.0)
    assert a != XC.candidate_params(20, pos_weight=39.0, seed=7)
    spw = {1.0, round(39.0 ** 0.5, 3), 19.5, 39.0}
    for p in a:
        assert 100 <= p["n_estimators"] <= 1200 and 2 <= p["max_depth"] <= 8
        assert 0.01 <= p["learning_rate"] <= 0.15 and 1 <= p["min_child_weight"] <= 30
        assert 0.6 <= p["subsample"] <= 1.0 and 0.5 <= p["colsample_bytree"] <= 1.0
        assert 0 <= p["gamma"] <= 10 and 0 <= p["reg_alpha"] <= 10
        assert 0.5 <= p["reg_lambda"] <= 30 and p["scale_pos_weight"] in spw


# ----------------------------------------------------------------- calibration

@pytest.mark.parametrize("method", ["platt", "isotonic"])
def test_calibrated_scores_are_probabilities(method):
    rng = np.random.default_rng(1)
    raw = rng.random(2000)
    y = (rng.random(2000) < raw * 0.2).astype(int)
    cal = XC.fit_calibrator(method, raw, y)
    out = cal.apply(np.array([0.0, 1e-12, 0.3, 1 - 1e-12, 1.0]))
    assert np.all((out >= 0) & (out <= 1)) and np.all(np.diff(out) >= -1e-12)


def test_calibration_is_fitted_on_validation_rows_only(monkeypatch):
    X, y, meta = _synthetic()
    n_val = int((meta["split"] == "validate").sum())
    val_days = set(meta.loc[meta["split"] == "validate", "day"])
    seen: list[int] = []
    real = XC.fit_calibrator
    monkeypatch.setattr(XC, "fit_calibrator",
                        lambda m, raw, yy: (seen.append(len(raw)), real(m, raw, yy))[1])
    parts = XC.pre_test(X, y, meta)
    XC.evaluate_candidate(0, QUICK, XC.walk_forward_folds(parts), 21.0, 126, 0.2, 6.0, 1)
    assert seen and max(seen) == n_val          # full-validation refits
    assert all(s <= n_val for s in seen)        # and the cross-fitting halves
    assert set(parts["validate"].day) == val_days


# ------------------------------------------------------------------ thresholds

def test_a_tie_that_would_overrun_the_budget_is_excluded_and_reported():
    p = np.array([0.9, 0.8, 0.8, 0.8, 0.5, 0.4])
    t = XC.select_threshold(p, budget_alerts=3)
    assert (t["threshold"], t["alerts"]) == (0.9, 1)
    assert t["naive_alerts"] == 4 and t["tie_overrun_avoided"] == 3
    t4 = XC.select_threshold(p, budget_alerts=4)
    assert (t4["threshold"], t4["alerts"], t4["score_count_at_threshold"]) == (0.8, 4, 3)


def test_a_plateau_larger_than_the_budget_alerts_on_nothing():
    """The champion's situation on validation: 162 units share the top score, budget 126."""
    p = np.r_[np.full(162, 0.406079), np.linspace(0.0, 0.39, 500)]
    t = XC.select_threshold(p, budget_alerts=126)
    assert t["alerts"] == 0 and t["naive_alerts"] == 162
    assert (p >= t["threshold"]).sum() == 0


def test_precision_recall_and_confusion_counts():
    y = np.array([1, 1, 0, 0, 1, 0, 0, 0])
    p = np.array([.9, .8, .7, .6, .2, .1, .1, .05])
    m = XC.alert_metrics(y, p, threshold=0.6, n_shifts=2)
    assert (m["true_positives"], m["false_positives"], m["false_negatives"]) == (2, 2, 1)
    assert m["precision"] == pytest.approx(0.5) and m["recall"] == pytest.approx(2 / 3)
    assert m["alerts"] == 4 and m["alerts_per_shift"] == 2.0


def test_score_diversity_detects_a_collapsed_cohort():
    flat = XC.diversity([0.4060785366] * 178)
    assert flat["collapsed"] and flat["unique_scores"] == 1 and flat["entropy_bits"] == 0.0
    near = XC.diversity([0.40607860, 0.40607870])      # both 0.406079 at 6 decimals
    assert near["collapsed"]
    spread = XC.diversity([0.1, 0.2, 0.3, 0.4])
    assert not spread["collapsed"] and spread["entropy_bits"] == pytest.approx(2.0)


# ------------------------------------------------------------- runtime round trip

@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    X, y, meta = _synthetic()
    parts = XC.pre_test(X, y, meta)
    clf = XC.fit_xgb(QUICK, parts["train"], parts["validate"], n_jobs=1)
    raw_va = XC.raw_proba(clf, parts["validate"].X)
    cal = XC.fit_calibrator("platt", raw_va, parts["validate"].y)
    art = XC.runtime_artifact(clf, cal, list(X.columns), 0.3,
                              {"model_type": "xgboost", "model_version": "t1",
                               "calibration_method": "platt"})
    path = tmp_path_factory.mktemp("xgb") / "challenger.json"
    path.write_text(json.dumps(art), encoding="utf-8")
    return clf, cal, X, path


def test_exported_booster_scores_identically_without_xgboost(trained):
    clf, cal, X, path = trained
    s = XGBScorer(path)
    Xn = X.to_numpy(dtype=np.float64)
    assert np.max(np.abs(s.predict_raw_proba(Xn) - XC.raw_proba(clf, X))) < 1e-5
    assert np.max(np.abs(s.predict(Xn) - cal.apply(XC.raw_proba(clf, X)))) < 1e-5
    p = s.predict(Xn)
    assert np.all((p >= 0) & (p <= 1))
    x = Xn[3]
    contrib = sum(f["contribution"] for f in s.explain(x, top=len(s.feature_names)))
    assert contrib == pytest.approx(s.raw_score(x.reshape(1, -1))[0] - s.expected_value, abs=1e-5)


def test_a_promoted_selection_file_loads_the_xgboost_scorer(trained, tmp_path):
    from serve.model_loader import load_scorer
    _, _, _, path = trained
    (tmp_path / path.name).write_bytes(path.read_bytes())
    (tmp_path / "model_selection_plant_z.json").write_text(json.dumps(
        {"champion": "xgboost", "champion_artifact": path.name,
         "challenger": {"promoted": True}}), encoding="utf-8")
    scorer, info = load_scorer(tmp_path, "plant_z")
    assert isinstance(scorer, XGBScorer)
    assert info["model_type"] == "xgboost" and info["calibration_method"] == "platt"


# ------------------------------------------------------------------ promotion

def _published():
    return {"precision_at_budget": 0.3539325843, "recall_at_budget": 0.2763157895,
            "pr_auc": 0.2558659238, "brier": 0.0206112605, "alerts_per_shift": 8.47619}


INVARIANTS = {"features_identical": True, "lead_time_identical": True,
              "test_untouched_during_tuning": True}


def _challenger(**over):
    base = {"precision": 0.40, "recall": 0.30, "pr_auc": 0.28, "brier": 0.0205, "ece": 0.012,
            "alerts_per_shift": 5.9,
            "alert_cohort": {"unique_scores": 120, "entropy_bits": 6.9, "modal_share": 0.01}}
    return {**base, **over}


def test_every_gate_must_pass_to_promote():
    ok = XC.promotion_gates(_challenger(), {"ece": 0.0104}, _published(), 6.0, INVARIANTS)
    assert ok["promote"] and ok["failed"] == []
    for over, gate in (({"recall": 0.27}, "recall_at_budget"),
                       ({"precision": 0.35}, "precision_at_budget"),
                       ({"pr_auc": 0.25}, "pr_auc"),
                       ({"brier": 0.03}, "calibration"),
                       ({"alerts_per_shift": 9.0}, "alert_budget"),
                       ({"alert_cohort": {"unique_scores": 1, "entropy_bits": 0.0,
                                          "modal_share": 1.0}}, "score_diversity")):
        d = XC.promotion_gates(_challenger(**over), {"ece": 0.0104}, _published(), 6.0,
                               INVARIANTS)
        assert not d["promote"] and gate in d["failed"]


def test_a_failed_challenger_leaves_the_hgb_champion_serving(tmp_path):
    from serve.model_loader import load_scorer
    from serve.scorer import Scorer
    npz = ARTIFACTS / "model_plant_a.npz"
    if not npz.exists():
        pytest.skip("artifacts not built")
    (tmp_path / npz.name).write_bytes(npz.read_bytes())
    (tmp_path / "model_selection_plant_a.json").write_text(json.dumps(
        {"champion": "hist_gradient_boosting", "champion_artifact": npz.name,
         "challenger": {"promoted": False, "failed_gates": ["recall_at_budget"]}}),
        encoding="utf-8")
    scorer, info = load_scorer(tmp_path, "plant_a")
    assert isinstance(scorer, Scorer) and info["model_type"] == "hist_gradient_boosting"
    assert info["challenger"]["failed_gates"] == ["recall_at_budget"]


# ------------------------------------------------------------------ API contract

@pytest.fixture(scope="module")
def client():
    if not (ARTIFACTS / "pulsetwin.db").exists():
        pytest.skip("artifacts not built")
    from fastapi.testclient import TestClient

    from serve.main import app
    with TestClient(app) as c:
        yield c


def test_predict_keeps_its_old_fields_and_adds_the_new_ones(client):
    from serve.main import STATE
    names = STATE["scorers"]["plant_a"].feature_names[:40]
    r = client.post("/api/predict/defect",
                    json={"plant_id": "plant_a", "features": {n: 0.0 for n in names}})
    assert r.status_code == 200
    body = r.json()
    for old in ("probability", "calibrated", "confidence_interval", "explanation_mode",
                "top_factors", "recommended_action", "model_id", "model_version"):
        assert old in body
    assert body["calibrated_risk"] == body["probability"]       # the supervisor's risk
    assert 0 <= body["raw_model_score"] <= 1
    assert body["model_type"] and body["calibration_method"] and body["alert_threshold"]
    assert body["risk_band"] == ("monitor" if body["probability"] < body["alert_threshold"]
                                 else ("high" if body["probability"] > 0.5 else "at_risk"))


def test_an_xgboost_champion_answers_through_the_same_endpoint(client, trained):
    """Swap an XGBoost scorer in as plant_a's champion and call the unchanged endpoint."""
    from serve.main import STATE
    _, _, _, path = trained
    xs = XGBScorer(path)
    saved = STATE["scorers"]["plant_a"], STATE["model_info"]["plant_a"]
    STATE["scorers"]["plant_a"] = xs
    STATE["model_info"]["plant_a"] = {"model_id": "plant_a_defect_risk_xgboost",
                                      "version": 2, "alert_threshold": xs.alert_threshold}
    try:
        body = client.post("/api/predict/defect", json={
            "plant_id": "plant_a", "features": {"f0": 2.5, "f1": -1.0}}).json()
    finally:
        STATE["scorers"]["plant_a"], STATE["model_info"]["plant_a"] = saved
    assert body["model_type"] == "xgboost" and body["model_id"] == "plant_a_defect_risk_xgboost"
    assert body["alert_threshold"] == pytest.approx(0.3)
    assert body["calibrated_risk"] == pytest.approx(
        float(xs.predict(xs.vector_from({"f0": 2.5, "f1": -1.0}).reshape(1, -1))[0]))
    assert body["explanation_mode"] == "decision_path"


def test_under_alerting_cannot_win_on_precision_when_the_recall_floor_is_unreachable():
    """Run 1's degenerate winner: 32 of 126 alert slots at precision 0.44 / recall 0.04
    beat full-budget candidates on precision alone. The closest-to-floor candidate wins."""
    def cand(trial, precision, recall, uniq=100, modal=0.02):
        return {"trial": trial, "eligible": False, "recall_shortfall": max(0, 0.276 - recall),
                "walk_forward_mean_pr_auc": 0.2,
                "constraints": {"recall_floor": False, "alert_budget": True,
                                "diverse_alert_cohort": modal <= 0.5},
                "validation": {"precision": precision, "pr_auc": 0.25}}
    ranked = sorted([cand(26, 0.44, 0.04), cand(24, 0.42, 0.14), cand(9, 0.50, 0.20, 2, 0.8)],
                    key=XC.rank_key)
    assert [c["trial"] for c in ranked] == [24, 26, 9]   # a collapsed cohort ranks last


# ------------------------------------------------ ADR-062: trailing-window threshold

def test_runtime_budget_cut_agrees_with_the_offline_threshold_rule():
    from serve.rolling_threshold import budget_cut
    rng = np.random.default_rng(3)
    for trial in range(40):
        p = np.round(rng.random(rng.integers(1, 400)), rng.integers(1, 4))  # heavy ties
        b = int(rng.integers(0, 60))
        assert budget_cut(p, b) == XC.select_threshold(p, b)["threshold"]


def test_the_trailing_window_tracks_the_budget_and_reads_no_label():
    """A score distribution that drifts upward: a static cut over-alerts, the trailing
    window re-centres on the budget (6 of every 100 units here)."""
    from serve.rolling_threshold import TrailingBudgetThreshold
    rng = np.random.default_rng(4)
    seed = rng.random(1000)
    drifted = 0.6 + 0.4 * rng.random(3000)   # shifted up (no clipped ties at 1.0)
    static_cut = XC.select_threshold(seed, 60)["threshold"]
    flags, _ = XC.trailing_budget_alerts(seed, drifted, window_units=1000, budget_alerts=60)
    assert (drifted >= static_cut).mean() > 0.12          # static: ~2x over budget
    assert abs(flags[1000:].mean() - 0.06) < 0.015          # trailing: back on budget
    roll = TrailingBudgetThreshold(seed, 1000, 60)
    import copy
    fork = copy.deepcopy(roll)                               # the counterfactual forks this
    fork.observe(0.99)
    assert len(roll._scores) == len(fork._scores) == 1000 and roll._scores[-1] != 0.99


def test_a_promoted_champion_rescoring_is_complete_and_hgb_is_untouched(trained, tmp_path):
    from offline import champion
    clf, _, X, path = trained
    (tmp_path / path.name).write_bytes(path.read_bytes())
    assert champion.promoted_xgb(tmp_path, "plant_z") is None     # no selection -> HGB
    (tmp_path / "model_selection_plant_z.json").write_text(json.dumps(
        {"champion": "xgboost", "champion_artifact": path.name}), encoding="utf-8")
    scorer = champion.promoted_xgb(tmp_path, "plant_z")
    feats = X.copy()
    feats.index = np.arange(len(X))
    scores = pd.DataFrame({"unit_seq": feats.index, "prediction_ts": np.arange(len(X), 0, -1),
                           "risk_raw": 0.0, "calibrated_risk": 0.0, "alert_fired": 0,
                           "above_cost_threshold": 0})
    out = champion.rescore(scorer, scores, feats, cost_threshold=0.05)
    assert np.allclose(out["calibrated_risk"], scorer.predict(feats.to_numpy(np.float64)))
    assert out["alert_fired"].sum() > 0 and scores["alert_fired"].sum() == 0


def test_the_champion_budget_threshold_is_chosen_on_validation():
    from offline.ml.train import DATA_ROOT
    m = DATA_ROOT / "plant_a" / "metrics.json"
    if not m.exists():
        pytest.skip("models not trained")
    thr = json.loads(m.read_text(encoding="utf-8"))["thresholds"]
    assert thr["threshold_selected_on"] == "validation"
    assert thr["budget_alerts_at_selection"] >= thr["budget_alerts_intended"]
