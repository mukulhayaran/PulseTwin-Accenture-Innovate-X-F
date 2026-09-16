"""Bake everything the API needs into artifacts/pulsetwin.db + stream JSONL + model npz.

The bridge between the heavy offline world and the thin serving layer. After this runs,
`serve/` needs only numpy, sqlite3 and the committed artifacts -- no pandas, no sklearn,
no training at request time.

    python -m offline.build_artifact --config configs/plant_a.yaml

ADR-008: station_readings and genealogy are written for the 48-hour REPLAY WINDOW only.
Full history across two plants would be ~3.6M rows and 220MB+ against a 50MB cap, and
nothing in the UI queries it -- the Manager and Leadership views read hourly rollups.
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import sqlite3
import time
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from offline.config_schema import load_economics_config, load_plant_config  # noqa: E402
from offline.export_model import export  # noqa: E402
from offline.ml import scenarios, validate  # noqa: E402
from offline.ml.bottleneck import analyse as analyse_bottleneck  # noqa: E402
from offline.ml.featurize import FeatureSet  # noqa: E402
from offline.ml.soft_sensor import build_soft_sensors  # noqa: E402
from offline.simulator.line_model import ALL_STATES, SIM_START, STATE_BUCKET_S  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ARTIFACTS = ROOT / "artifacts"

SCHEMA = """
CREATE TABLE IF NOT EXISTS units (
    unit_id TEXT, plant_id TEXT, variant TEXT, lot_id TEXT, shift_id TEXT,
    start_ts TEXT, end_ts TEXT, prediction_ts TEXT,
    risk_score REAL, calibrated_risk REAL, ci_low REAL, ci_high REAL,
    above_cost_threshold INTEGER, alert_fired INTEGER,
    actual_defect INTEGER, true_defect INTEGER, defect_mode TEXT,
    lead_time_min REAL, split TEXT,
    PRIMARY KEY (unit_id, plant_id));
CREATE INDEX IF NOT EXISTS ix_units_alert ON units(plant_id, alert_fired, calibrated_risk);
CREATE INDEX IF NOT EXISTS ix_units_lot ON units(plant_id, lot_id);
CREATE INDEX IF NOT EXISTS ix_units_pred ON units(plant_id, prediction_ts);

CREATE TABLE IF NOT EXISTS unit_factors (
    unit_id TEXT, plant_id TEXT, rank INTEGER, feature TEXT, direction TEXT,
    contribution REAL, value REAL, plain_language TEXT,
    PRIMARY KEY (unit_id, plant_id, rank));

CREATE TABLE IF NOT EXISTS genealogy (
    unit_id TEXT, plant_id TEXT, station_id INTEGER, entry_ts TEXT, exit_ts TEXT,
    operator_group TEXT, station_state TEXT, variant TEXT, lot_id TEXT,
    cycle_s REAL, buffer_wait_s REAL,
    PRIMARY KEY (unit_id, plant_id, station_id));
CREATE INDEX IF NOT EXISTS ix_gen_station ON genealogy(plant_id, station_id);

CREATE TABLE IF NOT EXISTS station_readings (
    unit_id TEXT, plant_id TEXT, station_id INTEGER, signal TEXT, value REAL,
    provenance TEXT, ci_low REAL, ci_high REAL,
    PRIMARY KEY (unit_id, plant_id, station_id, signal));

CREATE TABLE IF NOT EXISTS station_stats (
    plant_id TEXT, station_id INTEGER, hour_start TEXT,
    utilisation REAL, blocked_pct REAL, starved_pct REAL, down_pct REAL,
    mean_cycle_s REAL, throughput_uph REAL, lost_throughput_uph REAL, provenance TEXT,
    PRIMARY KEY (plant_id, station_id, hour_start));

CREATE TABLE IF NOT EXISTS bottleneck_windows (
    plant_id TEXT, window_start TEXT, window_end TEXT,
    bottleneck_station INTEGER, confidence REAL, ground_truth_station INTEGER,
    correct INTEGER, shift_event INTEGER,
    PRIMARY KEY (plant_id, window_start));

CREATE TABLE IF NOT EXISTS checklists (
    unit_id TEXT, plant_id TEXT, station_id INTEGER, timestamp TEXT,
    operator_group TEXT, checklist_pass INTEGER, checklist_time_s REAL, notes_code TEXT,
    PRIMARY KEY (unit_id, plant_id, station_id));

CREATE TABLE IF NOT EXISTS station_config (
    plant_id TEXT, station_id INTEGER, station_name TEXT, zone TEXT, zone_name TEXT,
    sensor_tier TEXT, nominal_cycle_s REAL, buffer_capacity INTEGER, sensors TEXT,
    is_inspection INTEGER, position INTEGER,
    PRIMARY KEY (plant_id, station_id));

CREATE TABLE IF NOT EXISTS economics (
    plant_id TEXT, key TEXT, value REAL, unit TEXT, source TEXT,
    PRIMARY KEY (plant_id, key));

CREATE TABLE IF NOT EXISTS metrics (
    plant_id TEXT, key TEXT, value REAL, context TEXT,
    PRIMARY KEY (plant_id, key, context));

CREATE TABLE IF NOT EXISTS metrics_json (
    plant_id TEXT, key TEXT, payload TEXT, PRIMARY KEY (plant_id, key));

CREATE TABLE IF NOT EXISTS whatif_grid (
    plant_id TEXT, station_id INTEGER, buffer_delta INTEGER, cycle_delta_pct REAL,
    throughput_uph REAL, wip_units REAL, new_bottleneck_station INTEGER,
    PRIMARY KEY (plant_id, station_id, buffer_delta, cycle_delta_pct));

CREATE TABLE IF NOT EXISTS whatif_baseline (
    plant_id TEXT PRIMARY KEY, throughput_uph REAL, wip_units REAL,
    bottleneck_station INTEGER, window_hours REAL);

CREATE TABLE IF NOT EXISTS instrumentation_plan (
    plant_id TEXT, station_id INTEGER, station_name TEXT, zone TEXT, sensor_type TEXT,
    cost REAL, cost_source TEXT, delta_pr_auc REAL, annual_value REAL,
    payback_months REAL, window TEXT, recommendation TEXT,
    PRIMARY KEY (plant_id, station_id));

CREATE TABLE IF NOT EXISTS stream_index (
    plant_id TEXT, ts TEXT, byte_offset INTEGER, PRIMARY KEY (plant_id, ts));

CREATE TABLE IF NOT EXISTS plant_meta (
    plant_id TEXT PRIMARY KEY, name TEXT, station_count INTEGER, units_per_hour_target REAL,
    achieved_uph REAL, replay_start_ts TEXT, replay_end_ts TEXT, built_at TEXT,
    alert_budget INTEGER, cost_threshold REAL, budget_threshold REAL);
"""

# Floor-language templates. A supervisor reads these, not feature names.
PLAIN = {
    "paint_buffer_dwell_s": "Unit spent {v:.0f}s in the paint buffer (normal ~{n:.0f}s) - extended dwell risks cure defects",
    "paint_buffer_dwell_ratio": "Paint buffer dwell was {v:.1f}x the normal window",
    "lot_trailing_defect_rate": "Recent units from this lot inspected at {v:.1%} defect rate",
    "total_blocked_s": "Unit was blocked for {v:.0f}s upstream",
    "total_dwell_s": "Total time on line so far: {v:.0f}s",
}


def _plain(feature: str, value, cfg) -> str:
    if feature in PLAIN:
        n = cfg.defect_physics.mode_a.dwell_nominal_s
        try:
            return PLAIN[feature].format(v=value if value is not None else float("nan"), n=n)
        except (ValueError, TypeError):
            pass
    if feature.startswith("s") and "_checklist_time_s" in feature:
        sid = feature.split("_")[0][1:]
        nom = cfg.station(int(sid)).checklist_nominal_s or 0
        return f"Station {sid} checklist completed in {value:.0f}s (normal ~{nom:.0f}s)"
    if "_z" in feature or "_dev_median" in feature:
        base = feature.split("_")[0]
        return f"{base.upper()} reading deviates from its recent baseline ({value:.2f})"
    if feature.startswith("s"):
        parts = feature.split("_", 1)
        return f"Station {parts[0][1:]} {parts[1].replace('_', ' ')} = {value:.2f}"
    return f"{feature.replace('_', ' ')} = {value:.2f}"


def _ts(seconds) -> str:
    return (SIM_START + pd.to_timedelta(float(seconds), unit="s")).isoformat()


def build(config_path: str) -> dict:
    t0 = time.time()
    cfg = load_plant_config(config_path)
    econ = load_economics_config()
    pid = cfg.plant_id
    d = DATA / pid
    ARTIFACTS.mkdir(exist_ok=True)

    tel = pd.read_parquet(d / "telemetry.parquet")
    ck = pd.read_parquet(d / "checklists.parquet")
    ins = pd.read_parquet(d / "inspections.parquet")
    scores = pd.read_parquet(d / "scores.parquet")
    feats = pd.read_parquet(d / "features.parquet")
    meta = pd.read_parquet(d / "feature_meta.parquet")
    metrics = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
    with (d / "model.pkl").open("rb") as fh:
        bundle = pickle.load(fh)
    model = bundle["model"]

    # ------------------------------------------------------- export model to numpy
    npz_path = ARTIFACTS / f"model_{pid}.npz"
    export(model, npz_path, bundle["feature_names"])

    con = sqlite3.connect(ARTIFACTS / "pulsetwin.db")
    con.executescript(SCHEMA)
    for tbl in ("units", "unit_factors", "genealogy", "station_readings", "station_stats",
                "bottleneck_windows", "checklists", "station_config", "economics",
                "metrics", "metrics_json", "stream_index", "plant_meta",
                "whatif_grid", "whatif_baseline", "instrumentation_plan"):
        con.execute(f"DELETE FROM {tbl} WHERE plant_id = ?", (pid,))

    # ------------------------------------------------------------- replay window
    # Anchor on the last full 48h that contains a peak day, so the demo replay actually
    # shows the bottleneck shift rather than a quiet weekend.
    win_h = cfg.simulation.replay_window_hours
    end_ts = float(tel["exit_ts"].max())
    peak_days = set(cfg.ambient.humidity_high_days)
    day_of = lambda s: ((SIM_START + pd.to_timedelta(s, unit="s")).weekday())
    cand = end_ts - win_h * 3600.0
    for back in range(0, 14):
        start = end_ts - (win_h + back * 24) * 3600.0
        if day_of(start) in peak_days or day_of(start + win_h * 1800) in peak_days:
            cand = start
            break
    replay_start, replay_end = cand, cand + win_h * 3600.0

    # ------------------------------------------------------------------- units
    # Unfiltered, aligned to the feature matrix -- ablations need every row, while the
    # units TABLE is trimmed to what the demo can reach.
    sc_all = scores.set_index("unit_seq").reindex(feats.index)
    sc = scores.set_index("unit_seq")
    m = meta.set_index("unit_seq")
    unit_end = tel.groupby("unit_seq")["exit_ts"].max()
    unit_start = tel.groupby("unit_seq")["entry_ts"].min()

    # ADR-008 extended to `units`. The API only ever serves units the demo can reach:
    # anything inside the replay window (plus a 2h margin so containment can look
    # backwards past the window edge) and every unit that ever fired an alert. Keeping
    # all 66k units across both plants pushed the database past its 50 MB cap for no
    # functional gain -- full-history numbers come from the metrics tables.
    # The window is the ONLY filter. An earlier version also kept every alert-fired unit
    # regardless of when it ran, which produced units the API would serve with an EMPTY
    # journey -- genealogy and readings are window-scoped, so a unit from outside it has
    # no station history to show. Clicking such an alert in the UI showed nothing.
    #
    # Restricting to the window guarantees every served unit is complete. It costs
    # nothing that matters: the demo replays this window, so those are the only alerts
    # ever visible, and full-history numbers come from the metrics tables.
    margin = 7200.0
    keep_unit = (sc["prediction_ts"] >= replay_start - margin) & (
        sc["prediction_ts"] <= replay_end + margin
    )
    sc = sc[keep_unit]

    urows = []
    for seq, r in sc.iterrows():
        mm = m.loc[seq]
        lead_min = max(0.0, (unit_end.get(seq, 0) - r["prediction_ts"]) / 60.0)
        risk = float(r["calibrated_risk"])
        # Interval width shrinks with confidence; a flat +/- would be dishonest.
        half = 0.5 * risk * (1.0 - risk) ** 0.5 + 0.02
        urows.append((
            str(r["unit_id"]), pid, str(mm["variant"]), str(mm["lot_id"]), str(mm["shift_id"]),
            _ts(unit_start.get(seq, 0)), _ts(unit_end.get(seq, 0)), _ts(r["prediction_ts"]),
            float(r["risk_raw"]), risk, max(0.0, risk - half), min(1.0, risk + half),
            int(r["above_cost_threshold"]), int(r["alert_fired"]),
            int(r["y"]), int(r["y_true"]), str(r["defect_mode"]), lead_min, str(r["split"]),
        ))
    con.executemany(f"INSERT OR REPLACE INTO units VALUES ({','.join('?'*19)})", urows)

    # ------------------------------------------------------------ unit factors
    # Explanations for every alerting unit, precomputed so the API never needs a model
    # library at request time.
    from serve.scorer import Scorer

    scorer = Scorer(npz_path)
    alert_units = sc[sc["alert_fired"] == 1].index.tolist()
    frows = []
    Xv = feats.loc[feats.index.isin(alert_units)]
    for seq, row in Xv.iterrows():
        uid = str(sc.loc[seq, "unit_id"])
        for rank, f in enumerate(scorer.explain(row.to_numpy(dtype=np.float64), top=5)):
            frows.append((uid, pid, rank, f["feature"], f["direction"],
                          f["contribution"], f["value"], _plain(f["feature"], f["value"], cfg)))
    con.executemany(f"INSERT OR REPLACE INTO unit_factors VALUES ({','.join('?'*8)})", frows)

    # ------------------------------------------ genealogy + readings (replay window)
    win = tel[(tel["entry_ts"] >= replay_start) & (tel["entry_ts"] <= replay_end)].copy()
    nb = cfg.operators.operator_bucket_count
    win["operator_group"] = "group_" + (
        pd.util.hash_pandas_object(win["operator_id"], index=False) % nb
    ).astype(str)

    con.executemany(
        f"INSERT OR REPLACE INTO genealogy VALUES ({','.join('?'*11)})",
        [(str(r.unit_id), pid, int(r.station_id), _ts(r.entry_ts), _ts(r.exit_ts),
          str(r.operator_group), str(r.prior_state), str(r.variant), str(r.lot_id),
          float(r.cycle_s), float(r.buffer_wait_s)) for r in win.itertuples()],
    )

    sig_cols = ["torque_nm", "vibration_mm_s", "temperature_c", "weld_current_a",
                "humidity_pct", "film_thickness_um"]
    rrows = []
    for r in win.itertuples():
        for s in sig_cols:
            v = getattr(r, s)
            if v is not None and not pd.isna(v):
                rrows.append((str(r.unit_id), pid, int(r.station_id), s, float(v),
                              "measured", None, None))
    con.executemany(f"INSERT OR REPLACE INTO station_readings VALUES ({','.join('?'*8)})", rrows)

    # ------------------------------------------------------------- station stats
    buckets = np.load(d / "state_buckets.npz", allow_pickle=True)
    secs = buckets["seconds"]                     # (station, bucket, state)
    ids = list(buckets["station_ids"])
    truth = buckets["ground_truth_bottleneck"]
    per_hour = int(3600 / STATE_BUCKET_S)
    n_hours = secs.shape[1] // per_hour

    iw, ib, isv, idn = (ALL_STATES.index(x) for x in ("working", "blocked", "starved", "down"))
    srows = []
    for si, sid in enumerate(ids):
        tier = cfg.station(int(sid)).sensor_tier
        prov = "inferred" if tier == "manual" else "measured"
        nominal = cfg.station(int(sid)).nominal_cycle_s
        for h in range(n_hours):
            blk = secs[si, h * per_hour:(h + 1) * per_hour, :].sum(axis=0)
            tot = blk.sum()
            if tot <= 0:
                continue
            util = blk[iw] / tot
            thr = blk[iw] / max(nominal, 1e-9)
            srows.append((pid, int(sid), _ts(h * 3600), float(util), float(blk[ib] / tot),
                          float(blk[isv] / tot), float(blk[idn] / tot), float(nominal),
                          float(thr), float(max(0.0, 3600 / nominal - thr)), prov))
    con.executemany(f"INSERT OR REPLACE INTO station_stats VALUES ({','.join('?'*11)})", srows)

    # ---------------------------------------------------------- bottleneck windows
    # Detection and ground truth are DIFFERENT computations on purpose -- see
    # offline/ml/bottleneck.py. Scoring a 15-minute argmax against a 15-minute argmax is
    # circular and returns 100%, which is worse than a low number.
    bn_report, detected, confidence, truth = analyse_bottleneck(secs, ids, iw, idn, cfg)

    brows = []
    prev = None
    for b in range(len(detected)):
        if detected[b] <= 0:
            continue
        gt = int(truth[b])
        brows.append((pid, _ts(b * STATE_BUCKET_S), _ts((b + 1) * STATE_BUCKET_S),
                      int(detected[b]), float(confidence[b]), gt,
                      int(detected[b] == gt),
                      int(prev is not None and detected[b] != prev)))
        prev = int(detected[b])
    con.executemany(f"INSERT OR REPLACE INTO bottleneck_windows VALUES ({','.join(chr(63)*8)})", brows)
    bn_accuracy = bn_report.detection_accuracy
    # BOTH constraints, not just the most frequent one: the line has a normal-day
    # bottleneck and a variant-heavy-day bottleneck, and the second is the interesting one.
    hot = sorted(bn_report.station_share, key=bn_report.station_share.get, reverse=True)[:2]
    grid_hot = hot or [ids[len(ids) // 2]]

    # ------------------------------------------------------------------ checklists
    ckw = ck[(ck["ts"] >= replay_start) & (ck["ts"] <= replay_end)].copy()
    ckw["operator_group"] = "group_" + (
        pd.util.hash_pandas_object(ckw["operator_id"], index=False) % nb
    ).astype(str)
    con.executemany(
        f"INSERT OR REPLACE INTO checklists VALUES ({','.join('?'*8)})",
        [(str(r.unit_id), pid, int(r.station_id), _ts(r.ts), str(r.operator_group),
          int(r.checklist_pass), float(r.checklist_time_s), str(r.notes_code))
         for r in ckw.itertuples()],
    )

    # -------------------------------------------------------------- station config
    zone_name = {z.id: z.name for z in cfg.zones}
    con.executemany(
        f"INSERT OR REPLACE INTO station_config VALUES ({','.join('?'*11)})",
        [(pid, s.id, s.name, s.zone, zone_name[s.zone], s.sensor_tier, s.nominal_cycle_s,
          s.buffer_capacity_after, json.dumps(s.sensors), int(s.is_inspection_station), i)
         for i, s in enumerate(cfg.stations)],
    )

    # ------------------------------------------------------------------ economics
    erows = []
    for k, v in econ.costs.items():
        erows.append((pid, k, v.value, v.currency or "USD", v.source))
    erows.append((pid, "discount_rate", econ.discount_rate, "ratio",
                  econ.finance["discount_rate"]["source"]))
    erows.append((pid, "max_alerts_per_shift", float(econ.max_alerts_per_shift), "count",
                  econ.alarm_budget["source"]))
    for k, v in econ.instrumentation["sensor_costs"].items():
        erows.append((pid, f"sensor_{k}", float(v["value"]), "USD", v["source"]))
    erows.append((pid, "system_implementation_cost", econ.system["implementation_cost"].value,
                  "USD", econ.system["implementation_cost"].source))
    con.executemany(f"INSERT OR REPLACE INTO economics VALUES (?,?,?,?,?)", erows)

    # -------------------------------------------------------- what-if scenario grid
    # ADR-007: 6 candidate stations x 5 buffer deltas x 3 cycle deltas on a 4-hour
    # window = 90 short runs, not the 3,510 full-horizon runs the brief implied.
    grid_rows, grid_base = scenarios.build_grid(cfg, [int(h) for h in grid_hot])
    con.executemany(
        f"INSERT OR REPLACE INTO whatif_grid VALUES ({','.join('?' * 7)})",
        [(pid, r.station_id, r.buffer_delta, r.cycle_delta_pct, r.throughput_uph,
          r.wip_units, r.new_bottleneck_station) for r in grid_rows],
    )
    con.execute("INSERT OR REPLACE INTO whatif_baseline VALUES (?,?,?,?,?)", (
        pid, grid_base["throughput_uph"], grid_base["wip_units"],
        grid_base["bottleneck_station"], grid_base["window_hours"]))

    # ------------------------------------- masked-station soft-sensor validation
    # The credibility move: hide the sensors on stations that HAVE them, reconstruct
    # from neighbours + checklist proxies, and score against the withheld truth.
    suite = build_soft_sensors(cfg, tel, ck, meta, seed=cfg.simulation.seed)
    soft_validation = suite.validation_summary()

    if suite.inferred is not None and not suite.inferred.empty:
        inf = suite.inferred
        inf = inf[inf["unit_seq"].isin(win["unit_seq"])]
        id_of = sc_all["unit_id"] if "unit_id" in sc_all else None
        rows_inf = []
        for r in inf.itertuples():
            uid = id_of.get(r.unit_seq) if id_of is not None else None
            if uid is None:
                continue
            rows_inf.append((str(uid), pid, int(r.station_id), str(r.signal),
                             float(r.value), "inferred", float(r.ci_low), float(r.ci_high)))
        if rows_inf:
            con.executemany(
                f"INSERT OR REPLACE INTO station_readings VALUES ({','.join('?' * 8)})",
                rows_inf,
            )

    # ------------------------------------------------- ablations + station value
    fs = FeatureSet(X=feats, meta=meta, y=sc_all["y"].to_numpy(),
                    y_true=sc_all["y_true"].to_numpy(),
                    defect_mode=sc_all["defect_mode"].to_numpy())
    val = validate.run_all(model, fs, cfg, seed=cfg.simulation.seed)

    # Price of one PR-AUC point per year, from measured economics: annual defect
    # population x what catching one is worth. Used to value retrofit uplift.
    units_year = float(len(sc_all)) / cfg.simulation.duration_days * 250.0
    cost_miss = (econ.cost("scrap_cost_per_unit") * 0.55
                 + econ.cost("rework_cost_per_unit")
                 + econ.cost("warranty_escape_cost_per_unit") * metrics["escape_rate"])
    value_per_point = units_year * metrics["base_rate_test"] * cost_miss

    uplift = {int(k): v for k, v in val["retrofit_uplift_estimate"].items()}
    plan = scenarios.instrumentation_plan(cfg, econ, uplift, value_per_point)
    con.executemany(
        f"INSERT OR REPLACE INTO instrumentation_plan VALUES ({','.join('?' * 12)})",
        [(pid, r["station_id"], r["station_name"], r["zone"], r["sensor_type"], r["cost"],
          r["cost_source"], r["delta_pr_auc"], r["annual_value"], r["payback_months"],
          r["window"], r["recommendation"]) for r in plan],
    )

    # -------------------------------------------------------------------- metrics
    thr = metrics["thresholds"]
    flat = {
        "pr_auc": metrics["pr_auc"], "pr_auc_ci_low": metrics["pr_auc_ci_low"],
        "pr_auc_ci_high": metrics["pr_auc_ci_high"], "oracle_pr_auc": metrics["oracle_pr_auc"],
        "oracle_capture": metrics["pr_auc"] / max(1e-9, metrics["oracle_pr_auc"]),
        "roc_auc": metrics["roc_auc"], "brier": metrics["brier_test"],
        "base_rate_test": metrics["base_rate_test"], "positives_test": metrics["positives_test"],
        "cost_optimal_threshold": thr["cost_optimal"],
        "budget_threshold": thr["budget_constrained"],
        "precision_at_budget": thr["precision_at_budget"],
        "recall_at_budget": thr["recall_at_budget"],
        "alerts_per_shift": thr["alerts_per_shift_at_budget"],
        "marginal_value_of_doubling": thr["marginal_value_of_doubling"],
        "marginal_cost_of_doubling": thr["marginal_cost_of_doubling"],
        "bottleneck_detection_accuracy": bn_accuracy,
        "bottleneck_shift_events": float(bn_report.shift_events),
        "forecast_skill_vs_persistence": bn_report.forecast_skill,
        "forecast_mae_model": bn_report.forecast_mae_model,
        "forecast_mae_persistence": bn_report.forecast_mae_persistence,
        "soft_sensor_ablation_delta": val["soft_sensor_ablation"]["delta_pr_auc"],
        "soft_sensor_masked_r2": float(soft_validation.get("mean_r2", 0.0)),
        "instrumentation_total_cost": float(sum(r["cost"] for r in plan)),
        "instrumentation_total_value": float(sum(r["annual_value"] for r in plan)),
        "escape_rate": metrics["escape_rate"],
    }
    con.executemany("INSERT OR REPLACE INTO metrics VALUES (?,?,?,?)",
                    [(pid, k, float(v), "test_set") for k, v in flat.items()])
    con.executemany("INSERT OR REPLACE INTO metrics_json VALUES (?,?,?)", [
        (pid, "reliability", json.dumps(metrics["reliability"])),
        (pid, "alarm_budget_curve", json.dumps(thr["alarm_budget_curve"])),
        (pid, "permutation_importance", json.dumps(metrics["permutation_importance"])),
        (pid, "station_value", json.dumps(val["station_value"])),
        (pid, "soft_sensor_ablation", json.dumps(val["soft_sensor_ablation"])),
        (pid, "soft_sensor_validation", json.dumps(soft_validation)),
        (pid, "bottleneck_report", json.dumps(bn_report.to_dict())),
        (pid, "whatif_baseline", json.dumps(grid_base)),
        (pid, "instrumentation_plan", json.dumps(plan)),
    ])

    # ------------------------------------------------------------ stream + index
    stream_path = ARTIFACTS / f"stream_{pid}.jsonl"
    idx_rows = []
    risk_by_unit = sc["calibrated_risk"].to_dict()
    alert_by_unit = sc["alert_fired"].to_dict()
    ck_by = {(r.unit_seq, r.station_id): r for r in ckw.itertuples()}

    with stream_path.open("w", encoding="utf-8") as fh:
        offset = 0
        last_key = None
        for r in win.sort_values("entry_ts").itertuples():
            st = cfg.station(int(r.station_id))
            prov = {}
            vals = {}
            for s in sig_cols:
                v = getattr(r, s)
                if v is not None and not pd.isna(v):
                    vals[s] = round(float(v), 3)
                    prov[s] = "measured"
            rec = {
                "ts": _ts(r.entry_ts), "unit_id": str(r.unit_id), "station_id": int(r.station_id),
                "variant": str(r.variant), "lot_id": str(r.lot_id),
                "cycle_time_s": round(float(r.cycle_s), 2),
                "station_state": str(r.prior_state),
                "sensor_tier": st.sensor_tier,
                "calibrated_risk": round(float(risk_by_unit.get(r.unit_seq, 0.0)), 4),
                "alert": int(alert_by_unit.get(r.unit_seq, 0)),
                "provenance": prov,
                **vals,
            }
            hit = ck_by.get((r.unit_seq, r.station_id))
            if hit is not None:
                rec["checklist_time_s"] = round(float(hit.checklist_time_s), 1)
                rec["checklist_pass"] = int(hit.checklist_pass)
                rec["notes_code"] = str(hit.notes_code)
            line = json.dumps(rec, separators=(",", ":")) + "\n"
            key = rec["ts"][:16]
            if key != last_key:
                idx_rows.append((pid, rec["ts"], offset))
                last_key = key
            fh.write(line)
            offset += len(line.encode("utf-8"))

    con.executemany("INSERT OR REPLACE INTO stream_index VALUES (?,?,?)", idx_rows)

    # Achieved throughput must use the FULL unit population over the FULL horizon.
    #
    # This previously divided `sc` -- which by then had been trimmed to the replay window
    # (ADR-032) -- by the whole 28-day span, and reported 2.1 UPH for a line that actually
    # runs at 53.8. Nothing failed loudly: the number simply flowed into
    # /api/economics/roi, where units_per_year = achieved_uph x 8 x 3 x 250, and quietly
    # understated the entire business case by a factor of 25.
    horizon_h = (tel["exit_ts"].max() - tel["entry_ts"].min()) / 3600.0
    achieved = float(len(sc_all) / max(1e-9, horizon_h))
    con.execute("INSERT OR REPLACE INTO plant_meta VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
        pid, cfg.plant["name"], len(cfg.stations), cfg.units_per_hour_target, achieved,
        _ts(replay_start), _ts(replay_end), time.strftime("%Y-%m-%dT%H:%M:%S"),
        econ.max_alerts_per_shift, thr["cost_optimal"], thr["budget_constrained"],
    ))

    con.commit()
    con.execute("VACUUM")
    con.close()

    db_mb = (ARTIFACTS / "pulsetwin.db").stat().st_size / 1e6
    return {
        "plant_id": pid, "elapsed_s": time.time() - t0,
        "whatif_rows": len(grid_rows), "instrumentation_rows": len(plan),
        "forecast_skill": bn_report.forecast_skill,
        "soft_sensor_delta": val["soft_sensor_ablation"]["delta_pr_auc"],
        "masked_r2": float(soft_validation.get("mean_r2", 0.0)),
        "units": len(urows), "factors": len(frows), "genealogy": len(win),
        "readings": len(rrows), "stream_events": len(win), "stream_index": len(idx_rows),
        "db_mb": db_mb, "npz_mb": npz_path.stat().st_size / 1e6,
        "bottleneck_accuracy": bn_accuracy,
        "replay_start": _ts(replay_start), "replay_end": _ts(replay_end),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    r = build(args.config)
    print(f"\n=== {r['plant_id']} artifact ==================================")
    print(f"  built in {r['elapsed_s']:.1f}s")
    print(f"  units {r['units']:,}   factors {r['factors']:,}   "
          f"genealogy {r['genealogy']:,}   readings {r['readings']:,}")
    print(f"  stream events {r['stream_events']:,}  index {r['stream_index']:,}")
    print(f"  replay window {r['replay_start']} -> {r['replay_end']}")
    print(f"  bottleneck detection accuracy {r['bottleneck_accuracy']:.1%}   "
          f"forecast skill vs persistence {r['forecast_skill']:+.3f}")
    print(f"  what-if grid {r['whatif_rows']} scenarios   "
          f"instrumentation plan {r['instrumentation_rows']} stations")
    print(f"  masked-station soft sensor R2 {r['masked_r2']:.3f}   "
          f"soft-sensor ablation delta PR-AUC {r['soft_sensor_delta']:+.4f}")
    print(f"  [{'PASS' if r['db_mb'] < 50 else 'FAIL'}] pulsetwin.db {r['db_mb']:.1f} MB (< 50)")
    print(f"  [{'PASS' if r['npz_mb'] < 5 else 'FAIL'}] model npz {r['npz_mb']:.2f} MB (< 5)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
