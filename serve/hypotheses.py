"""Competing root-cause hypotheses and their transparent scoring (ADR-067).

Hypotheses for one incident are MUTUALLY EXCLUSIVE explanations of the same symptom
(including "no fault"), so their confidences are a softmax over log-odds:

    logit_h = ln(prior_h) + sum over evidence e of  quality_e * weight_{e,h}
    confidence_h = exp(logit_h) / sum_k exp(logit_k)

`quality_e` is the provenance multiplier from configs/agentic.yaml `evidence_quality:`;
`weight_{e,h}` was set by the probe from an effect size with a fixed rule
(serve/incident_evidence.py). A model never assigns a probability: an LLM planner can only
choose WHICH evidence to gather next.

Nothing here is calibrated in the statistical sense -- a confidence of 0.8 is "the
evidence gathered favours this 4:1 over the alternatives under these weights", not an
80% hit rate. docs/AI_EVALUATION.md records how the five demo scenarios score, and that
the weights were set against them (a disclosed look, like ADR-061's).
"""

from __future__ import annotations

import math
from typing import Any

from serve.agentic_config import CONF, CONFIG

NO_FAULT = "no_fault"

# key -> (title, description, possible causes, expected observations). `{S}`, `{D}` and
# `{U}` are the incident station, its downstream and upstream neighbour.
TEMPLATES: dict[str, tuple[str, str, list[str], list[str]]] = {
    "local_degradation": (
        "S{S} equipment degradation",
        "The station itself has slowed or become unreliable.",
        ["tool wear", "drive or motor fault", "repeated micro-stops"],
        ["down time at S{S}", "torque/weld deviation at S{S}", "slower cycles at S{S}"]),
    "downstream_congestion": (
        "Downstream congestion after S{S}",
        "S{S} is held by the line after it: S{D} or the transfer between them.",
        ["S{D} slow or down", "transfer conveyor fault", "undersized buffer S{S}->S{D}"],
        ["S{S} blocked", "S{S} equipment signals normal", "S{D} busy, down or its feed full"]),
    "upstream_starvation": (
        "Upstream supply loss before S{S}",
        "S{S} is waiting for parts from S{U}.",
        ["S{U} down", "material shortage"], ["S{S} starved", "S{U} stalled"]),
    "sensor_fault": (
        "Measurement problem at S{S}",
        "The symptom is in the data, not the process.",
        ["sensor dropout", "calibration drift", "stale telemetry"],
        ["missing or inferred readings", "dropout events"]),
    "variant_mix": (
        "Variant-mix shift",
        "A different product mix changed the station's workload.",
        ["sequencing change", "demand shift"], ["variant shares differ from the recorded mix"]),
    "process_deviation": (
        "Process deviation at S{S}",
        "The method, not the machine: a rushed or skipped procedure.",
        ["rushed checklist", "skipped step", "training gap"],
        ["checklist times shorter than the norm", "equipment signals normal"]),
    "environment_control": (
        "Paint-zone climate control failure",
        "Zone humidity is above what the ambient explains.",
        ["HVAC fault", "dehumidifier failure", "door/seal left open"],
        ["humidity excess over ambient above threshold"]),
    "ambient_driven": (
        "Ambient weather, not a control failure",
        "Zone humidity is high because outside air is; the controls are tracking it.",
        ["seasonal humidity"], ["zone humidity tracks ambient"]),
    "material_lot": (
        "Incoming material lot",
        "Defects concentrate in one supplier lot.",
        ["supplier quality escape"], ["one lot over-represented among defects"]),
    NO_FAULT: (
        "Normal variation - no fault",
        "The signal is within the line's recorded variation; nothing needs fixing.",
        ["noise", "a transient that already cleared"],
        ["every measured statistic within baseline"]),
}

CATALOG: dict[str, list[str]] = {
    "throughput": ["local_degradation", "downstream_congestion", "upstream_starvation",
                   "sensor_fault", "variant_mix", NO_FAULT],
    "equipment": ["local_degradation", "process_deviation", "sensor_fault", "variant_mix",
                  NO_FAULT],
    "environment": ["environment_control", "ambient_driven", "sensor_fault", NO_FAULT],
    "quality": ["material_lot", NO_FAULT],   # + one process_deviation@<station> per candidate
}


def _fmt(text: str, s: Any, d: Any, u: Any) -> str:
    return text.replace("{S}", str(s)).replace("{D}", str(d)).replace("{U}", str(u))


def build(kind: str, station_id: int | None, down: int | None, up: int | None,
          candidates: list[dict[str, Any]] | None, detector_fired: bool,
          names: dict[int, str]) -> list[dict[str, Any]]:
    """The hypothesis set for an incident, with priors.

    Prior: `no_fault` gets 0.10 when a deterministic detector crossed a threshold (it
    measured something) and 0.30 for a manual report; the rest is shared equally. A
    stated choice, documented in docs/AGENT_ARCHITECTURE.md."""
    keys = list(CATALOG[kind])
    out = []
    if kind == "quality":
        for c in candidates or []:
            sid = c["station_id"]
            manual = c.get("manual")
            out.append(_hyp(
                f"process_deviation@{sid}", f"Defects introduced at S{sid} ({names.get(sid, '')})",
                ("A manual procedure at this station is letting defects through."
                 if manual else "A process or tool deviation at this station is creating "
                                "defects that surface at the gate."),
                ["rushed or skipped procedure"] if manual else
                ["tool wear or miscalibration", "fixture problem", "wrong program"],
                ["defective units read differently at this station than good units passing "
                 "in the same period", "deviation began before the first defective unit "
                 "entered the station"], sid))
    for k in keys:
        title, desc, causes, expect = TEMPLATES[k]
        out.append(_hyp(k, _fmt(title, station_id, down, up), _fmt(desc, station_id, down, up),
                        [_fmt(x, station_id, down, up) for x in causes],
                        [_fmt(x, station_id, down, up) for x in expect], station_id))
    p_none = 0.10 if detector_fired else 0.30
    rest = (1.0 - p_none) / max(len(out) - 1, 1)
    for h in out:
        h["prior"] = round(p_none if h["key"] == NO_FAULT else rest, 4)
        h["confidence"] = h["prior"]
    return out


def _hyp(key: str, title: str, desc: str, causes: list[str], expect: list[str],
         station_id: int | None) -> dict[str, Any]:
    return {"id": key, "key": key, "title": title, "description": desc,
            "possible_causes": causes, "expected_observations": expect,
            "supporting_evidence": [], "contradicting_evidence": [], "status": "proposed",
            "station_id": station_id, "logit": 0.0, "prior": 0.0, "confidence": 0.0,
            "contributions": []}


def score(hyps: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recompute every hypothesis from its prior and ALL evidence so far. Pure: the same
    evidence always yields the same confidences, whatever order it arrived in."""
    q = CONFIG["evidence_quality"]
    for h in hyps:
        h["logit"] = math.log(max(h["prior"], 1e-6))
        h["supporting_evidence"], h["contradicting_evidence"], h["contributions"] = [], [], []
    by_key = {h["key"]: h for h in hyps}
    for e in evidence:
        quality = float(q.get(e["provenance"], 0.0))
        for key, weight in (e.get("effects") or {}).items():
            h = by_key.get(key)
            if h is None or not weight or not quality:
                continue
            delta = quality * float(weight)
            h["logit"] += delta
            h["contributions"].append({"evidence_id": e["evidence_id"], "delta": round(delta, 3)})
            (h["supporting_evidence"] if delta > 0 else h["contradicting_evidence"]).append(
                e["evidence_id"])
    top = max(h["logit"] for h in hyps)
    z = sum(math.exp(h["logit"] - top) for h in hyps)
    for h in hyps:
        h["confidence"] = round(math.exp(h["logit"] - top) / z, 4)
        h["logit"] = round(h["logit"], 4)
        touched = h["supporting_evidence"] or h["contradicting_evidence"]
        if not touched:
            h["status"] = "proposed"
        elif h["confidence"] >= 0.5 and h["supporting_evidence"]:
            h["status"] = "supported"
        elif h["confidence"] < 0.05 and h["contradicting_evidence"]:
            h["status"] = "rejected"
        elif h["confidence"] < h["prior"] and h["contradicting_evidence"]:
            h["status"] = "weakened"
        else:
            h["status"] = "investigating"
    hyps.sort(key=lambda h: -h["confidence"])
    return hyps


def finalise(hyps: list[dict[str, Any]]) -> None:
    """At the end of an investigation, anything neither supported nor rejected is
    `unresolved` -- the commander stopped without settling it, and says so."""
    for i, h in enumerate(hyps):
        if (i == 0 and h["status"] != "supported") or h["status"] in ("proposed", "investigating"):
            h["status"] = "unresolved"


ASSESSMENTS = ("CONFIDENT", "LOW_CONFIDENCE", "INSUFFICIENT_DATA", "CONFLICTING_EVIDENCE",
               "MODEL_DEGRADED", "SENSOR_UNRELIABLE")


def assess(hyps: list[dict[str, Any]], *, cap: float, coverage: float, model_status: str,
           sensor_reliable: bool) -> dict[str, Any]:
    """The abstention classifier. Deterministic, ordered, and every branch names why."""
    top, second = hyps[0], (hyps[1] if len(hyps) > 1 else None)
    confidence = round(min(top["confidence"], cap), 4)
    margin = top["confidence"] - (second["confidence"] if second else 0.0)
    if model_status == "degraded":
        state, why = "MODEL_DEGRADED", ("model inputs have drifted past the PSI block "
                                        "threshold; model-derived conclusions are not trusted")
    elif not sensor_reliable and top["key"] != "sensor_fault":
        state, why = "SENSOR_UNRELIABLE", ("the implicated station's sensors are incomplete; "
                                           "its measured evidence cannot be relied on")
    elif coverage < CONF["min_evidence_coverage"]:
        state, why = "INSUFFICIENT_DATA", (f"only {coverage:.0%} of the evidence gathered was "
                                           "measurable")
    elif (second and margin < CONF["conflict_margin"] and top["supporting_evidence"]
          and second["supporting_evidence"]):
        state, why = "CONFLICTING_EVIDENCE", (f"'{top['title']}' and '{second['title']}' are "
                                              f"within {margin:.0%} and both have support")
    elif confidence < CONF["recommendation_min"]:
        state, why = "LOW_CONFIDENCE", (f"confidence {confidence:.0%} is below the "
                                        f"{CONF['recommendation_min']:.0%} recommendation floor"
                                        + (f" (capped at {cap:.0%} by sensor maturity)"
                                           if cap < top["confidence"] else ""))
    else:
        state, why = "CONFIDENT", f"'{top['title']}' leads at {confidence:.0%}"
    return {"assessment": state, "reason": why, "confidence": confidence,
            "raw_confidence": top["confidence"], "cap": cap, "margin": round(margin, 4),
            "uncertainty": round(1.0 - confidence, 4), "coverage": round(coverage, 3),
            "root_cause_key": top["key"], "root_cause_title": top["title"]}
