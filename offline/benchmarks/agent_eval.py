"""Does the proactive watch beat simple threshold alerting? (ADR-056)

Replays injected faults on the PHYSICAL twin (`offline.live_twin`) -- not the committed
replay stream the watch thresholds were profiled on, which would grade the watch on its
own training data -- and feeds every station event, identically, to three alerters:

  watch       serve/watch.py::LineWatch -- 8-event window, latch, per-key cooldown, and
              (ADR-057) silence: ticked with the twin's clock every CHUNK_S, because a
              stopped line emits no event to carry time forward
  threshold   the SAME thresholds on each single event, every crossing an alarm. This is
              "simple threshold alerting" as a plant historian ships it.
  deadband    threshold + the watch's own per-key cooldown. Isolates what the WINDOW buys,
              so the watch cannot win on alarm volume alone.

Ground truth is the injected incident: its scenario, station and onset time. Per system:
  recall            share of fault runs with a correct alert inside the horizon
  detection delay   first correct alert minus onset (sim minutes)
  false alarms      alerts that match no injected fault, per 8 h shift -- on clean runs
                    and in the pre-onset segment of fault runs
  placebo recall    how often the SAME alerter "detects" the fault on the same-seed CLEAN
                    run, where there is none -- detection above this is detection, below
                    it is chance hits on routine blocked/starved events
  impact            units lost in the horizon vs the same-seed clean run (both are forks
                    of one warmed engine, so the difference is the fault); recall is also
                    reported on IMPACTFUL faults only (loss >= IMPACT_PCT)
  root cause        the scenario the alert's evidence implies (offline/counterfactual.py
                    ::hypothesis, what the agent tier then investigates) vs the injected one

Not measured, and why (stated in the report rather than silently skipped):
  * containment precision/recall and defect escape vs EOL-only: the twin's single-station
    incidents are defect-inert (ADR-055 -- Mode C needs S3 torque AND S8 vibration), so an
    escape-rate comparison would be comparing zeros.
  * the LLM agent's diagnosis: it costs 7-12 provider requests per run against a 50/day
    free quota; the deterministic diagnosis measured here is what the agent is verified
    against.

    python -m offline.benchmarks.agent_eval --plant plant_a --seeds 3
"""

from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from offline.benchmarks.run_bottleneck_benchmark import git_sha
from offline.benchmarks.simrun import with_horizon
from offline.config_schema import load_plant_config
from offline.counterfactual import MAX_CONGESTION, hypothesis
from offline.live_twin.engine import LiveTwinEngine
from offline.live_twin.incidents import Incident
from serve.watch import (PAINT_ENV, STALLED_STATES, STARVATION, TOOL_WEAR, LineWatch, _epoch,
                         load_rules)

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts"
SYSTEMS = ("watch", "threshold", "deadband")
SHIFT_H = 8.0
PRE_S = 2 * 3600.0   # clean segment before onset: false alarms on a line that is fine
POST_S = 3 * 3600.0  # detection horizon after onset
SEVERITY = 0.8
CHUNK_S = 60.0    # how often the eval ticks the watch's clock
IMPACT_PCT = 5.0  # a fault costing under 5% of the horizon's output is a minor event
DETECTOR_FOR = {"torque_degradation": TOOL_WEAR, "high_humidity": PAINT_ENV,
                "station_breakdown": STARVATION, "buffer_congestion": STARVATION}


@dataclass
class Alert:
    detector: str
    station_id: int
    t: float
    evidence: dict[str, Any]


class ThresholdAlerter:
    """The watch's thresholds on ONE event at a time -- no window, no latch."""

    def __init__(self, rules: dict[str, Any], cooldown_s: float = 0.0):
        self.rules = rules
        self.cooldown = cooldown_s
        self.paint = {int(s) for s in rules["paint_env"]["stations"]}
        self._last: dict[tuple, float] = {}

    def _emit(self, key: tuple, t: float) -> bool:
        if t - self._last.get(key, float("-inf")) < self.cooldown:
            return False
        self._last[key] = t
        return True

    def observe(self, e: dict[str, Any], t: float) -> list[Alert]:
        out: list[Alert] = []
        sid = int(e["station_id"])
        cycle = e.get("cycle_time_s")
        if (e.get("station_state") in STALLED_STATES and cycle is not None
                and cycle > float(self.rules["takt_s"]) and self._emit((STARVATION, sid), t)):
            out.append(Alert(STARVATION, sid, t, {"stalled_share": 1.0,
                                                  "stalled_state": e["station_state"]}))
        zone, ambient = e.get("humidity_pct"), e.get("ambient_humidity_pct")
        pe = self.rules["paint_env"]
        if (sid in self.paint and zone is not None and ambient is not None
                and zone > float(pe["humidity_threshold_pct"])
                and zone - ambient >= float(pe["humidity_excess_pct"])
                and self._emit((PAINT_ENV, 0), t)):
            out.append(Alert(PAINT_ENV, sid, t,
                             {"humidity_excess_over_ambient_pct": zone - ambient}))
        tw = self.rules["tool_wear"]
        for signal in tw["signals"]:
            value, ref = e.get(signal), tw["baselines"].get(f"{sid}|{signal}|{e.get('variant')}")
            if value is None or not ref:
                continue
            dev = (float(value) - ref) / ref * 100.0
            if abs(dev) >= float(tw["deviation_pct"]) and self._emit((TOOL_WEAR, sid, signal), t):
                out.append(Alert(TOOL_WEAR, sid, t, {"deviation_pct": dev}))
        return out


def _is_match(alert: Alert, fault: dict[str, Any] | None, t_onset: float) -> bool:
    """A correct alert: right detector, right place, at or after onset. A flow fault shows
    at the station AND its neighbours (a down station blocks upstream, starves
    downstream), so starvation accepts the station or either neighbour; exact-station
    hits are reported separately."""
    if fault is None or alert.t < t_onset or alert.detector != DETECTOR_FOR[fault["scenario"]]:
        return False
    if alert.detector == PAINT_ENV:
        return True
    reach = 1 if alert.detector == STARVATION else 0
    return abs(alert.station_id - fault["station_id"]) <= reach


@dataclass
class RunResult:
    fault: dict[str, Any] | None
    seed: int
    first_match: dict[str, float | None] = field(default_factory=dict)
    exact_station: dict[str, bool] = field(default_factory=dict)
    root_cause_ok: dict[str, bool | None] = field(default_factory=dict)
    false_alarms: dict[str, int] = field(default_factory=dict)
    observed_h: float = 0.0
    units: int = 0
    alerts: dict[str, list[Alert]] = field(default_factory=dict)  # kept for clean runs
    placebo: dict[str, bool] = field(default_factory=dict)
    loss_pct: float = 0.0


def _run(base: LiveTwinEngine, rules: dict[str, Any], fault: dict[str, Any] | None,
         seed: int) -> RunResult:
    twin = copy.deepcopy(base)
    alerters = {"watch": LineWatch(rules), "threshold": ThresholdAlerter(rules),
                "deadband": ThresholdAlerter(rules, float(rules["cooldown_s"]))}
    res = RunResult(fault=fault, seed=seed,
                    first_match={s: None for s in SYSTEMS},
                    false_alarms={s: 0 for s in SYSTEMS},
                    alerts={s: [] for s in SYSTEMS})
    t0 = twin.now
    t_onset = t0 + PRE_S

    def record(name: str, got: list[Alert]) -> None:
        for a in got:
            if fault is None:
                res.alerts[name].append(a)
            if not _is_match(a, fault, t_onset):
                res.false_alarms[name] += 1
            elif res.first_match[name] is None:
                res.first_match[name] = (a.t - t_onset) / 60.0
                res.exact_station[name] = (a.detector == PAINT_ENV
                                           or a.station_id == fault["station_id"])
                res.root_cause_ok[name] = (
                    hypothesis(a.detector, a.evidence)[0] == fault["scenario"])

    def run_for(seconds: float) -> None:
        watch = alerters["watch"]
        for _ in range(int(seconds // CHUNK_S)):
            for e in twin.advance(CHUNK_S):
                if e.get("event_type") != "station_update" or "station_id" not in e:
                    continue
                t = float(e["sim_time_s"])
                record("watch", [Alert(f.detector, f.station_id, t, f.evidence)
                                 for f in watch.observe(e)])
                for name in ("threshold", "deadband"):
                    record(name, alerters[name].observe(e, t))
            # The twin's clock target keeps moving when the DES has nothing left to do.
            t = float(twin._clock_target)
            record("watch", [Alert(f.detector, f.station_id, t, f.evidence)
                             for f in watch.tick(_epoch(twin.timestamp(t)))])

    run_for(PRE_S)
    if fault is not None:
        twin.inject(Incident(fault["scenario"], fault["severity"],
                             station_id=(None if fault["scenario"] == "high_humidity"
                                         else fault["station_id"])))
    u_onset = twin.units_completed
    run_for(POST_S)
    res.units = twin.units_completed - u_onset
    res.observed_h = (float(twin._clock_target) - t0) / 3600.0  # now freezes in a deadlock
    return res


def _score_against_clean(r: RunResult, clean: RunResult, t_onset: float) -> None:
    r.loss_pct = 100.0 * (clean.units - r.units) / clean.units if clean.units else 0.0
    for s in SYSTEMS:
        r.placebo[s] = any(_is_match(a, r.fault, t_onset) for a in clean.alerts[s])


def _faults(cfg) -> list[dict[str, Any]]:
    """Config-driven targets -- no station id is written in this file."""
    tq = list(cfg.defect_physics.mode_c.contributing_stations)
    paint = cfg.defect_physics.mode_a.contributing_stations[0]
    ids = [s.id for s in cfg.stations if not s.is_inspection_station]
    # Three flow targets spread along the line: the quartiles of the station list.
    flow = sorted({ids[len(ids) // 4], ids[len(ids) // 2], ids[3 * len(ids) // 4]})
    out = [{"scenario": "torque_degradation", "station_id": s, "severity": SEVERITY} for s in tq]
    out.append({"scenario": "high_humidity", "station_id": paint, "severity": SEVERITY})
    for s in flow:
        out.append({"scenario": "station_breakdown", "station_id": s, "severity": SEVERITY})
        out.append({"scenario": "buffer_congestion", "station_id": s,
                    "severity": min(SEVERITY, MAX_CONGESTION)})
    return out


def _summarise(results: list[RunResult]) -> dict[str, Any]:
    fault_runs = [r for r in results if r.fault]
    clean_runs = [r for r in results if not r.fault]
    out: dict[str, Any] = {}
    for s in SYSTEMS:
        delays = [r.first_match[s] for r in fault_runs if r.first_match[s] is not None]
        rc = [r.root_cause_ok[s] for r in fault_runs if r.root_cause_ok.get(s) is not None]
        exact = [r.exact_station[s] for r in fault_runs if s in r.exact_station]
        clean_h = sum(r.observed_h for r in clean_runs)
        all_h = sum(r.observed_h for r in results)
        by_scenario: dict[str, dict[str, Any]] = {}
        groups: dict[str, list[RunResult]] = defaultdict(list)
        for r in fault_runs:
            groups[r.fault["scenario"]].append(r)
        for scen, rs in sorted(groups.items()):
            d = [r.first_match[s] for r in rs if r.first_match[s] is not None]
            c = [r.root_cause_ok[s] for r in rs if r.root_cause_ok.get(s) is not None]
            by_scenario[scen] = {
                "runs": len(rs), "detected": len(d),
                "placebo_detected": sum(r.placebo[s] for r in rs),
                "median_loss_pct": round(statistics.median(r.loss_pct for r in rs), 1),
                "median_delay_min": round(statistics.median(d), 1) if d else None,
                "root_cause_correct": sum(c), "root_cause_of": len(c),
            }
        impactful = [r for r in fault_runs if r.loss_pct >= IMPACT_PCT]
        out[s] = {
            "recall": round(len(delays) / len(fault_runs), 3) if fault_runs else None,
            "placebo_recall": (round(sum(r.placebo[s] for r in fault_runs) / len(fault_runs), 3)
                               if fault_runs else None),
            "impactful_runs": len(impactful),
            "impactful_detected": sum(r.first_match[s] is not None for r in impactful),
            "detected": len(delays), "fault_runs": len(fault_runs),
            "median_delay_min": round(statistics.median(delays), 1) if delays else None,
            "exact_station_share": round(sum(exact) / len(exact), 3) if exact else None,
            "root_cause_accuracy": round(sum(rc) / len(rc), 3) if rc else None,
            "false_alarms_per_shift_clean": (
                round(sum(r.false_alarms[s] for r in clean_runs) / (clean_h / SHIFT_H), 2)
                if clean_h else None),
            "false_alarms_per_shift_all": round(
                sum(r.false_alarms[s] for r in results) / (all_h / SHIFT_H), 2),
            "by_scenario": by_scenario,
        }
    return out


def verdict(summary: dict[str, Any]) -> list[str]:
    """Plain sentences. If the watch does not win on a measure, this says so."""
    w, lines = summary["watch"], []
    for base in ("threshold", "deadband"):
        b = summary[base]
        parts = []
        parts.append(f"placebo recall {w['placebo_recall']:.0%} vs {b['placebo_recall']:.0%} "
                     f"(detections on a clean line)")
        parts.append(f"impactful faults caught {w['impactful_detected']}/{w['impactful_runs']} "
                     f"vs {b['impactful_detected']}/{b['impactful_runs']}")
        parts.append(("MORE" if w["recall"] > b["recall"] else
                      "FEWER" if w["recall"] < b["recall"] else "the SAME number of")
                     + f" faults detected ({w['detected']} vs {b['detected']})")
        if w["median_delay_min"] is not None and b["median_delay_min"] is not None:
            faster = w["median_delay_min"] < b["median_delay_min"]
            parts.append(f"{'FASTER' if faster else 'SLOWER or equal'} median detection "
                         f"({w['median_delay_min']} vs {b['median_delay_min']} min)")
        fa_w, fa_b = w["false_alarms_per_shift_all"], b["false_alarms_per_shift_all"]
        parts.append(f"{'FEWER' if fa_w < fa_b else 'MORE or equal'} false alarms per "
                     f"shift ({fa_w} vs {fa_b})")
        if w["root_cause_accuracy"] is not None and b["root_cause_accuracy"] is not None:
            parts.append(f"root cause {w['root_cause_accuracy']:.0%} vs "
                         f"{b['root_cause_accuracy']:.0%}")
        lines.append(f"watch vs {base}: " + "; ".join(parts) + ".")
    return lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--plant", default="plant_a")
    p.add_argument("--seeds", type=int, default=3)
    p.add_argument("--out", type=Path, default=ARTIFACTS / "benchmarks")
    args = p.parse_args(argv)

    started = time.time()
    cfg = with_horizon(load_plant_config(ROOT / "configs" / f"{args.plant}.yaml"), 2.0)
    rules = load_rules(ARTIFACTS, args.plant)
    if rules is None:
        raise SystemExit(f"no watch rules for {args.plant}: run python -m offline.build_watch_rules")
    con = sqlite3.connect(f"file:{ARTIFACTS / 'pulsetwin.db'}?mode=ro", uri=True)
    threshold = float(con.execute("SELECT value FROM metrics WHERE plant_id=? AND "
                                  "key='budget_threshold'", (args.plant,)).fetchone()[0])
    con.close()

    faults = _faults(cfg)
    results: list[RunResult] = []
    for seed in range(42, 42 + args.seeds):
        base = LiveTwinEngine(cfg, seed=seed, artifacts=ARTIFACTS, budget_threshold=threshold)
        base.start()  # warmed once per seed, forked per scenario
        t_onset = base.now + PRE_S
        clean = None
        for fault in [None, *faults]:
            r = _run(base, rules, fault, seed)
            if fault is None:
                clean = r
            else:
                _score_against_clean(r, clean, t_onset)
            results.append(r)
            label = f"{fault['scenario']}@S{fault['station_id']}" if fault else "clean"
            print(f"seed {seed} {label:<28} loss {r.loss_pct:5.1f}%  "
                  + "  ".join(f"{s}={r.first_match[s] if r.first_match[s] is not None else '-'}"
                              f"/fa{r.false_alarms[s]}/pl{int(r.placebo.get(s, 0))}"
                              for s in SYSTEMS), flush=True)

    summary = _summarise(results)
    report = {
        "plant_id": args.plant, "git_sha": git_sha(), "seeds": list(range(42, 42 + args.seeds)),
        "twin": "offline.live_twin.LiveTwinEngine", "severity": SEVERITY,
        "pre_onset_h": PRE_S / 3600, "post_onset_h": POST_S / 3600, "shift_h": SHIFT_H,
        "faults": faults, "systems": summary, "verdict": verdict(summary),
        "not_measured": {
            "containment_and_escape": "single-station incidents are defect-inert in the "
                                      "twin (ADR-055); an escape comparison compares zeros",
            "llm_diagnosis": "provider quota; the deterministic diagnosis is measured",
        },
        "elapsed_s": round(time.time() - started, 1),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"agent_eval_{args.plant}.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("\n" + "\n".join(report["verdict"]))
    print(f"wrote {path} in {report['elapsed_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
