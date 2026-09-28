"""Deterministic evidence for the Incident Commander (ADR-067).

Every number the commander reasons over is computed HERE, from recorded telemetry:

  * the incident WINDOW -- a live or MQTT-projected run's own events (`simulation_events`),
    the same table the watch, the outcome monitor and the SSE stream read;
  * the BASELINE -- the plant's committed replay stream (`artifacts/stream_<plant>.jsonl`),
    i.e. its recorded normal operation, summarised once per plant and cached.

A probe compares the two and returns Evidence: what it measured, its provenance, and a
signed weight per hypothesis key. Weights come from effect sizes with fixed, documented
rules (docs/AGENT_ARCHITECTURE.md, "Evidence weights") -- never from a model. A probe that
cannot measure (no sensor, too few events) returns MISSING evidence rather than nothing,
so the gap is visible and counts against coverage.

Import wall: numpy + stdlib only.
"""

from __future__ import annotations

import json
import math
import secrets
import threading
from collections import Counter, OrderedDict, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import numpy as np

from serve import db
from serve.agentic_config import C, CONFIG

STALL_STATES = ("down", "blocked", "starved")
# The signals the watch treats as equipment-condition evidence. Vibration is excluded for
# the reason configs/plant_*.yaml `watch:` records: its rolling deviation reaches ~60% on
# a normal run, so it cannot distinguish a fault from noise.
CONDITION_SIGNALS = ("torque_nm", "weld_current_a")
PSI_SIGNALS = ("torque_nm", "weld_current_a", "humidity_pct", "temperature_c",
               "film_thickness_um", "vibration_mm_s")


def drift_features(e: dict[str, Any], signals: dict[tuple, Any]) -> dict[str, float]:
    """Drift is measured on STATIONARY transforms of the model's inputs, not the raw
    signals: humidity and temperature follow the day's ambient cycle, so a 30-minute window
    of raw humidity always "drifts" against a 24-hour baseline (measured: PSI 5.4 on a
    normal run, the first time this ran). Excess over ambient, and each reading's deviation
    from its station/variant mean, are what actually shifts when an input goes wrong."""
    out: dict[str, float] = {}
    sid, var = int(e["station_id"]), str(e.get("variant") or "")
    for sig, name in (("torque_nm", "torque_dev_pct"), ("weld_current_a", "weld_dev_pct"),
                      ("film_thickness_um", "film_dev_pct")):
        if e.get(sig) is None:
            continue
        ref = signals.get((sid, sig, var)) or signals.get((sid, sig, "*"))
        if ref and ref["mean"]:
            out[name] = (float(e[sig]) / ref["mean"] - 1.0) * 100.0
    if e.get("humidity_pct") is not None and e.get("ambient_humidity_pct") is not None:
        out["humidity_excess"] = float(e["humidity_pct"]) - float(e["ambient_humidity_pct"])
    if e.get("temperature_c") is not None and e.get("ambient_temperature_c") is not None:
        out["temperature_excess"] = float(e["temperature_c"]) - float(e["ambient_temperature_c"])
    return out
WINDOW_MIN_EVENTS = 8
MAX_RUN_EVENTS = 20000


@dataclass
class Evidence:
    specialist: str
    probe: str
    station_id: int | None
    provenance: str            # measured | inferred | simulated | manual | missing
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    effects: dict[str, float] = field(default_factory=dict)
    observed_at: str | None = None
    evidence_id: str = field(default_factory=lambda: "EV-" + secrets.token_hex(5))

    @property
    def quality(self) -> float:
        return float(CONFIG["evidence_quality"][self.provenance])

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "quality": self.quality}


def w_from_z(z: float, per: float = 3.0, cap: float = 2.0) -> float:
    """Support weight from an effect size: |z|/3, capped at 2 log-odds."""
    return max(-cap, min(cap, z / per))


# ------------------------------------------------------------------------ topology
def topology(main_con: Any, plant_id: str) -> list[dict[str, Any]]:
    rows = db.query_all(main_con, "SELECT station_id, station_name, zone, sensor_tier, "
                                  "nominal_cycle_s, buffer_capacity, sensors, is_inspection, "
                                  "position FROM station_config WHERE plant_id=? "
                                  "ORDER BY position", (plant_id,))
    for i, r in enumerate(rows):
        r["sensors"] = json.loads(r["sensors"]) if isinstance(r["sensors"], str) else r["sensors"]
        r["index"] = i
        r["is_inspection"] = bool(r["is_inspection"])
    return rows


def neighbours(topo: list[dict[str, Any]], sid: int) -> tuple[int | None, int | None]:
    idx = next((t["index"] for t in topo if t["station_id"] == sid), None)
    if idx is None:
        return None, None
    up = topo[idx - 1]["station_id"] if idx > 0 else None
    down = topo[idx + 1]["station_id"] if idx + 1 < len(topo) else None
    return up, down


def scope_stations(topo: list[dict[str, Any]], sid: int | None, hops: int,
                   extra: list[int] | None = None) -> list[int]:
    """Stations a probe may read: within `hops` positions of the incident, plus any
    station the genealogy trace itself implicated (quality incidents)."""
    if sid is None:
        return [t["station_id"] for t in topo]
    idx = next((t["index"] for t in topo if t["station_id"] == sid), 0)
    near = [t["station_id"] for t in topo if abs(t["index"] - idx) <= hops]
    return sorted(set(near) | set(extra or []))


# ------------------------------------------------------------------------ baseline
def _stats(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    arr = np.asarray(values, float)
    return {"mean": float(arr.mean()), "sd": float(arr.std(ddof=1)) if len(arr) > 1 else 0.0,
            "n": int(len(arr))}


@lru_cache(maxsize=4)
def baseline(artifacts: str, plant_id: str) -> dict[str, Any]:
    """The plant's recorded normal operation, summarised once. Provenance: measured."""
    path = Path(artifacts) / f"stream_{plant_id}.jsonl"
    states: dict[int, Counter] = defaultdict(Counter)
    cycles: dict[int, list[float]] = defaultdict(list)
    signals: dict[tuple[int, str, str], list[float]] = defaultdict(list)
    checklist: dict[int, list[float]] = defaultdict(list)
    checklist_pass: dict[int, list[int]] = defaultdict(list)
    excess: dict[int, list[float]] = defaultdict(list)
    kept: list[dict[str, Any]] = []
    variants: Counter = Counter()
    downs: Counter = Counter()
    last_state: dict[int, str] = {}
    first_ts = last_ts = None
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            e = json.loads(line)
            sid = int(e["station_id"])
            first_ts = first_ts or e["ts"]
            last_ts = e["ts"]
            st = e.get("station_state")
            states[sid][st] += 1
            if st == "down" and last_state.get(sid) != "down":
                downs[sid] += 1
            last_state[sid] = st
            if e.get("cycle_time_s") is not None:
                cycles[sid].append(float(e["cycle_time_s"]))
            for sig in PSI_SIGNALS:
                if e.get(sig) is not None:
                    signals[(sid, sig, str(e.get("variant") or ""))].append(float(e[sig]))
                    signals[(sid, sig, "*")].append(float(e[sig]))
            kept.append(e)
            if e.get("checklist_time_s") is not None:
                checklist[sid].append(float(e["checklist_time_s"]))
                checklist_pass[sid].append(int(e.get("checklist_pass") or 0))
            if e.get("humidity_pct") is not None and e.get("ambient_humidity_pct") is not None:
                excess[sid].append(float(e["humidity_pct"]) - float(e["ambient_humidity_pct"]))
            if sid == 1:
                variants[str(e.get("variant"))] += 1
    span_h = max((datetime.fromisoformat(last_ts) - datetime.fromisoformat(first_ts)
                  ).total_seconds() / 3600.0, 1e-6) if first_ts else 1.0
    sig_stats = {k: _stats(v) for k, v in signals.items()}
    psi_values: dict[str, list[float]] = defaultdict(list)
    for e in kept:
        for name, v in drift_features(e, sig_stats).items():
            psi_values[name].append(v)
    psi = {}
    for sig, vals in psi_values.items():
        arr = np.asarray(vals, float)
        edges = np.unique(np.quantile(arr, np.linspace(0, 1, 11)))
        if len(edges) < 3:
            continue
        counts = np.histogram(arr, bins=edges)[0]
        psi[sig] = {"edges": edges.tolist(), "frac": (counts / counts.sum()).tolist()}
    # The NORMAL envelope: the largest PSI any 30-minute window of the recorded day shows
    # against the whole day. Several inputs are non-stationary on a healthy line (tool wear
    # drifts weld current; measured normal-window PSI up to 5.4), so a single global
    # threshold would call every window drifted. A window is judged against this envelope.
    # Two window sizes (15 and 30 simulated minutes): PSI is biased upward on small samples,
    # and a live window is often shorter than 30 minutes of line.
    full = max(int(len(kept) / max(span_h * 2.0, 1.0)), 60)
    envelope: dict[str, float] = {}
    for size in (full // 2, full):
        for start in range(0, max(len(kept) - size, 1), max(size // 2, 1)):
            feats: dict[str, list[float]] = defaultdict(list)
            for e in kept[start:start + size]:
                for name, v in drift_features(e, sig_stats).items():
                    feats[name].append(v)
            for name, vals in feats.items():
                if name in psi and len(vals) >= 30:
                    envelope[name] = max(envelope.get(name, 0.0), _psi(vals, psi[name]))
    total_v = sum(variants.values()) or 1
    return {
        "span_h": span_h,
        "states": {sid: dict(c) for sid, c in states.items()},
        "n": {sid: sum(c.values()) for sid, c in states.items()},
        "cycle": {sid: _stats(v) for sid, v in cycles.items()},
        "signals": sig_stats,
        "checklist": {sid: _stats(v) for sid, v in checklist.items()},
        "checklist_pass": {sid: float(np.mean(v)) for sid, v in checklist_pass.items() if v},
        "humidity_excess": {sid: _stats(v) for sid, v in excess.items()},
        "variants": {k: v / total_v for k, v in variants.items()},
        "down_episodes": dict(downs),
        "psi": psi,
        "psi_envelope": {k: round(v, 4) for k, v in envelope.items()},
    }


def defect_base_rate(main_con: Any, plant_id: str) -> float:
    row = db.query_one(main_con, "SELECT AVG(CASE WHEN actual_defect IS TRUE THEN 1.0 ELSE 0.0 END) AS r FROM units "
                                 "WHERE plant_id=?", (plant_id,))
    return float((row or {}).get("r") or 0.0)


# ------------------------------------------------------------------------ the window
_EVENTS: "OrderedDict[tuple[str, int], list[dict[str, Any]]]" = OrderedDict()
_EVENTS_LOCK = threading.Lock()


def run_events(ops: Any, run_id: str, upto_seq: int) -> list[dict[str, Any]]:
    """A run's events up to `upto_seq` (the newest MAX_RUN_EVENTS), oldest first. Cached:
    an investigation reads the same frozen window on every step."""
    key = (run_id, int(upto_seq))
    with _EVENTS_LOCK:
        if key in _EVENTS:
            _EVENTS.move_to_end(key)
            return _EVENTS[key]
    rows = db.query_all(ops, "SELECT seq, payload FROM simulation_events WHERE run_id=? "
                             "AND seq<=? ORDER BY seq DESC LIMIT ?",
                        (run_id, int(upto_seq), MAX_RUN_EVENTS))
    out = []
    for r in reversed(rows):
        p = r["payload"]
        p = json.loads(p) if isinstance(p, str) else dict(p)
        p["seq"] = r["seq"]
        out.append(p)
    with _EVENTS_LOCK:
        _EVENTS[key] = out
        while len(_EVENTS) > 16:
            _EVENTS.popitem(last=False)
    return out


def _ts(v: str) -> datetime:
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)


def in_window(events: list[dict[str, Any]], end_ts: str, seconds: float) -> list[dict[str, Any]]:
    end = _ts(end_ts)
    return [e for e in events if e.get("ts") and 0 <= (end - _ts(e["ts"])).total_seconds()
            <= seconds]


def station_events(events: list[dict[str, Any]], sid: int) -> list[dict[str, Any]]:
    return [e for e in events if e.get("event_type") in ("station_update", "alert")
            and e.get("station_id") is not None and int(e["station_id"]) == sid]


# ------------------------------------------------------------------------ context
@dataclass
class ProbeContext:
    plant_id: str
    incident: dict[str, Any]
    topo: list[dict[str, Any]]
    base: dict[str, Any]
    events: list[dict[str, Any]]           # the run up to the trigger
    window: list[dict[str, Any]]           # the evidence window
    ops: Any
    main_con: Any
    rules: dict[str, Any]                  # artifacts/watch_rules_<plant>.json
    defect_rate: float
    candidates: list[int] = field(default_factory=list)   # quality: traced stations
    model_available: bool = True

    def station(self, sid: int | None) -> dict[str, Any]:
        return next((t for t in self.topo if t["station_id"] == sid), {})

    def name(self, sid: int | None) -> str:
        s = self.station(sid)
        return f"S{sid} {s.get('station_name', '')}".strip()


def _share_z(k: int, n: int, p0_count: int, n0: int) -> tuple[float, float, float]:
    """(window share, baseline share, z) with Laplace-smoothed baseline."""
    p = k / n if n else 0.0
    p0 = (p0_count + 1) / (n0 + 2)
    z = (p - p0) / math.sqrt(p0 * (1 - p0) / max(n, 1))
    return p, p0, z


# ------------------------------------------------------------------------ probes
def _missing(specialist: str, probe: str, sid: int | None, why: str) -> Evidence:
    return Evidence(specialist, probe, sid, "missing", why, {"reason": why})


def probe_state_mix(ctx: ProbeContext, sid: int) -> list[Evidence]:
    ev = station_events(ctx.window, sid)
    if len(ev) < WINDOW_MIN_EVENTS:
        return [_missing("production", "state_mix", sid,
                         f"only {len(ev)} events at {ctx.name(sid)} in the window")]
    counts = Counter(e.get("station_state") for e in ev)
    b = ctx.base["states"].get(sid, {})
    n0 = ctx.base["n"].get(sid, 0)
    data: dict[str, Any] = {"events": len(ev), "baseline_events": n0}
    effects: dict[str, float] = {}
    parts, strong = [], 0.0
    for state, hyp in (("down", "local_degradation"), ("blocked", "downstream_congestion"),
                       ("starved", "upstream_starvation")):
        p, p0, z = _share_z(counts.get(state, 0), len(ev), b.get(state, 0), n0)
        data[f"{state}_share"] = round(p, 3)
        data[f"{state}_baseline_share"] = round(p0, 3)
        data[f"{state}_z"] = round(z, 2)
        if z >= 3 and p >= 0.15:
            effects[hyp] = effects.get(hyp, 0.0) + w_from_z(z)
            strong = max(strong, w_from_z(z))
            parts.append(f"{state} {p:.0%} (baseline {p0:.1%})")
        elif z < 1:
            effects[hyp] = effects.get(hyp, 0.0) - 0.6
    if counts.get("blocked", 0) / len(ev) >= 0.15:
        # A station that spends its time BLOCKED is waiting on the line after it; it is
        # not the slow one. Stated as its own rule so it can be argued with.
        effects["local_degradation"] = effects.get("local_degradation", 0.0) - 0.8
    # A normal state mix says "no THROUGHPUT fault"; about a suspected equipment or
    # procedure problem it says little (a rushed check does not stall a station).
    effects["no_fault"] = -strong if strong else (
        1.0 if ctx.incident.get("kind") == "throughput" else 0.4)
    summary = (f"{ctx.name(sid)}: " + (", ".join(parts) if parts else
               "state mix within recorded baseline (no excess down/blocked/starved time)"))
    return [Evidence("production", "state_mix", sid, "measured", summary, data, effects,
                     ev[-1]["ts"])]


def probe_cycle_time(ctx: ProbeContext, sid: int) -> list[Evidence]:
    ev = [e for e in station_events(ctx.window, sid) if e.get("cycle_time_s") is not None]
    ref = ctx.base["cycle"].get(sid)
    if len(ev) < WINDOW_MIN_EVENTS or not ref or ref["n"] < C["baseline_min_events"]:
        return [_missing("production", "cycle_time", sid,
                         f"too few cycle readings at {ctx.name(sid)} to compare")]
    vals = np.asarray([float(e["cycle_time_s"]) for e in ev])
    mean = float(vals.mean())
    pct = (mean / ref["mean"] - 1.0) * 100.0
    z = (mean - ref["mean"]) / max(ref["sd"] / math.sqrt(len(vals)), 1e-6)
    effects: dict[str, float] = {}
    if pct >= 10 and z >= 3:
        effects = {"no_fault": -1.0, "local_degradation": 0.5}
        summary = f"{ctx.name(sid)} mean cycle {mean:.1f}s, {pct:+.0f}% vs baseline {ref['mean']:.1f}s"
    elif abs(pct) < 3:
        effects = {"no_fault": 0.8, "local_degradation": -0.5}
        summary = f"{ctx.name(sid)} mean cycle {mean:.1f}s, within 3% of baseline"
    else:
        summary = f"{ctx.name(sid)} mean cycle {mean:.1f}s ({pct:+.1f}%), not decisive"
    return [Evidence("production", "cycle_time", sid, "measured", summary,
                     {"mean_cycle_s": round(mean, 2), "baseline_cycle_s": round(ref["mean"], 2),
                      "change_pct": round(pct, 1), "z": round(z, 2), "events": len(vals)},
                     effects, ev[-1]["ts"])]


def probe_neighbour_flow(ctx: ProbeContext, sid: int) -> list[Evidence]:
    up, down = neighbours(ctx.topo, sid)
    out = []
    for nb, hyp, role in ((down, "downstream_congestion", "downstream"),
                          (up, "upstream_starvation", "upstream")):
        if nb is None:
            continue
        ev = station_events(ctx.window, nb)
        if len(ev) < WINDOW_MIN_EVENTS:
            out.append(_missing("production", "neighbour_flow", nb,
                                f"{role} {ctx.name(nb)}: too few events"))
            continue
        counts = Counter(e.get("station_state") for e in ev)
        b = ctx.base["states"].get(nb, {})
        n0 = ctx.base["n"].get(nb, 0)
        p_down, p0, z_down = _share_z(counts.get("down", 0) + counts.get("blocked", 0),
                                      len(ev), b.get("down", 0) + b.get("blocked", 0), n0)
        ref = ctx.base["cycle"].get(nb)
        cyc = [float(e["cycle_time_s"]) for e in ev if e.get("cycle_time_s") is not None]
        pct = ((float(np.mean(cyc)) / ref["mean"] - 1) * 100) if ref and cyc else 0.0
        data = {"role": role, "stalled_share": round(p_down, 3),
                "stalled_baseline_share": round(p0, 3), "z": round(z_down, 2),
                "cycle_change_pct": round(pct, 1), "events": len(ev)}
        if z_down >= 3 and p_down >= 0.15:
            effects = {hyp: w_from_z(z_down)}
            summary = f"{role} {ctx.name(nb)} stalled {p_down:.0%} of the window"
        elif pct >= 10:
            effects = {hyp: 0.8}
            summary = f"{role} {ctx.name(nb)} cycling {pct:+.0f}% slower than baseline"
        else:
            effects = {hyp: -0.6} if role == "upstream" else {}
            summary = (f"{role} {ctx.name(nb)} operating normally"
                       + (" -- congestion, if any, is in the transfer between the two"
                          if role == "downstream" else ""))
        out.append(Evidence("production", "neighbour_flow", nb, "measured", summary, data,
                            effects, ev[-1]["ts"]))
    return out


def probe_variant_mix(ctx: ProbeContext, sid: int | None) -> list[Evidence]:
    units = {e.get("unit_id"): e.get("variant") for e in ctx.window
             if e.get("event_type") in ("station_update", "alert") and e.get("variant")}
    if len(units) < 30:
        return [_missing("production", "variant_mix", sid,
                         f"only {len(units)} units in the window; mix cannot be compared")]
    counts = Counter(units.values())
    n = len(units)
    shares = {k: v / n for k, v in counts.items()}
    base = ctx.base["variants"]
    tvd = 0.5 * sum(abs(shares.get(k, 0) - base.get(k, 0)) for k in set(shares) | set(base))
    # Pearson chi-square against the recorded mix; Wilson-Hilferty turns it into a z so the
    # rule has the same shape as every other probe's. A fixed distance threshold was tried
    # first: sampling noise alone crosses it at ~50 units.
    k = max(len(base) - 1, 1)
    chi2 = sum((counts.get(v, 0) - n * q) ** 2 / (n * q) for v, q in base.items() if q > 0)
    z = ((chi2 / k) ** (1 / 3) - (1 - 2 / (9 * k))) / math.sqrt(2 / (9 * k))
    if z >= 2.33 and tvd >= 0.1:
        effects = {"variant_mix": w_from_z(z)}
        summary = f"variant mix shifted: total variation {tvd:.2f} (chi-square z={z:.1f})"
    elif z < 1.28:
        effects = {"variant_mix": -1.0}
        summary = (f"variant mix consistent with the recorded mix (total variation {tvd:.2f}, "
                   f"z={z:.1f})")
    else:
        effects = {"variant_mix": -0.3}
        summary = f"variant mix marginally different (total variation {tvd:.2f}, z={z:.1f})"
    return [Evidence("production", "variant_mix", sid, "measured", summary,
                     {"units": n, "tvd": round(tvd, 3), "chi2_z": round(z, 2),
                      "window_shares": {k: round(v, 3) for k, v in shares.items()},
                      "baseline_shares": {k: round(v, 3) for k, v in base.items()}}, effects)]


def _signal_deviation(ctx: ProbeContext, ev: list[dict[str, Any]], sid: int,
                      sig: str) -> tuple[list[float], str]:
    """Per-event % deviation from the station's per-variant recorded mean."""
    devs, prov = [], "measured"
    for e in ev:
        if e.get(sig) is None:
            continue
        ref = ctx.base["signals"].get((sid, sig, str(e.get("variant") or ""))) or \
            ctx.base["signals"].get((sid, sig, "*"))
        if not ref or not ref["mean"]:
            continue
        devs.append((float(e[sig]) / ref["mean"] - 1.0) * 100.0)
        if (e.get("provenance") or {}).get(sig) == "inferred":
            prov = "inferred"
    return devs, prov


def probe_signal_health(ctx: ProbeContext, sid: int) -> list[Evidence]:
    st = ctx.station(sid)
    sigs = [s for s in (st.get("sensors") or []) if s in CONDITION_SIGNALS]
    if st.get("sensor_tier") == "manual" or not sigs:
        return [_missing("reliability", "signal_health", sid,
                         f"{ctx.name(sid)} has no torque/weld sensor: equipment condition "
                         "cannot be measured here")]
    thr = float(ctx.rules.get("tool_wear", {}).get("deviation_pct", 8.0))
    ev = station_events(ctx.window, sid)
    # On a quality incident the same reading is evidence for "defects introduced HERE".
    key = f"process_deviation@{sid}" if ctx.incident.get("kind") == "quality" else "local_degradation"
    out = []
    for sig in sigs:
        devs, prov = _signal_deviation(ctx, ev, sid, sig)
        if len(devs) < WINDOW_MIN_EVENTS:
            out.append(_missing("reliability", "signal_health", sid,
                                f"{sig} at {ctx.name(sid)}: {len(devs)} readings in window"))
            continue
        dev = float(np.mean(devs))
        if abs(dev) >= thr:
            effects = {key: min(2.0, abs(dev) / thr), "no_fault": -1.0}
            if key == "local_degradation":
                effects["process_deviation"] = -0.3
            summary = f"{sig} at {ctx.name(sid)} deviates {dev:+.1f}% from its per-variant baseline"
        elif abs(dev) < thr / 2:
            effects = {key: -1.0}
            summary = f"{sig} at {ctx.name(sid)} normal ({dev:+.1f}% vs baseline)"
        else:
            effects = {}
            summary = f"{sig} at {ctx.name(sid)} {dev:+.1f}% vs baseline: below the {thr:.0f}% threshold"
        out.append(Evidence("reliability", "signal_health", sid, prov, summary,
                            {"signal": sig, "deviation_pct": round(dev, 2),
                             "threshold_pct": thr, "readings": len(devs)}, effects,
                            ev[-1]["ts"] if ev else None))
    return out


def probe_failure_history(ctx: ProbeContext, sid: int) -> list[Evidence]:
    ev = station_events(ctx.events, sid)
    episodes, prev = 0, None
    for e in ev:
        st = e.get("station_state")
        if st == "down" and prev != "down":
            episodes += 1
        prev = st
    run_h = 0.0
    if ev:
        run_h = max((_ts(ev[-1]["ts"]) - _ts(ev[0]["ts"])).total_seconds() / 3600.0, 1e-6)
    rate = (ctx.base["down_episodes"].get(sid, 0) + 1) / ctx.base["span_h"]
    expected = rate * run_h
    prior = db.query_all(ctx.ops, "SELECT root_cause FROM incident_cases WHERE plant_id=? "
                                  "AND station_id=? AND root_cause='local_degradation'",
                         (ctx.plant_id, sid))
    effects: dict[str, float] = {}
    if episodes >= expected + 2 and episodes >= 2 * max(expected, 0.5):
        effects["local_degradation"] = 0.8
    if prior:
        effects["local_degradation"] = effects.get("local_degradation", 0.0) + min(0.9, 0.3 * len(prior))
    summary = (f"{ctx.name(sid)}: {episodes} breakdown episode(s) this run vs "
               f"{expected:.2f} expected from the recorded rate; {len(prior)} prior "
               "degradation case(s) here")
    return [Evidence("reliability", "failure_history", sid, "measured", summary,
                     {"episodes_this_run": episodes, "expected_episodes": round(expected, 3),
                      "failure_rate_per_h": round(rate, 5), "prior_degradation_cases": len(prior),
                      "rate_basis": "recorded down episodes + 1 (Laplace) over the replay span"},
                     effects)]


def sensor_completeness(ctx: ProbeContext, sid: int) -> dict[str, Any]:
    st = ctx.station(sid)
    sensors = st.get("sensors") or []
    ev = station_events(ctx.window, sid)
    if st.get("sensor_tier") == "manual":
        return {"tier": "manual", "events": len(ev), "completeness": None, "dropouts": 0,
                "inferred_share": None}
    if not ev or not sensors:
        return {"tier": st.get("sensor_tier"), "events": len(ev), "completeness": None,
                "dropouts": 0, "inferred_share": None}
    present = sum(1 for e in ev for s in sensors if e.get(s) is not None)
    inferred = sum(1 for e in ev for s in sensors
                   if (e.get("provenance") or {}).get(s) == "inferred")
    dropouts = sum(1 for e in ev if e.get("sensor_status") == "dropout")
    return {"tier": st.get("sensor_tier"), "events": len(ev),
            "completeness": present / (len(ev) * len(sensors)),
            "dropouts": dropouts, "inferred_share": inferred / max(present, 1),
            "sensors": sensors}


def maturity(ctx: ProbeContext, sid: int) -> str:
    """HIGH / MEDIUM / LOW / MANUAL_ONLY / UNKNOWN -- the most this station's sensing can
    support (docs/AGENT_ARCHITECTURE.md, "Sensor maturity")."""
    c = sensor_completeness(ctx, sid)
    if c["tier"] == "manual":
        return "MANUAL_ONLY"
    if c["completeness"] is None:
        return "UNKNOWN"
    if c["completeness"] < float(CONFIG["data_quality"]["sensor_unreliable_below"]):
        return "LOW"
    if c["completeness"] >= 0.95 and c["inferred_share"] < 0.2 and len(c["sensors"]) >= 2:
        return "HIGH"
    return "MEDIUM"


def probe_sensor_health(ctx: ProbeContext, sid: int) -> list[Evidence]:
    c = sensor_completeness(ctx, sid)
    # sensor_health is a CHANGE LOG: only each sensor's latest row says whether it is dark
    # now. Reading raw recent rows counted days-old "degraded" transitions on the Docker
    # stack and pushed a healthy station's measurement hypothesis to 24%. One definition:
    from serve.routers.system import _active_dropouts
    dark = [r["sensor_type"] for r in _active_dropouts(ctx.ops, ctx.plant_id)
            if int(r["station_id"]) == sid]
    data = {**{k: (round(v, 3) if isinstance(v, float) else v) for k, v in c.items()},
            "registry_dark_sensors": dark, "maturity": maturity(ctx, sid)}
    if c["tier"] == "manual":
        return [Evidence("data_quality", "sensor_health", sid, "missing",
                         f"{ctx.name(sid)} is a manual station: no sensors, checklist records only",
                         data, {"sensor_fault": -0.5})]
    if c["completeness"] is None:
        return [_missing("data_quality", "sensor_health", sid,
                         f"no telemetry from {ctx.name(sid)} in the window")]
    unreliable = c["completeness"] < float(CONFIG["data_quality"]["sensor_unreliable_below"])
    if unreliable or c["dropouts"] or dark:
        effects = {"sensor_fault": 2.0 if unreliable else 1.0}
        summary = (f"{ctx.name(sid)} sensor completeness {c['completeness']:.0%}, "
                   f"{c['dropouts']} dropout event(s)")
    else:
        effects = {"sensor_fault": -1.5}
        summary = f"{ctx.name(sid)} sensors healthy: {c['completeness']:.0%} of readings present"
    return [Evidence("data_quality", "sensor_health", sid, "measured", summary, data, effects)]


def _psi(values: list[float], ref: dict[str, Any]) -> float:
    return psi(values, ref)


def psi(values: list[float], ref: dict[str, Any]) -> float:
    edges = np.asarray(ref["edges"])
    frac0 = np.asarray(ref["frac"])
    arr = np.clip(np.asarray(values, float), edges[0], edges[-1])
    frac = np.histogram(arr, bins=edges)[0] / max(len(arr), 1)
    a, b = np.maximum(frac, 1e-4), np.maximum(frac0, 1e-4)
    return float(np.sum((a - b) * np.log(a / b)))


def model_health(ctx: ProbeContext) -> dict[str, Any]:
    """Window PSI per signal vs the recorded baseline, plus any logged drift alert and
    whether the plant's scorer is loaded. Governance input: it moves no hypothesis."""
    feats: dict[str, list[float]] = defaultdict(list)
    for e in ctx.window:
        if e.get("station_id") is not None and e.get("event_type") in ("station_update", "alert"):
            for name, v in drift_features(e, ctx.base["signals"]).items():
                feats[name].append(v)
    scores = {}
    for sig, ref in ctx.base["psi"].items():
        if len(feats.get(sig, [])) >= 30:
            scores[sig] = round(psi(feats[sig], ref), 3)
    alerts = db.query_all(ctx.ops, "SELECT feature_name, value, threshold FROM drift_metrics "
                                   "WHERE plant_id=? AND alert_triggered IS TRUE "
                                   "ORDER BY id DESC LIMIT 5", (ctx.plant_id,))
    mh = CONFIG["model_health"]
    env = ctx.base.get("psi_envelope", {})
    block = {k: max(mh["psi_block"], 1.5 * env.get(k, 0.0)) for k in scores}
    warn = {k: max(mh["psi_warn"], env.get(k, 0.0)) for k in scores}
    worst = max(scores.values(), default=0.0)
    blocked = sorted(k for k, v in scores.items() if v >= block[k])
    drifting = sorted(k for k, v in scores.items() if v >= warn[k])
    status = ("degraded" if blocked or not ctx.model_available
              else "warn" if drifting or alerts else "healthy")
    return {"status": status, "max_psi": worst, "psi": scores, "blocked": blocked,
            "block_threshold": {k: round(v, 3) for k, v in block.items()},
            "drifting": drifting,
            "logged_drift_alerts": len(alerts), "model_available": ctx.model_available}


def probe_model_health(ctx: ProbeContext, sid: int | None) -> list[Evidence]:
    mh = model_health(ctx)
    summary = (f"model inputs {mh['status']}: max PSI {mh['max_psi']:.2f}"
               + (f"; beyond the recorded normal envelope on {', '.join(mh['drifting'])}"
                  if mh["drifting"] else "; within the recorded normal envelope"))
    return [Evidence("data_quality", "model_health", sid, "measured", summary, mh, {})]


def probe_zone_climate(ctx: ProbeContext, sid: int | None) -> list[Evidence]:
    paint = set(int(s) for s in (ctx.rules.get("paint_env") or {}).get("stations", []))
    ev = [e for e in ctx.window if e.get("station_id") is not None
          and int(e["station_id"]) in paint and e.get("humidity_pct") is not None
          and e.get("ambient_humidity_pct") is not None]
    if len(ev) < WINDOW_MIN_EVENTS:
        return [_missing("quality", "zone_climate", sid, "too few paint-zone humidity readings")]
    exc = [float(e["humidity_pct"]) - float(e["ambient_humidity_pct"]) for e in ev]
    zone = float(np.mean([float(e["humidity_pct"]) for e in ev]))
    refs = [ctx.base["humidity_excess"].get(s) for s in paint if ctx.base["humidity_excess"].get(s)]
    ref_mean = float(np.mean([r["mean"] for r in refs])) if refs else 0.0
    ref_sd = float(np.mean([r["sd"] for r in refs])) if refs else 1.0
    m = float(np.mean(exc))
    z = (m - ref_mean) / max(ref_sd / math.sqrt(len(exc)), 1e-6)
    thr = float(ctx.rules["paint_env"]["humidity_excess_pct"])
    if m >= thr and z >= 3:
        effects = {"environment_control": w_from_z(z), "ambient_driven": -1.0, "no_fault": -1.0}
        summary = f"paint-zone humidity {m:+.1f} points above ambient (baseline {ref_mean:+.1f})"
    elif zone > float(ctx.rules["paint_env"]["humidity_threshold_pct"]):
        effects = {"ambient_driven": 1.5, "environment_control": -1.0}
        summary = f"zone humidity {zone:.1f}% is high but tracks ambient (excess {m:+.1f})"
    else:
        effects = {"no_fault": 1.0, "environment_control": -1.0}
        summary = f"paint-zone climate normal (humidity {zone:.1f}%, excess {m:+.1f})"
    return [Evidence("quality", "zone_climate", sid, "measured", summary,
                     {"zone_humidity_pct": round(zone, 2), "excess_over_ambient": round(m, 2),
                      "baseline_excess": round(ref_mean, 2), "z": round(z, 2),
                      "readings": len(ev)}, effects, ev[-1]["ts"])]


def probe_manual_checklist(ctx: ProbeContext, sid: int) -> list[Evidence]:
    ev = [e for e in station_events(ctx.window, sid) if e.get("checklist_time_s") is not None]
    ref = ctx.base["checklist"].get(sid)
    key = f"process_deviation@{sid}" if ctx.incident["kind"] == "quality" else "process_deviation"
    if len(ev) < WINDOW_MIN_EVENTS or not ref:
        return [_missing("quality", "manual_checklist", sid,
                         f"no manual checklist records at {ctx.name(sid)} to compare")]
    t = float(np.mean([float(e["checklist_time_s"]) for e in ev]))
    pct = (t / ref["mean"] - 1) * 100
    z = (t - ref["mean"]) / max(ref["sd"] / math.sqrt(len(ev)), 1e-6)
    pass_rate = float(np.mean([int(e.get("checklist_pass") or 0) for e in ev]))
    notes = Counter(e.get("notes_code") for e in ev)
    if pct <= -20 and z <= -3:
        effects = {key: w_from_z(-z), "no_fault": -1.0}
        summary = (f"checklists at {ctx.name(sid)} completed {-pct:.0f}% faster than the "
                   "recorded norm: a rushed procedure")
    elif abs(pct) < 10:
        effects = {key: -0.8}
        summary = f"checklist times at {ctx.name(sid)} normal ({pct:+.0f}%)"
    else:
        effects = {}
        summary = f"checklist times at {ctx.name(sid)} {pct:+.0f}% vs norm, not decisive"
    # Operator identity never leaves this function: only counts and shares.
    return [Evidence("quality", "manual_checklist", sid, "manual", summary,
                     {"mean_checklist_s": round(t, 1), "baseline_checklist_s": round(ref["mean"], 1),
                      "change_pct": round(pct, 1), "z": round(z, 2), "records": len(ev),
                      "pass_rate": round(pass_rate, 3),
                      "baseline_pass_rate": round(ctx.base["checklist_pass"].get(sid, 0.0), 3),
                      "notes_codes": dict(notes)}, effects, ev[-1]["ts"])]


# ------------------------------------------------------------------------ quality trace
def _inspections(ctx: ProbeContext) -> list[dict[str, Any]]:
    return [e for e in ctx.events if e.get("event_type") == "inspection_outcome"]


def probe_defect_rate(ctx: ProbeContext, sid: int | None) -> list[Evidence]:
    ins = in_window(_inspections(ctx), ctx.incident["trigger_ts"], C["evidence_window_s"] * 2)
    if len(ins) < 10:
        return [_missing("quality", "defect_rate", sid, f"only {len(ins)} inspections in window")]
    k = sum(int(e.get("actual_defect") or 0) for e in ins)
    p = k / len(ins)
    p0 = max(ctx.defect_rate, 1e-3)
    z = (p - p0) / math.sqrt(p0 * (1 - p0) / len(ins))
    effects = {"no_fault": -w_from_z(z)} if z >= 3 else ({"no_fault": 1.0} if z < 1 else {})
    return [Evidence("quality", "defect_rate", sid, "measured",
                     f"{k} defects in {len(ins)} inspections ({p:.1%}) vs recorded rate {p0:.1%}",
                     {"defects": k, "inspected": len(ins), "rate": round(p, 4),
                      "baseline_rate": round(p0, 4), "z": round(z, 2)}, effects,
                     ins[-1]["ts"])]


def _unit_station_values(ctx: ProbeContext, sid: int) -> dict[str, tuple[float, str]]:
    """unit -> (SIGNED % deviation of its reading at `sid` from the recorded mean, ts).
    Sensors where the station has them; the checklist time at a manual station (a weaker,
    manual record). Signed, and compared defective-vs-good at the SAME station and period:
    a station's own recorded offset (tool wear drifting through the day) is shared by both
    groups and cancels -- measured: an absolute deviation hid a +15% fault under S3's
    natural -13% torque deficit."""
    st = ctx.station(sid)
    sigs = [s for s in (st.get("sensors") or []) if s in CONDITION_SIGNALS]
    ref_ck = ctx.base["checklist"].get(sid)
    out = {}
    for e in station_events(ctx.events, sid):
        uid = e.get("unit_id")
        vals = []
        for sig in sigs:
            devs, _ = _signal_deviation(ctx, [e], sid, sig)
            vals.extend(devs)
        if not sigs and ref_ck and e.get("checklist_time_s") is not None:
            vals.append((float(e["checklist_time_s"]) / ref_ck["mean"] - 1) * 100)
        if vals:
            out[uid] = (float(np.mean(vals)), e["ts"])
    return out


def _contrast(ctx: ProbeContext, sid: int, bad: set[str], good: set[str]) -> dict[str, Any] | None:
    vals = _unit_station_values(ctx, sid)
    b = [vals[u][0] for u in bad if u in vals]
    g = [vals[u][0] for u in good if u in vals]
    if len(b) < 2 or len(g) < 5:
        return None
    mb, mg = float(np.mean(b)), float(np.mean(g))
    se = math.sqrt(np.var(b, ddof=1) / len(b) + np.var(g, ddof=1) / len(g)) or 1e-6
    return {"station_id": sid, "defective_dev_pct": round(mb, 2), "good_dev_pct": round(mg, 2),
            "difference_pct": round(mb - mg, 2), "good_median_pct": round(float(np.median(g)), 2),
            "t": round(abs(mb - mg) / se, 2), "n_defective": len(b), "n_good": len(g),
            "manual": ctx.station(sid).get("sensor_tier") == "manual"}


def inspected_units(ctx: ProbeContext) -> tuple[set[str], set[str], list[dict[str, Any]]]:
    """Cases and controls. CASES: units found defective in the recent window. CONTROLS:
    every unit inspected good so far in the run (historical controls). Controls drawn
    from the same 30 minutes cannot separate a fault that touches every unit through a
    station -- they all carry it (measured: +12% at the faulty station in both groups)."""
    every = _inspections(ctx)
    ins = in_window(every, ctx.incident["trigger_ts"], C["evidence_window_s"] * 2)
    bad = {e["unit_id"] for e in ins if int(e.get("actual_defect") or 0)}
    good = {e["unit_id"] for e in every if not int(e.get("actual_defect") or 0)} - bad
    return bad, good, ins


def _threshold(ctx: ProbeContext, sid: int) -> float:
    manual = ctx.station(sid).get("sensor_tier") == "manual"
    return 20.0 if manual else float(ctx.rules.get("tool_wear", {}).get("deviation_pct", 8.0))


def exposure_split(ctx: ProbeContext, sid: int) -> dict[str, Any] | None:
    """The genealogy test: did units that passed `sid` AFTER its readings shifted fail at
    the gate more often than units that passed it before?

    Onset = the first run of 8 consecutive readings at the station whose mean deviates
    from the recorded baseline by the plant's threshold. A shift present from the very
    first readings has no "before" to compare with -- it is reported as such and cannot
    support a link to a defect burst that began later. Two-proportion z on the gate's
    verdicts, split by each unit's passage time at the station."""
    vals = _unit_station_values(ctx, sid)
    if len(vals) < 16:
        return None
    thr = _threshold(ctx, sid)
    series = sorted((ts, u, d) for u, (d, ts) in vals.items())
    onset = None
    for i in range(len(series) - 7):
        if abs(float(np.mean([d for _, _, d in series[i:i + 8]]))) >= thr:
            onset = series[i][0]
            break
    verdict = {e["unit_id"]: int(e.get("actual_defect") or 0) for e in _inspections(ctx)}
    if onset is None:
        return {"station_id": sid, "onset_ts": None, "since_run_start": False, "z": 0.0,
                "reason": "readings never left the recorded baseline"}
    since_start = onset == series[0][0]
    before = [verdict[u] for ts, u, _ in series if ts < onset and u in verdict]
    after = [verdict[u] for ts, u, _ in series if ts >= onset and u in verdict]
    out = {"station_id": sid, "onset_ts": onset, "since_run_start": since_start,
           "n_before": len(before), "n_after": len(after),
           "defect_rate_before": round(float(np.mean(before)), 4) if before else None,
           "defect_rate_after": round(float(np.mean(after)), 4) if after else None, "z": 0.0}
    if since_start or len(before) < 5 or len(after) < 5:
        out["reason"] = ("shift present since the run began: not temporally linked to this burst"
                         if since_start else "too few inspected units on one side of the onset")
        return out
    p1, p0 = float(np.mean(after)), float(np.mean(before))
    pooled = (sum(after) + sum(before)) / (len(after) + len(before))
    se = math.sqrt(max(pooled * (1 - pooled), 1e-6) * (1 / len(after) + 1 / len(before)))
    out["z"] = round((p1 - p0) / se, 2)
    return out


def discover_candidates(ctx: ProbeContext, limit: int = 3) -> list[dict[str, Any]]:
    """Stations the defective units passed, ranked by the stronger of two genealogy tests:
    the case-control contrast (defective vs good units' readings at the station) and the
    exposure split (defect rate after vs before the station's own shift)."""
    bad, good, _ = inspected_units(ctx)
    if not bad:
        return []
    ranked = []
    for t in ctx.topo:
        if t["is_inspection"]:
            continue
        c = _contrast(ctx, t["station_id"], bad, good)
        split = exposure_split(ctx, t["station_id"])
        if c or split:
            score = max((c or {}).get("t", 0.0), (split or {}).get("z", 0.0))
            ranked.append({**(c or {"station_id": t["station_id"],
                                     "manual": t["sensor_tier"] == "manual"}),
                           "t": round(score, 2), "split_z": (split or {}).get("z")})
    ranked.sort(key=lambda c: -c["t"])
    top = [c for c in ranked if c["t"] > 1.5][:limit]
    return top or ranked[:1]


def probe_defect_trace(ctx: ProbeContext, sid: int) -> list[Evidence]:
    """Backward trace for one candidate: exposure split, case-control contrast, onset,
    affected window and the in-flight units that passed the station inside it."""
    bad, good, ins = inspected_units(ctx)
    key = f"process_deviation@{sid}"
    c = _contrast(ctx, sid, bad, good)
    split = exposure_split(ctx, sid)
    if c is None and split is None:
        return [_missing("quality", "defect_trace", sid,
                         f"not enough traced units through {ctx.name(sid)}")]
    thr = _threshold(ctx, sid)
    vals = _unit_station_values(ctx, sid)
    onset = (split or {}).get("onset_ts")
    deviating = sorted((ts, u) for u, (d, ts) in vals.items() if abs(d) >= thr
                       and (onset is None or ts >= onset))
    last = deviating[-1][0] if deviating else None
    bad_entries = sorted(vals[u][1] for u in bad if u in vals)
    first_defect = min((e["ts"] for e in ins if int(e.get("actual_defect") or 0)), default=None)
    inspected = {e["unit_id"] for e in _inspections(ctx)}
    affected = sorted(u for _, u in deviating if u not in inspected)
    z = (split or {}).get("z", 0.0)
    t = (c or {}).get("t", 0.0)
    material = abs((c or {}).get("difference_pct", 0.0)) >= thr / 2
    # The candidates were CHOSEN as the strongest of ~30 stations, so a statistic of 2-3
    # is what selection alone produces: it moves nothing. Support needs z or t >= 3
    # (and, for the contrast, a material difference); a shift present since the run began
    # cannot explain a burst that began later.
    if split and split.get("since_run_start"):
        effects = {key: -0.8}
    elif z >= 3 or (t >= 3 and material):
        effects = {key: w_from_z(max(z, t)), "no_fault": -1.0}
    elif max(z, t) < 2:
        effects = {key: -0.8}
    else:
        effects = {}
    prov = "manual" if ctx.station(sid).get("sensor_tier") == "manual" else "measured"
    parts = []
    if split and split.get("defect_rate_after") is not None and split.get("defect_rate_before") is not None:
        parts.append(f"units through {ctx.name(sid)} after its shift at {str(onset)[11:19]} failed "
                     f"{split['defect_rate_after']:.0%} vs {split['defect_rate_before']:.0%} before "
                     f"(z={z:.1f})")
    elif split and split.get("reason"):
        parts.append(f"{ctx.name(sid)}: {split['reason']}")
    if c:
        parts.append(f"recent defective units read {c['difference_pct']:+.1f}% vs good units (t={t:.1f})")
    data = {**(c or {}), "exposure_split": split, "threshold_pct": thr,
            "deviation_onset_ts": onset, "deviation_last_ts": last,
            "first_defective_unit_entry_ts": bad_entries[0] if bad_entries else None,
            "first_defect_observed_ts": first_defect,
            "affected_window": [onset, last] if onset and last else None,
            "potentially_affected_units": affected[:50],
            "potentially_affected_count": len(affected), "t": max(z, t)}
    return [Evidence("quality", "defect_trace", sid, prov, "; ".join(parts), data, effects, last)]


def probe_lot_concentration(ctx: ProbeContext, sid: int | None) -> list[Evidence]:
    bad, good, ins = inspected_units(ctx)
    lots = {e.get("unit_id"): e.get("lot_id") for e in ctx.events if e.get("lot_id")}
    bad_lots = Counter(lots.get(u) for u in bad if lots.get(u))
    all_lots = Counter(lots.get(u) for u in bad | good if lots.get(u))
    if len(bad) < 2 or not bad_lots:
        return [_missing("quality", "lot_concentration", sid, "too few defects to compare lots")]
    lot, k = bad_lots.most_common(1)[0]
    share_bad = k / sum(bad_lots.values())
    share_all = all_lots[lot] / max(sum(all_lots.values()), 1)
    if share_bad >= 0.6 and share_all < 0.4:
        effects = {"material_lot": 1.5}
        summary = f"{share_bad:.0%} of defects come from one lot that is {share_all:.0%} of output"
    else:
        effects = {"material_lot": -1.0}
        summary = f"defects spread across lots (largest lot {share_bad:.0%} of defects, {share_all:.0%} of output)"
    return [Evidence("quality", "lot_concentration", sid, "measured", summary,
                     {"top_lot_share_of_defects": round(share_bad, 3),
                      "top_lot_share_of_output": round(share_all, 3), "lots": len(all_lots)},
                     effects)]


# ------------------------------------------------------------------------ memory
def probe_similar_cases(ctx: ProbeContext, sid: int | None,
                        hypothesis_keys: list[str]) -> list[Evidence]:
    from serve import case_memory  # local: case_memory imports this module
    matches = case_memory.similar(ctx.ops, ctx.main_con, ctx.incident, ctx.topo, limit=3)
    if not matches:
        return [Evidence("memory", "similar_cases", sid, "missing",
                         "no comparable resolved case in operational memory", {"matches": []})]
    effects: dict[str, float] = {}
    for m in matches:
        root = m.get("root_cause")
        if root in hypothesis_keys and m.get("outcome_status") in ("successful", "partially_successful"):
            effects[root] = effects.get(root, 0.0) + round(0.6 * m["similarity"], 3)
    top = matches[0]
    summary = (f"most similar case {top['case_id']} ({top['plant_id']} {top.get('station_label')}), "
               f"similarity {top['similarity']:.0%}: root cause {top.get('root_cause') or 'unresolved'}")
    return [Evidence("memory", "similar_cases", sid, "inferred", summary, {"matches": matches},
                     effects)]


# ------------------------------------------------------------------------ registry
@dataclass(frozen=True)
class ProbeSpec:
    specialist: str
    label: str
    kinds: tuple[str, ...]
    # The largest |weight| this probe can put on each hypothesis key: what the planner's
    # expected-information-gain estimate reads. `@` means "the candidate's own key".
    informs: dict[str, float]
    target: str   # incident | candidates | plant
    fn: Callable[..., list[Evidence]]


ALL = ("throughput", "equipment", "quality", "environment")
PROBES: dict[str, ProbeSpec] = {
    "state_mix": ProbeSpec("production", "Station state mix vs recorded baseline",
                           ("throughput", "equipment"),
                           {"local_degradation": 2, "downstream_congestion": 2,
                            "upstream_starvation": 2, "no_fault": 2}, "incident", probe_state_mix),
    "cycle_time": ProbeSpec("production", "Cycle-time change vs baseline",
                            ("throughput", "equipment"),
                            {"no_fault": 1, "local_degradation": 0.5}, "incident", probe_cycle_time),
    "neighbour_flow": ProbeSpec("production", "Upstream/downstream station flow",
                                ("throughput",),
                                {"downstream_congestion": 2, "upstream_starvation": 2},
                                "incident", probe_neighbour_flow),
    "variant_mix": ProbeSpec("production", "Variant mix vs recorded mix",
                             ("throughput", "equipment"), {"variant_mix": 2}, "plant",
                             probe_variant_mix),
    "signal_health": ProbeSpec("reliability", "Torque / weld-current condition",
                               ("throughput", "equipment"),
                               {"local_degradation": 2, "no_fault": 1, "@": 2}, "incident",
                               probe_signal_health),
    "failure_history": ProbeSpec("reliability", "Breakdown history and prior cases",
                                 ("throughput", "equipment"), {"local_degradation": 1.5},
                                 "incident", probe_failure_history),
    "sensor_health": ProbeSpec("data_quality", "Sensor completeness and dropouts", ALL,
                               {"sensor_fault": 2}, "incident", probe_sensor_health),
    "model_health": ProbeSpec("data_quality", "Model-input drift (PSI)", ALL, {}, "plant",
                              probe_model_health),
    "zone_climate": ProbeSpec("quality", "Paint-zone humidity vs ambient", ("environment",),
                              {"environment_control": 2, "ambient_driven": 1.5, "no_fault": 1},
                              "plant", probe_zone_climate),
    "manual_checklist": ProbeSpec("quality", "Manual checklist records",
                                  ("equipment", "quality"),
                                  {"process_deviation": 2, "@": 2}, "candidates",
                                  probe_manual_checklist),
    "defect_rate": ProbeSpec("quality", "Inspection defect rate", ("quality",),
                             {"no_fault": 2}, "plant", probe_defect_rate),
    "defect_trace": ProbeSpec("quality", "Genealogy trace of defective units", ("quality",),
                              {"@": 2, "no_fault": 1}, "candidates", probe_defect_trace),
    "lot_concentration": ProbeSpec("quality", "Material-lot concentration", ("quality",),
                                   {"material_lot": 1.5}, "plant", probe_lot_concentration),
    "similar_cases": ProbeSpec("memory", "Similar resolved cases", ALL, {"*": 0.6}, "plant",
                               probe_similar_cases),
}
SPECIALISTS = ("reliability", "production", "quality", "data_quality", "memory")


def available(ctx: ProbeContext, probe: str, sid: int | None) -> float:
    """0..1: how measurable this probe is at this station -- the planner's expectation of
    evidence quality before running it. A torque probe at a manual station is 0."""
    st = ctx.station(sid) if sid is not None else {}
    if probe == "signal_health":
        return 0.0 if st.get("sensor_tier") == "manual" or not any(
            s in CONDITION_SIGNALS for s in st.get("sensors") or []) else 1.0
    if probe == "manual_checklist":
        return CONFIG["evidence_quality"]["manual"] if st.get("sensor_tier") == "manual" else 0.0
    if probe == "sensor_health" and st.get("sensor_tier") == "manual":
        return 0.2
    return 1.0
