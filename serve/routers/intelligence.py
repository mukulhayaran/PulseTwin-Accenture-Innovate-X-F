"""Decision-intelligence surfaces: alert triage, shift reporting, data quality, evidence.

Everything here is DETERMINISTIC and computed in the backend. That is the point of the
file, not an implementation detail: these four endpoints are the evidence the GenAI tier
reasons over, and if a language model were allowed to produce any of them it could invent
a triage class, a shift statistic or a causal edge and nothing downstream would catch it.
The verifier in `genai/verifier.py` can only check a number against a tool result -- so
the tool result has to be the trustworthy thing.

The division of labour, which ADR-038 states in full:

    this file        decides, counts, ranks, and links      (deterministic, testable)
    genai/           explains, narrates, and recommends     (verified against this)

Every threshold below is a named constant with the reasoning beside it, and every
plant-specific number (`budget_threshold`, `alert_budget`) is read from `plant_meta`
rather than written here -- CLAUDE.md §1 bans plant and station literals outside
`configs/*.yaml`, and `plant_b` must work with no edit to this file.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from serve import db
from serve.middleware.auth import require_role
from serve.routers.system import _active_dropouts
from serve.shift import shift_label, shift_window

router = APIRouter()


# --------------------------------------------------------------------------- constants

# Triage classes, most urgent first. `SUPPRESS` is a RANKING, never a deletion: a
# suppressed row is still returned, still carries its reasons, and still counts in the
# response totals. An alert the operator never sees is an alert nobody can overrule, and
# "the AI hid it" is the single worst outcome this endpoint could produce.
CRITICAL = "CRITICAL"
INVESTIGATE = "INVESTIGATE"
MONITOR = "MONITOR"
SUPPRESS = "LOW_CONFIDENCE"

TRIAGE_CLASSES = (CRITICAL, INVESTIGATE, MONITOR, SUPPRESS)

# Severity is expressed as a MULTIPLE of the plant's own alert threshold, not as a raw
# probability. A risk of 0.41 means nothing on its own; 1.0x the threshold the plant
# actually alerts at means everything, and it transfers across plants unchanged.
SEVERITY_CRITICAL = 1.25
SEVERITY_INVESTIGATE = 1.00
SEVERITY_MONITOR = 0.75

# LOW_CONFIDENCE is NOT triggered by a wide confidence interval, and the reason is a
# measured property of this artifact rather than a design preference.
#
# The stored interval is a deterministic function of the point estimate: across both
# plants, every one of the ~1,250 distinct `calibrated_risk` values maps to exactly ONE
# `ci_high - ci_low`, and there are exactly as many distinct widths as distinct risks.
# The interval therefore carries NO information the point estimate does not already
# carry. Ranking on it would be double-counting one number and presenting the second
# copy as corroborating evidence -- which is the specific dishonesty an evidence-grounded
# triage surface exists to prevent. The width is still REPORTED, labelled as derived.
#
# What does independently degrade confidence is the quality of the INPUTS: a dark sensor
# at the unit's station, or station statistics that are reconstructed rather than
# measured. Those are what LOW_CONFIDENCE keys on.
CI_IS_DERIVED_FROM_POINT_ESTIMATE = True

# Peers sharing a related-unit fingerprint inside the containment window. Mirrors the
# `at_risk_threshold` in configs/alert_policies.yaml that escalates straight to level 2.
CONTAINMENT_ESCALATION_PEERS = 3

# Lead time below which a correct prediction is not actionable: the unit reaches the
# inspection gate before anyone can intervene. Sourced to the same 5-minute
# floor_supervisor response window as serve/routers/alerts_mgmt.py's level 1.
LEAD_TIME_URGENT_MIN = 5.0

# Containment window, mirroring serve/main.py::containment.
CONTAINMENT_WINDOW_MIN = 30

# Physically plausible signal ranges. MIRRORS ingestion/data_quality.py::VALID_RANGES --
# `serve/` cannot import `ingestion/` (the import wall bans it), the same way
# alerts_mgmt.py mirrors configs/alert_policies.yaml. `tests/test_data_quality_api.py`
# asserts the two dictionaries are equal, so a drift is a test failure, not a surprise.
VALID_RANGES: dict[str, tuple[float, float]] = {
    "torque_nm": (0.0, 200.0),
    "vibration_mm_s": (0.0, 50.0),
    "temperature_c": (-10.0, 250.0),
    "weld_current_a": (0.0, 20_000.0),
    "humidity_pct": (0.0, 100.0),
    "film_thickness_um": (0.0, 500.0),
}

# ingestion/data_quality.py weights completeness 0.4, timeliness 0.3, validity 0.3.
# TIMELINESS IS NOT COMPUTABLE HERE and is reported as null rather than invented: the
# artifact stores event timestamps but no ARRIVAL timestamps, so there is no way to know
# how late a reading was. The two available components are renormalised over their own
# weights and the response states which components it actually used.
DQ_WEIGHTS = {"completeness": 0.4, "timeliness": 0.3, "validity": 0.3}

# Features name their station as an `s<N>_` prefix; see the evidence-graph builder for
# why substring-matching the signal name instead is wrong.
_STATION_PREFIX = re.compile(r"^s(\d+)_")

ASSOCIATION_DISCLAIMER = (
    "Evidence suggests an association; causal relationship is not established."
)


def _plant(request: Request, plant_id: str) -> dict:
    meta = request.app.state.plants.get(plant_id)
    if meta is None:
        raise HTTPException(404, f"unknown plant '{plant_id}'")
    return meta


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(int(value), hi))


def _round(value: Any, places: int = 4) -> Any:
    return round(float(value), places) if isinstance(value, (int, float)) else None


# ------------------------------------------------------------------------ alert triage


def _triage_one(
    unit: dict,
    *,
    budget_threshold: float,
    peers: int,
    peers_in_window: int,
    implicated_station: int | None,
    station_lost_uph: float | None,
    station_dark: bool,
    station_inferred: bool,
) -> dict[str, Any]:
    """Classify one alert, and say why in terms an operator can argue with.

    Every branch appends a reason. A classification with an empty `reasons` list is a
    bug, not a quiet default -- `tests/test_alert_triage.py` asserts the list is never
    empty for any row.
    """
    risk = float(unit["calibrated_risk"] or 0.0)
    severity = risk / budget_threshold if budget_threshold else 0.0
    ci_low, ci_high = unit.get("ci_low"), unit.get("ci_high")
    ci_width = (
        float(ci_high) - float(ci_low)
        if isinstance(ci_low, (int, float)) and isinstance(ci_high, (int, float))
        else None
    )
    lead = unit.get("lead_time_min")
    lead = float(lead) if isinstance(lead, (int, float)) else None

    reasons: list[str] = []
    factors = [
        {"name": "severity", "value": _round(severity, 3),
         "unit": "ratio",
         "note": f"{severity:.2f}x the plant's own alert threshold "
                 f"({budget_threshold:.4f})"},
        {"name": "confidence_interval_width", "value": _round(ci_width, 4),
         "unit": "ratio", "informative": False,
         "note": "derived: this interval is a deterministic function of the point "
                 "estimate, so it adds no information beyond `severity` and is NOT "
                 "used to classify"},
        {"name": "lead_time_min", "value": _round(lead, 2), "unit": "minutes",
         "note": "time between the prediction and the inspection gate"
                 if lead is not None else "not available for this unit"},
        {"name": "peers_above_threshold", "value": peers, "unit": "count",
         "informative": True,
         "note": f"other units inside the +/-{CONTAINMENT_WINDOW_MIN}min window that are "
                 f"themselves above the plant's alert threshold"},
        {"name": "units_sharing_fingerprint", "value": peers_in_window, "unit": "count",
         "informative": False,
         "note": "the containment panel's list (same lot, or risk above 0.6x threshold, "
                 "capped at 12). Saturates its cap for every unit in this artifact, so "
                 "it is reported for agreement with that panel but NOT used to escalate"},
        {"name": "station_lost_uph", "value": _round(station_lost_uph, 2), "unit": "uph",
         "informative": True,
         "note": f"throughput S{implicated_station} is losing -- the station named by "
                 f"this unit's top-ranked factor"
                 if station_lost_uph is not None else "station not in the stats table"},
        {"name": "sensor_dark_at_station", "value": 1 if station_dark else 0,
         "unit": "count", "informative": True,
         "note": "a dropout at this station means its inputs are inferred, not measured"},
        {"name": "station_stats_inferred", "value": 1 if station_inferred else 0,
         "unit": "count", "informative": True,
         "note": "this station has no continuous sensors; its statistics are "
                 "reconstructed by the soft sensor"},
    ]

    # --- classify. Order matters: the escalating conditions are checked before the
    # de-escalating one, so a genuinely severe alert can never be talked down to
    # LOW_CONFIDENCE by a wide interval.
    if severity >= SEVERITY_CRITICAL:
        cls = CRITICAL
        reasons.append(
            f"Severity {severity:.2f}x threshold is at or above the "
            f"{SEVERITY_CRITICAL:.2f}x critical line."
        )
    elif severity >= SEVERITY_INVESTIGATE and peers >= CONTAINMENT_ESCALATION_PEERS:
        cls = CRITICAL
        reasons.append(
            f"{peers} other units in the same half-hour window are themselves above the "
            f"alert threshold, at or above the {CONTAINMENT_ESCALATION_PEERS}-unit "
            f"containment escalation line, with severity {severity:.2f}x."
        )
    elif (
        severity >= SEVERITY_INVESTIGATE
        and lead is not None
        and lead < LEAD_TIME_URGENT_MIN
    ):
        cls = CRITICAL
        reasons.append(
            f"Only {lead:.1f} minutes of lead time remain, below the "
            f"{LEAD_TIME_URGENT_MIN:.0f}-minute floor-supervisor response window."
        )
    elif severity >= SEVERITY_INVESTIGATE:
        cls = INVESTIGATE
        reasons.append(f"Severity {severity:.2f}x threshold warrants investigation.")
    elif severity >= SEVERITY_MONITOR:
        cls = MONITOR
        reasons.append(
            f"Severity {severity:.2f}x threshold is elevated but below the "
            f"{SEVERITY_INVESTIGATE:.2f}x investigation line."
        )
    else:
        cls = MONITOR
        reasons.append(
            f"Severity {severity:.2f}x threshold is below every escalation line; "
            f"the alert stands but does not compete for attention."
        )

    # De-escalation on INPUT QUALITY, never on the derived interval, and never applied
    # to a row that is already CRITICAL -- a severe alert must not be talked down by a
    # sensor problem, because a sensor problem is a reason to look harder, not less.
    if station_dark:
        reasons.append(
            f"A sensor at S{implicated_station} -- the station this unit's top-ranked "
            f"factor names -- is dark, so the inputs behind this score are reconstructed "
            f"rather than measured."
        )
    if cls != CRITICAL and (station_dark or station_inferred):
        cls = SUPPRESS
        reasons.append(
            "Ranked LOW_CONFIDENCE because the evidence behind the score is inferred "
            "rather than measured, not because the unit is known to be fine. The row is "
            "still returned and still counted."
        )

    return {
        "unit_id": unit["unit_id"],
        "prediction_ts": unit.get("prediction_ts"),
        "alert_fired": bool(unit.get("alert_fired")),
        "current_station": unit.get("current_station"),
        "implicated_station": implicated_station,
        "triage_class": cls,
        "risk": _round(risk),
        "ci_low": _round(ci_low),
        "ci_high": _round(ci_high),
        "severity_x_threshold": _round(severity, 3),
        "lead_time_min": _round(lead, 2),
        "peers_above_threshold": peers,
        "units_sharing_fingerprint": peers_in_window,
        "reasons": reasons,
        "factors": factors,
        # The whole row is model output about a prediction, not a sensor measurement.
        "provenance": "inferred" if station_dark else "measured",
    }


@router.get("/api/alerts/triage")
def alert_triage(
    request: Request,
    plant_id: str = "plant_a",
    limit: int = 25,
    at_ts: str | None = None,
    role: str = Depends(require_role("supervisor")),
) -> dict[str, Any]:
    """Rank the open alerts and say why each landed where it did.

    Deterministic by construction. The GenAI tier reads this as a tool and narrates it;
    it does not get to decide what is critical.

    `at_ts` scopes the candidate set to `prediction_ts <= at_ts`, so a caller riding the
    replay clock (the supervisor view) sees the backlog grow as the tape advances instead
    of the whole artifact up front. Omitted, it defaults to `replay_end_ts` -- the whole
    dataset -- which is the GenAI tool's calling convention and every existing caller's
    expectation, same default pattern as `shift_summary`.
    """
    meta = _plant(request, plant_id)
    limit = _clamp(limit, 1, 500)
    con = request.app.state.main_con
    budget_threshold = float(meta["budget_threshold"])
    anchor = at_ts or str(meta["replay_end_ts"])
    try:
        datetime.fromisoformat(anchor)
    except ValueError:
        raise HTTPException(422, f"at_ts is not an ISO-8601 timestamp: {at_ts!r}")

    # THE CANDIDATE SET IS NOT "alerts that already fired", and this is the whole reason
    # the endpoint earns its place.
    #
    # `alert_fired = 1` means `calibrated_risk >= budget_threshold` BY CONSTRUCTION -- 0
    # of 141 fired rows across both plants violate it. Triaging only those rows makes
    # `severity >= 1.0` a tautology, every row lands CRITICAL, and the output is a
    # ranking that cannot rank. The first version of this endpoint did exactly that.
    #
    # The right population is everything the COST model says is worth inspecting:
    # `above_cost_threshold = 1` (1,423 units on plant_a against 113 fired). The gap
    # between those two numbers IS this project's thesis -- the binding constraint is
    # human attention, not detection -- so triage is the surface that decides which of
    # the 1,423 get some of the 113. Severity then genuinely spans 0.02x to 1.0x and the
    # classes separate.
    alerts = db.query_all(
        con,
        """SELECT u.unit_id, u.calibrated_risk, u.ci_low, u.ci_high, u.lead_time_min,
                  u.prediction_ts, u.lot_id, u.variant, u.alert_fired,
                  (SELECT MAX(station_id) FROM genealogy g
                    WHERE g.plant_id = u.plant_id AND g.unit_id = u.unit_id)
                  AS current_station
             FROM units u
            WHERE u.plant_id = ?
              AND (u.alert_fired IS TRUE OR u.above_cost_threshold IS TRUE)
              AND u.prediction_ts <= ?
            ORDER BY u.calibrated_risk DESC, u.prediction_ts DESC LIMIT ?""",
        (plant_id, anchor, limit),
    )

    # The station whose signal the model actually leaned on, per unit.
    #
    # `current_station` is MAX(genealogy.station_id), which for a unit that has finished
    # its journey -- every unit in the committed artifact has -- is simply the LAST
    # station on the line, identically for all of them. Keying the dark-sensor and
    # inferred-statistics checks on that asks "is the end of the line healthy" for every
    # row, which is the same question 200 times and never the right one.
    #
    # The top-ranked factor names its station as an `s<N>_` prefix, so that is the
    # station the risk is actually about. Fetched for the whole candidate set in ONE
    # query rather than per unit: at 500 candidates the per-unit version is 500 extra
    # round trips on the request path.
    top_factor_station: dict[str, int] = {}
    for f in db.query_all(
        con,
        """SELECT unit_id, feature FROM unit_factors
            WHERE plant_id = ? AND rank = (
                SELECT MIN(rank) FROM unit_factors f2
                 WHERE f2.plant_id = unit_factors.plant_id
                   AND f2.unit_id = unit_factors.unit_id)""",
        (plant_id,),
    ):
        m = _STATION_PREFIX.match(str(f["feature"]))
        if m:
            top_factor_station[f["unit_id"]] = int(m.group(1))

    station_rows = db.query_all(
        con,
        "SELECT station_id, AVG(lost_throughput_uph) AS lost_uph, "
        "MIN(provenance) AS provenance FROM station_stats "
        "WHERE plant_id = ? GROUP BY station_id",
        (plant_id,),
    )
    lost_by_station = {r["station_id"]: r["lost_uph"] for r in station_rows}
    inferred_stations = {
        r["station_id"] for r in station_rows if r["provenance"] == "inferred"
    }
    dark_stations = {
        d["station_id"] for d in _active_dropouts(request.app.state.ops, plant_id)
    }

    results = []
    for unit in alerts:
        # Peer count uses the same predicate AND THE SAME LIMIT as
        # /api/trace/containment. The limit matters: without it this counts every unit in
        # the window (51 on plant_a) while the containment panel shows 12, and the two
        # surfaces would state different numbers for the same fingerprint on the same
        # screen. The cap is the containment list's own definition of "peers".
        flagged_dt = datetime.fromisoformat(unit["prediction_ts"])
        win_lo = (flagged_dt - timedelta(minutes=CONTAINMENT_WINDOW_MIN)).isoformat()
        win_hi = (flagged_dt + timedelta(minutes=CONTAINMENT_WINDOW_MIN)).isoformat()

        # TWO peer measures, because the obvious one does not discriminate.
        #
        # `in_window` is the containment panel's own list: same lot OR risk above 0.6x
        # threshold, capped at 12. Measured across the artifact it is 12 FOR EVERY UNIT
        # -- it saturates its own cap, so escalating on it escalates everything. It is
        # still reported, because it is the number the containment panel shows and the
        # two surfaces must agree.
        #
        # `above_threshold` counts peers in the same window that are themselves above the
        # plant's alert threshold. That one ranges 0..20 across the artifact and is what
        # the escalation rule actually keys on: "twenty other units in this half-hour are
        # also over the line" is a containment event; "twelve units exist nearby" is not.
        in_window = db.query_all(
            con,
            """SELECT u.unit_id FROM units u
                WHERE u.plant_id = ? AND u.unit_id != ?
                  AND u.prediction_ts >= ? AND u.prediction_ts <= ?
                  AND (u.lot_id = ? OR u.calibrated_risk >= ?)
                ORDER BY u.calibrated_risk DESC LIMIT 12""",
            (plant_id, unit["unit_id"], win_lo, win_hi,
             unit["lot_id"], budget_threshold * 0.6),
        )
        above = db.query_one(
            con,
            """SELECT COUNT(*) AS n FROM units u
                WHERE u.plant_id = ? AND u.unit_id != ?
                  AND u.prediction_ts >= ? AND u.prediction_ts <= ?
                  AND u.calibrated_risk >= ?""",
            (plant_id, unit["unit_id"], win_lo, win_hi, budget_threshold),
        )
        # Fall back to the last station only when no factor names one (a unit whose top
        # factor is line-level, like paint_buffer_dwell_s).
        implicated = top_factor_station.get(unit["unit_id"], unit["current_station"])
        results.append(_triage_one(
            unit,
            budget_threshold=budget_threshold,
            peers=int((above or {}).get("n") or 0),
            peers_in_window=len(in_window),
            implicated_station=implicated,
            station_lost_uph=lost_by_station.get(implicated),
            station_dark=implicated in dark_stations,
            station_inferred=implicated in inferred_stations,
        ))

    # Rank within class by the factors that actually VARY in this plant's data. On a
    # model whose calibration has plateaued, severity is constant across every alert and
    # sorting by it produces an arbitrary order wearing the costume of a ranking. Lead
    # time ascending (least time to act first), then peer count descending.
    order = {c: i for i, c in enumerate(TRIAGE_CLASSES)}
    results.sort(key=lambda r: (
        order[r["triage_class"]],
        r["lead_time_min"] if r["lead_time_min"] is not None else 1e9,
        -r["peers_above_threshold"],
    ))
    for i, r in enumerate(results, 1):
        r["rank"] = i

    counts = {c: sum(1 for r in results if r["triage_class"] == c) for c in TRIAGE_CLASSES}

    # --- diagnostics: say when a dimension cannot discriminate, rather than emitting a
    # confident-looking ranking built on a constant. Same posture as the published
    # negative results in ADR-030/031: a measurement that came out flat is reported flat.
    distinct_risk = len({r["risk"] for r in results})
    severity_discriminates = distinct_risk > 1
    notes: list[str] = []
    if results and not severity_discriminates:
        notes.append(
            f"Severity does not discriminate here: all {len(results)} alerts share one "
            f"calibrated risk value ({results[0]['risk']}) because they land in the same "
            f"leaf across every tree; Platt's monotonic scaling passes that shared raw "
            f"score through unchanged. Ranking within a class therefore falls back to "
            f"lead time and containment peer count, which do vary. This is a property of "
            f"the calibrated model, not of the units."
        )
    if results and max(counts.values()) == len(results):
        dominant = max(counts, key=lambda k: counts[k])
        notes.append(
            f"Every alert landed in one class ({dominant}). A triage that separates "
            f"nothing is reporting that fact rather than hiding it."
        )
    return {
        "plant_id": plant_id,
        "alerts": results,
        "count": len(results),
        "anchor_ts": anchor,
        "anchor_source": "caller" if at_ts else "replay_end_ts",
        "candidate_set": (
            "units above the cost-optimal inspection threshold and at or before "
            "anchor_ts, whether or not the attention budget actually fired an alert "
            "for them"
        ),
        "already_fired": sum(1 for r in results if r["alert_fired"]),
        "counts_by_class": counts,
        "budget_threshold": _round(budget_threshold),
        "diagnostics": {
            "distinct_risk_values": distinct_risk,
            "severity_discriminates": severity_discriminates,
            "confidence_interval_is_derived": CI_IS_DERIVED_FROM_POINT_ESTIMATE,
            "notes": notes,
        },
        "method": (
            "Deterministic rules over measured signals. Severity is the unit's calibrated "
            "risk as a multiple of the plant's own alert threshold; escalation also "
            "considers containment peers and remaining lead time. LOW_CONFIDENCE keys on "
            "INPUT quality (dark sensors, reconstructed station statistics), never on the "
            "stored confidence interval, which is a deterministic function of the point "
            "estimate and so carries no independent information. No alert is ever "
            "withheld -- LOW_CONFIDENCE is a rank, and every row is returned."
        ),
        "thresholds": {
            "severity_critical": SEVERITY_CRITICAL,
            "severity_investigate": SEVERITY_INVESTIGATE,
            "severity_monitor": SEVERITY_MONITOR,
            "containment_escalation_peers": CONTAINMENT_ESCALATION_PEERS,
            "lead_time_urgent_min": LEAD_TIME_URGENT_MIN,
        },
    }


# ----------------------------------------------------------------------- data quality


@router.get("/api/system/data-quality")
def data_quality(
    request: Request,
    plant_id: str = "plant_a",
    role: str = Depends(require_role("supervisor")),
) -> dict[str, Any]:
    """Per-station data quality over the committed artifact.

    Mirrors `ingestion/data_quality.py`'s components, with ONE honest hole: timeliness
    needs an arrival timestamp and the artifact stores only event timestamps, so it is
    reported as null and its weight is renormalised away rather than being filled with a
    plausible-looking number. `components_used` names what actually went into the score.
    """
    _plant(request, plant_id)
    con = request.app.state.main_con

    rows = db.query_all(
        con,
        """SELECT station_id, signal, value, provenance
             FROM station_readings WHERE plant_id = ?""",
        (plant_id,),
    )
    stations = {
        r["station_id"]: r
        for r in db.query_all(
            con,
            "SELECT station_id, station_name, sensor_tier, sensors FROM station_config "
            "WHERE plant_id = ?",
            (plant_id,),
        )
    }
    dark = {}
    for d in _active_dropouts(request.app.state.ops, plant_id):
        dark.setdefault(d["station_id"], []).append(d["sensor_type"])

    per_station: dict[int, dict[str, Any]] = {}
    for r in rows:
        st = per_station.setdefault(
            r["station_id"],
            {"n": 0, "valid": 0, "measured": 0, "inferred": 0, "signals": set()},
        )
        st["n"] += 1
        st["signals"].add(r["signal"])
        lo, hi = VALID_RANGES.get(r["signal"], (float("-inf"), float("inf")))
        value = r["value"]
        if isinstance(value, (int, float)) and value == value and lo <= value <= hi:
            st["valid"] += 1
        if r["provenance"] == "inferred":
            st["inferred"] += 1
        else:
            st["measured"] += 1

    weight_sum = DQ_WEIGHTS["completeness"] + DQ_WEIGHTS["validity"]
    out = []
    for station_id, st in sorted(per_station.items()):
        n = st["n"]
        n_signals = len(st["signals"])
        # Completeness: of the readings this station COULD have emitted for the units
        # that actually passed through it, how many are present. Units are counted from
        # the readings themselves rather than from genealogy so a station that never
        # reported at all is absent rather than silently scored 1.0.
        units_seen = db.query_one(
            con,
            "SELECT COUNT(DISTINCT unit_id) AS n FROM station_readings "
            "WHERE plant_id = ? AND station_id = ?",
            (plant_id, station_id),
        )
        expected = max(1, int((units_seen or {}).get("n") or 0) * max(1, n_signals))
        completeness = min(1.0, n / expected)
        validity = st["valid"] / n if n else 0.0
        overall = (
            DQ_WEIGHTS["completeness"] * completeness + DQ_WEIGHTS["validity"] * validity
        ) / weight_sum
        cfg = stations.get(station_id, {})
        out.append({
            "station_id": station_id,
            "station_name": cfg.get("station_name"),
            "sensor_tier": cfg.get("sensor_tier"),
            "readings": n,
            "signals": n_signals,
            "completeness": _round(completeness),
            "timeliness": None,
            "validity": _round(validity),
            "overall": _round(overall),
            "measured": st["measured"],
            "inferred": st["inferred"],
            "provenance": "inferred" if st["inferred"] > st["measured"] else "measured",
            "sensors_dark": sorted(dark.get(station_id, [])),
        })

    scored = [s["overall"] for s in out if s["overall"] is not None]
    plant_score = sum(scored) / len(scored) if scored else None

    # The committed artifact is clean simulator output: no dropped messages, no corrupt
    # values, every unit-signal pair present. Scored over it, this endpoint returns ~1.0
    # and SHOULD -- but a perfect score here means "no faults were injected into this
    # artifact", not "the line is healthy". The faults live in the live ingestion path
    # (ingestion/mqtt_publisher.py injects dropout, late arrival, duplication and
    # corruption on purpose), so the number only becomes diagnostic once that path is
    # feeding it. Saying so is the difference between a metric and a decoration.
    notes: list[str] = []
    if plant_score is not None and plant_score > 0.999:
        notes.append(
            "Scored against the committed artifact, which is clean simulator output "
            "with no injected faults, so a near-perfect score is expected and is not "
            "evidence that live ingestion is healthy. Run the MQTT path "
            "(docker-compose.prod.yml) for a score that can move."
        )

    return {
        "plant_id": plant_id,
        "stations": out,
        "count": len(out),
        "plant_score": _round(plant_score),
        "components_used": ["completeness", "validity"],
        "components_unavailable": ["timeliness"],
        "notes": notes,
        "method": (
            "Mirrors ingestion/data_quality.py over the committed artifact. Timeliness "
            "requires a message arrival timestamp, which the artifact does not store, so "
            "it is reported as null and its weight is renormalised across the two "
            "components that ARE measurable rather than being estimated."
        ),
        "weights": DQ_WEIGHTS,
    }


# ---------------------------------------------------------------------- shift summary


@router.get("/api/shift/summary")
def shift_summary(
    request: Request,
    plant_id: str = "plant_a",
    at_ts: str | None = None,
    role: str = Depends(require_role("manager")),
) -> dict[str, Any]:
    """The factual substrate behind a shift report or a handover.

    One endpoint, two narratives: `intent=shift_intel` and `intent=handover` in the GenAI
    tier read THIS and differ only in how they tell it. Two endpoints would let the two
    reports quietly disagree about the same shift.
    """
    meta = _plant(request, plant_id)
    con = request.app.state.main_con
    ops = request.app.state.ops

    # Default to the end of the replay: "now" in a replay is wherever the tape is, and
    # the last unit in the artifact is the most defensible stand-in when the caller does
    # not say. The response always states which window it used.
    anchor = at_ts or str(meta["replay_end_ts"])
    try:
        start, end = shift_window(anchor)
    except ValueError:
        raise HTTPException(422, f"at_ts is not an ISO-8601 timestamp: {at_ts!r}")

    produced = db.query_one(
        con,
        """SELECT COUNT(*) AS units,
                  SUM(CASE WHEN alert_fired IS TRUE THEN 1 ELSE 0 END) AS alerts,
                  SUM(CASE WHEN actual_defect IS TRUE THEN 1 ELSE 0 END) AS defects,
                  AVG(calibrated_risk) AS mean_risk
             FROM units
            WHERE plant_id = ? AND prediction_ts >= ? AND prediction_ts <= ?""",
        (plant_id, start, end),
    ) or {}

    windows = db.query_all(
        con,
        """SELECT window_start, window_end, bottleneck_station, confidence,
                  ground_truth_station, correct, shift_event
             FROM bottleneck_windows
            WHERE plant_id = ? AND window_start >= ? AND window_start <= ?
            ORDER BY window_start""",
        (plant_id, start, end),
    )
    shifts = [w for w in windows if w["shift_event"]]
    correct = [w for w in windows if w["correct"] is not None]

    worst = db.query_all(
        con,
        """SELECT station_id, AVG(lost_throughput_uph) AS lost_uph,
                  AVG(utilisation) AS util, MIN(provenance) AS provenance
             FROM station_stats WHERE plant_id = ?
            GROUP BY station_id ORDER BY lost_uph DESC LIMIT 5""",
        (plant_id,),
    )

    dropouts = _active_dropouts(ops, plant_id)
    drift = db.query_all(
        ops,
        """SELECT metric_type, feature_name, value, threshold, alert_triggered
             FROM drift_metrics WHERE plant_id = ? ORDER BY id DESC LIMIT 200""",
        (plant_id,),
    )
    latest_drift: dict[tuple, dict] = {}
    for d in drift:
        latest_drift.setdefault((d["metric_type"], d["feature_name"]), d)
    breached = [d for d in latest_drift.values() if d["alert_triggered"]]

    return {
        "plant_id": plant_id,
        "shift": {
            "label": shift_label(anchor),
            "window_start": start,
            "window_end": end,
            "anchor_ts": anchor,
            "anchor_source": "caller" if at_ts else "replay_end_ts",
        },
        "production": {
            "units": int(produced.get("units") or 0),
            "alerts_fired": int(produced.get("alerts") or 0),
            "alert_budget": int(meta["alert_budget"]),
            "confirmed_defects": int(produced.get("defects") or 0),
            "mean_risk": _round(produced.get("mean_risk")),
            "units_per_hour_target": _round(meta["units_per_hour_target"], 2),
            "achieved_uph": _round(meta["achieved_uph"], 2),
        },
        "bottleneck": {
            "windows_in_shift": len(windows),
            "shift_events": len(shifts),
            "opened_at_station": windows[0]["bottleneck_station"] if windows else None,
            "closed_at_station": windows[-1]["bottleneck_station"] if windows else None,
            "detector_agreement": (
                _round(sum(1 for w in correct if w["correct"]) / len(correct), 3)
                if correct else None
            ),
            "moves": [
                {"at": w["window_start"], "to_station": w["bottleneck_station"],
                 "confidence": _round(w["confidence"], 3)}
                for w in shifts
            ],
        },
        "worst_stations": [
            {"station_id": w["station_id"], "lost_uph": _round(w["lost_uph"], 2),
             "util": _round(w["util"]), "provenance": w["provenance"]}
            for w in worst
        ],
        "sensors": {
            "dropouts_active": len(dropouts),
            "stations_affected": sorted({d["station_id"] for d in dropouts}),
            "detail": [
                {"station_id": d["station_id"], "sensor": d["sensor_type"],
                 "since": d["last_reading_at"]}
                for d in dropouts
            ],
        },
        "model": {
            "drift_metrics_breached": len(breached),
            "breached": [
                {"metric_type": d["metric_type"], "feature": d["feature_name"],
                 "value": _round(d["value"]), "threshold": _round(d["threshold"])}
                for d in breached
            ],
        },
        "method": (
            "Counts over the committed artifact inside one 8-hour shift window, plus "
            "live operational state from sensor_health and drift_metrics. Shift "
            "boundaries come from serve/shift.py, the same function the server-side "
            "alert budget uses, so this report and the supervisor's alert meter cannot "
            "disagree."
        ),
    }


# ---------------------------------------------------------------------- evidence graph


def _edge(
    src: str, dst: str, relation: str, *, evidence_source: str, window: str | None,
    provenance: str, confidence: float | None, model: str | None,
) -> dict[str, Any]:
    return {
        "from": src, "to": dst, "relation": relation,
        "evidence_source": evidence_source, "window": window,
        "provenance": provenance, "confidence": _round(confidence),
        "model": model,
    }


@router.get("/api/trace/evidence-graph/{unit_id}")
def evidence_graph(
    unit_id: str,
    request: Request,
    plant_id: str = "plant_a",
    role: str = Depends(require_role("supervisor")),
) -> dict[str, Any]:
    """The chain from a station signal to an inspection risk, as nodes and edges.

    Built HERE, deterministically, so the GenAI tier can narrate a chain it cannot
    fabricate. Every edge names its evidence source, its window, whether the underlying
    value was measured or inferred, and which model produced it.

    The graph is explicitly an ASSOCIATION graph. Nothing in this repository establishes
    causation: the features are correlated with a label, the fingerprint is a shared-lot
    and shared-window join, and the simulator's true causes are deliberately partly
    unobservable (docs/AUDIT.md B34). Saying otherwise to an industrial audience would be
    the most expensive sentence in the demo, so `causality` says so on every response.
    """
    _plant(request, plant_id)
    con = request.app.state.main_con

    unit = db.query_one(
        con, "SELECT * FROM units WHERE plant_id = ? AND unit_id = ?", (plant_id, unit_id)
    )
    if not unit:
        raise HTTPException(404, f"unknown unit '{unit_id}'")

    factors = db.query_all(
        con,
        """SELECT rank, feature, direction, contribution, value, plain_language
             FROM unit_factors WHERE plant_id = ? AND unit_id = ?
            ORDER BY rank LIMIT 5""",
        (plant_id, unit_id),
    )
    readings = db.query_all(
        con,
        """SELECT station_id, signal, value, provenance, ci_low, ci_high
             FROM station_readings WHERE plant_id = ? AND unit_id = ?
            ORDER BY station_id""",
        (plant_id, unit_id),
    )
    station_names = {
        r["station_id"]: r["station_name"]
        for r in db.query_all(
            con,
            "SELECT station_id, station_name FROM station_config WHERE plant_id = ?",
            (plant_id,),
        )
    }

    flagged_dt = datetime.fromisoformat(unit["prediction_ts"])
    win_start = (flagged_dt - timedelta(minutes=CONTAINMENT_WINDOW_MIN)).isoformat()
    win_end = (flagged_dt + timedelta(minutes=CONTAINMENT_WINDOW_MIN)).isoformat()
    window_label = f"{win_start} .. {win_end}"

    peers = db.query_all(
        con,
        """SELECT unit_id, calibrated_risk, lot_id, variant FROM units
            WHERE plant_id = ? AND unit_id != ?
              AND prediction_ts >= ? AND prediction_ts <= ?
              AND (lot_id = ? OR calibrated_risk >= ?)
            ORDER BY calibrated_risk DESC LIMIT 12""",
        (plant_id, unit_id, win_start, win_end, unit["lot_id"],
         float(request.app.state.plants[plant_id]["budget_threshold"]) * 0.6),
    )

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    # A reading is only attached if the factor list actually implicates ITS station.
    #
    # The station comes from the feature NAME, which encodes it as an `s<N>_` prefix
    # (`s15_checklist_time_s`, `s5_torque_nm_dev_median`). Substring-matching the signal
    # name instead -- looking for "torque_nm" anywhere in the feature -- matches that
    # signal at EVERY station on the line, which produced a 70-node graph naming all 30
    # stations for a unit whose top factors implicate three. A graph that indicts
    # everything explains nothing.
    #
    # Features with no `s<N>_` prefix (`paint_buffer_dwell_s`) are genuinely line-level,
    # not station-level. They are kept as their own node rather than dropped: "the unit
    # sat in the paint buffer for 572s" was the second-ranked factor here, and losing it
    # would be losing the explanation to a naming convention.
    implicated: dict[int, list[dict]] = {}
    line_level: list[dict] = []
    readings_by_station: dict[int, list[dict]] = {}
    for reading in readings:
        readings_by_station.setdefault(reading["station_id"], []).append(reading)

    for f in factors:
        m = _STATION_PREFIX.match(str(f["feature"]))
        if not m:
            line_level.append(f)
            continue
        station_id = int(m.group(1))
        for reading in readings_by_station.get(station_id, []):
            if str(reading["signal"]) in str(f["feature"]):
                implicated.setdefault(station_id, [])
                if reading not in implicated[station_id]:
                    implicated[station_id].append(reading)
        # A factor can implicate a station that emits no matching continuous signal --
        # `s15_checklist_time_s` is a manual station. Register it anyway so the chain
        # still names the station the model actually leaned on.
        implicated.setdefault(station_id, [])

    for station_id, sigs in sorted(implicated.items()):
        station_node = f"station:{station_id}"
        nodes.append({
            "id": station_node, "kind": "station", "label":
                f"S{station_id} {station_names.get(station_id, '')}".strip(),
            "station_id": station_id,
        })
        for sig in sigs:
            sig_node = f"signal:{station_id}:{sig['signal']}"
            nodes.append({
                "id": sig_node, "kind": "signal", "label": sig["signal"],
                "value": _round(sig["value"]), "provenance": sig["provenance"],
                "ci_low": _round(sig["ci_low"]), "ci_high": _round(sig["ci_high"]),
            })
            edges.append(_edge(
                station_node, sig_node, "emitted",
                evidence_source="station_readings",
                window=None,
                provenance=sig["provenance"],
                confidence=None,
                model="soft_sensor" if sig["provenance"] == "inferred" else None,
            ))

    for f in line_level:
        node_id = f"factor:{f['feature']}"
        nodes.append({
            "id": node_id, "kind": "line_factor", "label": f["feature"],
            "rank": f["rank"], "direction": f["direction"],
            "contribution": _round(f["contribution"]),
            "plain_language": f["plain_language"],
            # Line-level features are computed from the unit's own journey timings, which
            # are measured, not reconstructed.
            "provenance": "measured",
        })

    window_node = "window:containment"
    nodes.append({
        "id": window_node, "kind": "window", "label":
            f"+/-{CONTAINMENT_WINDOW_MIN} min around the prediction",
        "window_start": win_start, "window_end": win_end,
    })
    for station_id in sorted(implicated):
        edges.append(_edge(
            f"station:{station_id}", window_node, "observed_within",
            evidence_source="units.prediction_ts",
            window=window_label, provenance="measured", confidence=None, model=None,
        ))

    fingerprint_node = "fingerprint:shared"
    shared_kinds = sorted({
        kind
        for p in peers
        for kind in (
            ["same_lot"] if p["lot_id"] == unit["lot_id"] else []
        ) + (
            ["same_variant"] if p["variant"] == unit["variant"] else []
        ) + ["same_time_window"]
    })
    nodes.append({
        "id": fingerprint_node, "kind": "fingerprint",
        "label": "shared production context",
        "shared_factors": shared_kinds,
    })
    edges.append(_edge(
        window_node, fingerprint_node, "groups_units_by",
        evidence_source="units.lot_id, units.variant, units.prediction_ts",
        window=window_label, provenance="measured", confidence=None, model=None,
    ))

    risk_node = "risk:unit"
    # (line-level factor -> risk edges are appended after the risk node exists, below)
    nodes.append({
        "id": risk_node, "kind": "risk", "label": f"defect risk for {unit_id}",
        "value": _round(unit["calibrated_risk"]),
        "ci_low": _round(unit["ci_low"]), "ci_high": _round(unit["ci_high"]),
        "provenance": "inferred",
        "factors": [
            {"rank": f["rank"], "feature": f["feature"], "direction": f["direction"],
             "contribution": _round(f["contribution"]),
             "plain_language": f["plain_language"]}
            for f in factors
        ],
    })
    for f in factors:
        for station_id, sigs in implicated.items():
            for sig in sigs:
                if str(sig["signal"]) in str(f["feature"]):
                    edges.append(_edge(
                        f"signal:{station_id}:{sig['signal']}", risk_node,
                        "contributes_to",
                        evidence_source="unit_factors (decision-path attribution)",
                        window=window_label,
                        provenance=sig["provenance"],
                        confidence=abs(float(f["contribution"] or 0.0)),
                        model="defect_risk champion",
                    ))

    for f in line_level:
        edges.append(_edge(
            f"factor:{f['feature']}", risk_node, "contributes_to",
            evidence_source="unit_factors (decision-path attribution)",
            window=window_label, provenance="measured",
            confidence=abs(float(f["contribution"] or 0.0)),
            model="defect_risk champion",
        ))

    group_node = "units:at_risk"
    nodes.append({
        "id": group_node, "kind": "unit_group",
        "label": f"{len(peers)} units sharing the fingerprint",
        "count": len(peers),
        # Unit ids only. No operator, crew or checklist field is read anywhere in this
        # function -- CLAUDE.md §3, and the verifier's C4 check would reject it anyway.
        "unit_ids": [p["unit_id"] for p in peers],
    })
    edges.append(_edge(
        fingerprint_node, group_node, "identifies",
        evidence_source="/api/trace/containment peer predicate",
        window=window_label, provenance="measured", confidence=None, model=None,
    ))
    edges.append(_edge(
        risk_node, group_node, "extends_to",
        evidence_source="units.calibrated_risk over the peer set",
        window=window_label, provenance="inferred",
        confidence=_round(unit["calibrated_risk"]), model="defect_risk champion",
    ))

    inspection_node = "inspection:gate"
    nodes.append({
        "id": inspection_node, "kind": "inspection",
        "label": "final inspection exposure",
        "lead_time_min": _round(unit["lead_time_min"], 2),
    })
    edges.append(_edge(
        group_node, inspection_node, "arrives_at",
        evidence_source="units.lead_time_min",
        window=window_label, provenance="measured", confidence=None, model=None,
    ))

    return {
        "plant_id": plant_id,
        "unit_id": unit_id,
        "nodes": nodes,
        "edges": edges,
        "causality": "association",
        "causality_statement": ASSOCIATION_DISCLAIMER,
        "containment_window_min": CONTAINMENT_WINDOW_MIN,
        "method": (
            "Deterministic join over unit_factors, station_readings and the containment "
            "peer predicate. Edges are associations carried by shared lot, shared variant "
            "and a shared time window, plus decision-path attribution from the champion "
            "model. No causal inference is performed anywhere in this chain."
        ),
    }
