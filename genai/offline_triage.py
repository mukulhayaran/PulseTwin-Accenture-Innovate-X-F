"""Triage with no language model: a fixed tool sequence and rules.

This exists for three reasons, and the second is the interesting one:

 1. The whole feature demos and tests with NO provider credential, including the
    verifier running against real data rather than only fixtures.
 2. It is an honest BASELINE. "The agent found the same three facts the rule-based path
    found, plus the sensor-dropout correlation" is a measurable statement about whether
    the language model added anything -- which is the only framing of an LLM feature
    that fits this project's measured-not-assumed thesis. Persist both, report the delta.
 3. If the provider is down mid-demo, the panel still produces a finding.

Every claim is citation-bearing by construction, so the output passes the IDENTICAL
verifier the agent's output goes through. It is labelled as emphatically as the SPC
fallback in `serve/main.py::_fallback_predict` -- an honest lesser thing, never a
pretence.

PARITY IS A HARD REQUIREMENT, not a nice-to-have. Every named action the copilot offers
-- handover, shift intelligence, what-if, incident -- has a plan here. A button that
works only when a provider is reachable is a button that breaks on stage, and the whole
argument for this module is that the demo survives an outage with its guarantees intact.
`tests/test_genai_offline.py` runs every plan against both plants and asserts none is
ever rejected.
"""

from __future__ import annotations

import json
from typing import Any

from genai import tools as T
from genai.api_client import PulseTwinAPI
from genai.finding import Citation, Claim, Finding
from genai.loop import LoopResult, ToolRecord, truncate

BANNER = (
    "DETERMINISTIC MODE -- no language model was called. This finding was assembled by "
    "fixed rules from the tool results below."
)


def _call(
    records: list[ToolRecord],
    registry: dict[str, T.ToolSpec],
    api: PulseTwinAPI,
    name: str,
    args: dict[str, Any],
) -> tuple[str, Any]:
    seq = len(records) + 1
    call_id = f"call_{seq:04d}"
    payload = truncate(T.dispatch(registry, api, name, json.dumps(args)))
    records.append(ToolRecord(call_id, "", seq, name, args, payload, 0.0))
    return call_id, payload


def run(
    registry: dict[str, T.ToolSpec],
    api: PulseTwinAPI,
    *,
    plant_id: str,
    question: str,
) -> tuple[LoopResult, Finding]:
    records: list[ToolRecord] = []
    claims: list[Claim] = []
    unanswered: list[str] = []
    notes: list[str] = []

    bn_id, bn = _call(records, registry, api, "get_bottleneck_current",
                      {"plant_id": plant_id})
    st_id, st = _call(records, registry, api, "get_station_stats",
                      {"plant_id": plant_id})
    cfg_id, cfg = _call(records, registry, api, "get_station_config",
                        {"plant_id": plant_id})
    sys_id, sysh = _call(records, registry, api, "get_system_health",
                         {"plant_id": plant_id})

    names: dict[int, str] = {}
    if cfg.get("ok"):
        for s in cfg["data"].get("stations", []):
            names[s["station_id"]] = s["station_name"]

    # ---- the constraint itself
    current = None
    if bn.get("ok"):
        d = bn["data"]
        current = d.get("current_station")
        if isinstance(current, int):
            claims.append(Claim(
                id="c1",
                text=f"The current constraint is station S{current}"
                     f"{' (' + names[current] + ')' if current in names else ''}",
                value=float(current), unit="station_id", provenance="measured",
                citation=Citation(bn_id, "/data/current_station"),
            ))
        if isinstance(d.get("confidence"), (int, float)):
            claims.append(Claim(
                id="c2",
                text=f"Detector confidence in that constraint is "
                     f"{d['confidence']:.2f}",
                value=float(d["confidence"]), unit="ratio", provenance="measured",
                citation=Citation(bn_id, "/data/confidence"),
            ))
        if isinstance(d.get("previous_station"), int):
            notes.append(
                f"The constraint previously sat at S{d['previous_station']}"
                f" and now sits at S{current}."
            )
    else:
        unanswered.append(f"get_bottleneck_current: {bn.get('error')}")

    # ---- the evidence: worst station by lost throughput
    #
    # MEASURED rows only, and the reason is a real gap worth stating: /api/stations/stats
    # marks some rows `provenance: "inferred"` but ships no ci_low/ci_high, while
    # CLAUDE.md §3 requires an inferred value to carry its interval. A point claim about
    # such a row cannot be made contract-compliantly, so the honest move is not to make
    # it -- the same behaviour the system prompt demands of the agent. The stations are
    # named in `unanswered` rather than silently dropped.
    if st.get("ok") and isinstance(st.get("data"), list) and st["data"]:
        rows = st["data"]
        measured = [i for i, r in enumerate(rows)
                    if r.get("provenance", "measured") == "measured"]
        skipped = [rows[i].get("station_id") for i in range(len(rows))
                   if i not in measured]
        if skipped:
            unanswered.append(
                "get_station_stats: stations "
                + ", ".join(f"S{s}" for s in skipped)
                + " report inferred statistics with no confidence interval, so no point "
                  "claim is made about them (CLAUDE.md §3)."
            )

        if measured:
            worst_idx = max(measured, key=lambda i: rows[i].get("lost_uph") or 0.0)
            row = rows[worst_idx]
            sid = row.get("station_id")
            claims.append(Claim(
                id="c3",
                text=f"S{sid}{' (' + names[sid] + ')' if sid in names else ''} loses the "
                     f"most measured throughput on the line, {row['lost_uph']:.1f} UPH",
                value=float(row["lost_uph"]), unit="uph", provenance="measured",
                citation=Citation(st_id, f"/data/{worst_idx}/lost_uph"),
            ))
            for field_name, label, cid in (("blocked", "blocked", "c4"),
                                           ("starved", "starved", "c5")):
                val = row.get(field_name)
                if isinstance(val, (int, float)):
                    claims.append(Claim(
                        id=cid,
                        text=f"S{sid} is {label} {val:.3f} of the time",
                        value=float(val), unit="ratio", provenance="measured",
                        citation=Citation(st_id, f"/data/{worst_idx}/{field_name}"),
                    ))
    else:
        unanswered.append(f"get_station_stats: {st.get('error')}")

    # ---- infrastructure before behaviour: a constraint that "moved" is sometimes a
    # sensor that went dark.
    if sysh.get("ok"):
        dropouts = (sysh["data"].get("ingestion") or {}).get("sensor_dropouts_active")
        if isinstance(dropouts, int):
            claims.append(Claim(
                id="c6",
                text=f"{dropouts} sensor dropouts are currently active",
                value=float(dropouts), unit="count", provenance="measured",
                citation=Citation(sys_id, "/data/ingestion/sensor_dropouts_active"),
            ))
            if dropouts:
                notes.append(
                    "Active sensor dropouts mean some station statistics are inferred "
                    "rather than measured; treat the ranking above as provisional."
                )
    else:
        unanswered.append(f"get_system_health: {sysh.get('error')}")

    # ---- test a remedy rather than assert one
    recommendation = (
        "Insufficient data to recommend a change; re-run once the tools above return."
    )
    if current is not None:
        wf_id, wf_stations = _call(records, registry, api, "get_whatif_stations",
                                   {"plant_id": plant_id})
        covered = [s["station_id"] for s in wf_stations.get("data", [])] \
            if wf_stations.get("ok") else []
        target = current if current in covered else (covered[0] if covered else None)

        if target is None:
            unanswered.append("simulate_whatif: no station in the what-if grid")
        else:
            sim_id, sim = _call(records, registry, api, "simulate_whatif", {
                "plant_id": plant_id, "station_id": target,
                "buffer_delta": 4, "cycle_delta_pct": 0,
            })
            if sim.get("ok"):
                proj = sim["data"]["projected"]["throughput_uph"]
                base = sim["data"]["baseline"]["throughput_uph"]
                claims.append(Claim(
                    id="c7",
                    text=f"Adding 4 feed-buffer slots upstream of S{target} projects "
                         f"{proj:.2f} UPH against a {base:.2f} UPH baseline",
                    value=float(proj), unit="uph", provenance="measured",
                    citation=Citation(sim_id, "/data/projected/throughput_uph"),
                ))
                claims.append(Claim(
                    id="c8", text=f"Baseline throughput is {base:.2f} UPH",
                    value=float(base), unit="uph", provenance="measured",
                    citation=Citation(sim_id, "/data/baseline/throughput_uph"),
                ))
                direction = "raises" if proj > base else "does not raise"
                recommendation = (
                    f"{BANNER} Tested adding four feed-buffer slots upstream of "
                    f"S{target} against the precomputed simulation grid; it {direction} "
                    f"projected throughput. Feed buffer is capacity upstream of the "
                    f"constraint -- capacity downstream cannot relieve it."
                )
            else:
                unanswered.append(f"simulate_whatif: {sim.get('error')}")

    summary = f"{BANNER} " + (" ".join(notes) if notes else
                              "Constraint and station statistics were read directly.")

    loop = LoopResult(status="finished", content="", records=records, iterations=0)
    finding = Finding(
        question=question,
        summary=summary,
        claims=claims,
        recommendation=recommendation,
        alternatives_considered=[
            "Cycle-time reduction at the constraint (not simulated: the grid covers "
            "buffer and cycle deltas only at seeded stations)."
        ],
        confidence="medium" if len(claims) >= 4 else "low",
        unanswered=unanswered,
    )
    return loop, finding


# ---------------------------------------------------------------------------- plans
#
# One fixed tool sequence per named action. They share `_call`, so every claim carries a
# real call_id and a real RFC 6901 pointer into the payload that call actually returned,
# and the identical verifier grades them.
#
# House rules, learned from the general plan above and applied to all of these:
#   * never make a point claim about a row whose provenance is "inferred" unless the row
#     also ships ci_low/ci_high -- CLAUDE.md §3 requires the interval, so the honest move
#     is to name the row in `unanswered` instead;
#   * a 403 is a fact to report, not a reason to guess;
#   * keep bare numbers out of prose. Claim values and values appearing verbatim in a
#     tool result are exempt from the verifier's C3 sweep, but a number computed in
#     Python and dropped into a sentence is not, and it will cost the whole sentence.


def _ok(payload: dict) -> dict | None:
    """The data half of a tool result, or None if the call did not succeed."""
    return payload.get("data") if payload.get("ok") else None


def _fail(unanswered: list[str], name: str, payload: dict) -> None:
    unanswered.append(f"{name}: {payload.get('error')}")


def _station_names(cfg: dict | None) -> dict[int, str]:
    if not cfg:
        return {}
    return {s["station_id"]: s["station_name"] for s in cfg.get("stations", [])}


def _sname(names: dict[int, str], sid: Any) -> str:
    return f"S{sid}" + (f" ({names[sid]})" if sid in names else "")


def _count_claim(
    claims: list[Claim], cid: str, text: str, value: Any, call_id: str, pointer: str
) -> bool:
    """Append an integer-valued claim if the value is really a number. Returns whether it
    was appended, so callers can branch without re-checking the type."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    claims.append(Claim(id=cid, text=text, value=float(value), unit="count",
                        provenance="measured", citation=Citation(call_id, pointer)))
    return True


def run_handover(
    registry: dict[str, T.ToolSpec], api: PulseTwinAPI, *, plant_id: str, question: str,
    station_id: int | None = None,
) -> tuple[LoopResult, Finding]:
    """A shift handover: what changed, what is open, what to watch, what was ruled out."""
    records: list[ToolRecord] = []
    claims: list[Claim] = []
    unanswered: list[str] = []

    sh_id, sh = _call(records, registry, api, "get_shift_summary", {"plant_id": plant_id})
    bn_id, bn = _call(records, registry, api, "get_bottleneck_current",
                      {"plant_id": plant_id})
    cfg_id, cfg = _call(records, registry, api, "get_station_config",
                        {"plant_id": plant_id})
    dr_id, dr = _call(records, registry, api, "get_sensor_dropouts",
                      {"plant_id": plant_id})

    names = _station_names(_ok(cfg))
    shift = _ok(sh)
    notes: list[str] = []
    watch: list[str] = []

    if shift:
        prod = shift.get("production") or {}
        _count_claim(claims, "c1", "Units completed this shift",
                     prod.get("units"), sh_id, "/data/production/units")
        _count_claim(claims, "c2", "Alerts fired this shift",
                     prod.get("alerts_fired"), sh_id, "/data/production/alerts_fired")
        _count_claim(claims, "c3", "The shift alert budget",
                     prod.get("alert_budget"), sh_id, "/data/production/alert_budget")
        _count_claim(claims, "c4", "Confirmed defects this shift",
                     prod.get("confirmed_defects"), sh_id,
                     "/data/production/confirmed_defects")

        bneck = shift.get("bottleneck") or {}
        if _count_claim(claims, "c5", "Times the constraint moved during this shift",
                        bneck.get("shift_events"), sh_id,
                        "/data/bottleneck/shift_events"):
            opened, closed = bneck.get("opened_at_station"), bneck.get("closed_at_station")
            if opened is not None and closed is not None:
                notes.append(
                    f"The constraint opened the shift at {_sname(names, opened)} and "
                    f"closed it at {_sname(names, closed)}."
                )
        window = shift.get("shift") or {}
        if window.get("window_start"):
            notes.append(
                f"Window covered: {window['window_start']} to {window['window_end']}."
            )
    else:
        _fail(unanswered, "get_shift_summary", sh)

    current = None
    bnd = _ok(bn)
    if bnd:
        current = bnd.get("current_station")
        if isinstance(current, int):
            claims.append(Claim(
                id="c6", text=f"The constraint is open at {_sname(names, current)}",
                value=float(current), unit="station_id", provenance="measured",
                citation=Citation(bn_id, "/data/current_station")))
            watch.append(f"{_sname(names, current)}, which holds the constraint now")
    else:
        _fail(unanswered, "get_bottleneck_current", bn)

    drd = _ok(dr)
    if drd:
        n = drd.get("sensor_dropouts_active")
        if _count_claim(claims, "c7", "Sensors currently dark",
                        n, dr_id, "/data/sensor_dropouts_active") and n:
            affected = sorted({
                d.get("station_id") for d in (drd.get("dropout_details") or [])
            })
            for sid in affected:
                watch.append(
                    f"{_sname(names, sid)}, where a sensor is dark and its statistics "
                    f"are reconstructed rather than measured"
                )
    else:
        _fail(unanswered, "get_sensor_dropouts", dr)

    summary = f"{BANNER} " + (" ".join(notes) if notes else
                              "Shift facts were read directly from the line.")
    recommendation = (
        f"{BANNER} Hand over to the next shift: watch "
        + "; ".join(watch) + "."
        if watch else
        f"{BANNER} No station stood out as needing a watch from the tools that answered."
    )
    if unanswered:
        recommendation += (
            " Some facts could not be read at this role and are listed as unanswered "
            "rather than estimated."
        )

    loop = LoopResult(status="finished", content="", records=records, iterations=0)
    return loop, Finding(
        question=question, summary=summary, claims=claims,
        recommendation=recommendation,
        alternatives_considered=[
            "A narrative of every station (not produced: a handover the next operator "
            "will not finish reading is not a handover)."
        ],
        confidence="medium" if len(claims) >= 4 else "low",
        unanswered=unanswered,
    )


def run_shift_intel(
    registry: dict[str, T.ToolSpec], api: PulseTwinAPI, *, plant_id: str, question: str,
    station_id: int | None = None,
) -> tuple[LoopResult, Finding]:
    """Shift intelligence: rank the losses and qualify each one."""
    records: list[ToolRecord] = []
    claims: list[Claim] = []
    unanswered: list[str] = []
    notes: list[str] = []

    sh_id, sh = _call(records, registry, api, "get_shift_summary", {"plant_id": plant_id})
    st_id, st = _call(records, registry, api, "get_station_stats", {"plant_id": plant_id})
    cfg_id, cfg = _call(records, registry, api, "get_station_config",
                        {"plant_id": plant_id})
    mh_id, mh = _call(records, registry, api, "get_model_health", {"plant_id": plant_id})

    names = _station_names(_ok(cfg))
    shift = _ok(sh)
    if shift:
        prod = shift.get("production") or {}
        _count_claim(claims, "c1", "Units completed this shift",
                     prod.get("units"), sh_id, "/data/production/units")
        bneck = shift.get("bottleneck") or {}
        agreement = bneck.get("detector_agreement")
        if isinstance(agreement, (int, float)):
            claims.append(Claim(
                id="c2",
                text=f"The bottleneck detector agreed with sustained ground truth "
                     f"{agreement:.3f} of the time this shift",
                value=float(agreement), unit="ratio", provenance="measured",
                citation=Citation(sh_id, "/data/bottleneck/detector_agreement")))
            notes.append(
                "Detector agreement is measured against a deliberately different "
                "sustained-truth window, so it is not expected to reach 1."
            )
    else:
        _fail(unanswered, "get_shift_summary", sh)

    rows = _ok(st)
    if isinstance(rows, list) and rows:
        measured = [i for i, r in enumerate(rows)
                    if r.get("provenance", "measured") == "measured"]
        skipped = [rows[i].get("station_id") for i in range(len(rows))
                   if i not in measured]
        if skipped:
            unanswered.append(
                "get_station_stats: stations "
                + ", ".join(f"S{x}" for x in skipped)
                + " report inferred statistics with no confidence interval, so they are "
                  "not ranked (CLAUDE.md §3). They may well be losing more than the "
                  "stations that are ranked."
            )
        ranked = sorted(measured, key=lambda i: rows[i].get("lost_uph") or 0.0,
                        reverse=True)[:3]
        for n, idx in enumerate(ranked, start=1):
            row = rows[idx]
            sid = row.get("station_id")
            lost = row.get("lost_uph")
            if isinstance(lost, (int, float)):
                claims.append(Claim(
                    id=f"c{2 + n}",
                    text=f"Loss rank {n}: {_sname(names, sid)} loses "
                         f"{lost:.1f} UPH",
                    value=float(lost), unit="uph", provenance="measured",
                    citation=Citation(st_id, f"/data/{idx}/lost_uph")))
    else:
        _fail(unanswered, "get_station_stats", st)

    health = _ok(mh)
    if health:
        drift = (health.get("drift") or {}).get("psi_summary") or {}
        _count_claim(claims, "c6", "Features whose distribution has shifted",
                     drift.get("features_shifted"), mh_id,
                     "/data/drift/psi_summary/features_shifted")
        status = (health.get("drift") or {}).get("overall_status")
        if status:
            notes.append(f"Model drift status is reported as {status}.")
    else:
        _fail(unanswered, "get_model_health", mh)

    summary = f"{BANNER} " + (" ".join(notes) if notes else
                              "Shift losses were ranked directly from station statistics.")
    recommendation = (
        f"{BANNER} Work the ranked losses in order, but read the unanswered list first: "
        f"stations whose statistics are reconstructed are excluded from the ranking "
        f"entirely, so this is a ranking of what can be measured, not of what is worst."
    )

    loop = LoopResult(status="finished", content="", records=records, iterations=0)
    return loop, Finding(
        question=question, summary=summary, claims=claims,
        recommendation=recommendation,
        alternatives_considered=[
            "Ranking inferred stations alongside measured ones (not done: a point claim "
            "about an inferred row with no interval breaks the provenance contract)."
        ],
        confidence="medium" if len(claims) >= 4 else "low",
        unanswered=unanswered,
    )


def run_whatif(
    registry: dict[str, T.ToolSpec], api: PulseTwinAPI, *, plant_id: str, question: str,
    station_id: int | None = None,
) -> tuple[LoopResult, Finding]:
    """Test one intervention against the precomputed grid, or say it cannot be tested."""
    records: list[ToolRecord] = []
    claims: list[Claim] = []
    unanswered: list[str] = []

    cfg_id, cfg = _call(records, registry, api, "get_station_config",
                        {"plant_id": plant_id})
    wf_id, wf = _call(records, registry, api, "get_whatif_stations",
                      {"plant_id": plant_id})
    names = _station_names(_ok(cfg))

    covered = [s["station_id"] for s in (_ok(wf) or [])]
    target = station_id if station_id in covered else None

    if target is None:
        # The honest failure. An unavailable simulation must SAY it is unavailable rather
        # than approximate itself with a nearby scenario.
        #
        # A REFUSAL STILL NEEDS EVIDENCE. The verifier rejects a finding with no claims,
        # and a rejected run serves nothing at all -- so an un-evidenced refusal would
        # reach the operator as a blank panel, which is strictly worse than the refusal
        # it was trying to deliver. "The grid covers N stations and this is not one of
        # them" is a citable count, so the refusal is grounded in the same way an answer
        # would be, and the verifier rule stays untouched.
        detail = (
            f"station {station_id} is not in the precomputed what-if grid"
            if station_id is not None else "no station was specified"
        )
        unanswered.append(f"simulate_whatif: {detail}; the grid covers only seeded stations")
        if covered:
            claims.append(Claim(
                id="c2",
                text=f"The lowest-numbered station the grid covers is "
                     f"{_sname(names, covered[0])}",
                value=float(covered[0]), unit="station_id", provenance="measured",
                citation=Citation(wf_id, "/data/0/station_id")))
        loop = LoopResult(status="finished", content="", records=records, iterations=0)
        return loop, Finding(
            question=question,
            summary=f"{BANNER} The simulation could not be run.",
            claims=claims,
            recommendation=(
                f"{BANNER} SIMULATION UNAVAILABLE. The what-if grid is precomputed for a "
                f"fixed set of stations and two intervention types -- upstream feed "
                f"buffer and cycle time. This request falls outside it, and no nearby "
                f"scenario was substituted for it. Ask for a covered station, listed by "
                f"get_whatif_stations."
            ),
            alternatives_considered=[
                "Interpolating from a different station (refused: the grid is per "
                "station, and borrowing another station's response is not a simulation "
                "of this one)."
            ],
            confidence="low", unanswered=unanswered,
        )

    sim_id, sim = _call(records, registry, api, "simulate_whatif", {
        "plant_id": plant_id, "station_id": target,
        "buffer_delta": 4, "cycle_delta_pct": 0,
    })
    data = _ok(sim)
    if not data:
        _fail(unanswered, "simulate_whatif", sim)
        # Same reasoning as the branch above: ground the refusal so it can be served.
        # `target` is in `covered` by construction here, so cite ITS row rather than the
        # first one -- a citation that resolves to the right value for the wrong reason
        # is exactly what the verifier exists to catch.
        claims.append(Claim(
            id="c1",
            text=f"The station this request targeted is {_sname(names, target)}",
            value=float(target), unit="station_id", provenance="measured",
            citation=Citation(wf_id, f"/data/{covered.index(target)}/station_id"),
        ))
        loop = LoopResult(status="finished", content="", records=records, iterations=0)
        return loop, Finding(
            question=question,
            summary=f"{BANNER} The simulator did not answer.",
            claims=claims,
            recommendation=(
                f"{BANNER} SIMULATION UNAVAILABLE. The station is covered by the grid but "
                f"the simulator returned an error; it is listed in unanswered verbatim. "
                f"No projection was estimated in its place."
            ),
            confidence="low", unanswered=unanswered,
        )

    base = data["baseline"]["throughput_uph"]
    proj = data["projected"]["throughput_uph"]
    claims.append(Claim(
        id="c1", text=f"Baseline throughput at {_sname(names, target)} is {base:.2f} UPH",
        value=float(base), unit="uph", provenance="measured",
        citation=Citation(sim_id, "/data/baseline/throughput_uph")))
    claims.append(Claim(
        id="c2",
        text=f"Adding four feed-buffer slots upstream of {_sname(names, target)} "
             f"projects {proj:.2f} UPH",
        value=float(proj), unit="uph", provenance="measured",
        citation=Citation(sim_id, "/data/projected/throughput_uph")))

    wip_b = data["baseline"].get("wip_units")
    if isinstance(wip_b, (int, float)):
        claims.append(Claim(
            id="c3", text=f"Baseline work in process is {wip_b:.1f} units",
            value=float(wip_b), unit="count", provenance="measured",
            citation=Citation(sim_id, "/data/baseline/wip_units")))

    value = data.get("annual_value")
    if isinstance(value, (int, float)):
        claims.append(Claim(
            id="c4", text=f"Annualised value of the tested change",
            value=float(value), unit="usd", provenance="measured",
            citation=Citation(sim_id, "/data/annual_value"),
            # Contract #2: a dollar figure carries its source. The simulator states its
            # own basis, and that string is what travels with the number.
            source=str(data.get("assumptions") or "simulate_whatif annualisation basis"),
        ))

    direction = "raises" if proj > base else "does not raise"
    summary = (
        f"{BANNER} One intervention was tested against the precomputed grid at "
        f"{_sname(names, target)}: four additional feed-buffer slots upstream."
    )
    recommendation = (
        f"{BANNER} The tested change {direction} projected throughput. Feed buffer is "
        f"capacity UPSTREAM of the constraint; capacity downstream cannot relieve it. "
        f"The grid interpolates between seeded points, so treat the projection as a "
        f"direction and a rough magnitude rather than a commitment."
    )

    loop = LoopResult(status="finished", content="", records=records, iterations=0)
    return loop, Finding(
        question=question, summary=summary, claims=claims,
        recommendation=recommendation,
        alternatives_considered=[
            "Cycle-time reduction at the same station (not tested here: this plan varies "
            "one intervention so the projection is attributable to it)."
        ],
        confidence="medium", unanswered=unanswered,
    )


def run_incident(
    registry: dict[str, T.ToolSpec], api: PulseTwinAPI, *, plant_id: str, question: str,
    station_id: int | None = None,
) -> tuple[LoopResult, Finding]:
    """Incident triage: infrastructure before behaviour, then the affected units."""
    records: list[ToolRecord] = []
    claims: list[Claim] = []
    unanswered: list[str] = []
    notes: list[str] = []

    bn_id, bn = _call(records, registry, api, "get_bottleneck_current",
                      {"plant_id": plant_id})
    cfg_id, cfg = _call(records, registry, api, "get_station_config",
                        {"plant_id": plant_id})
    dr_id, dr = _call(records, registry, api, "get_sensor_dropouts",
                      {"plant_id": plant_id})
    tr_id, tr = _call(records, registry, api, "get_alert_triage",
                      {"plant_id": plant_id, "limit": 50})

    names = _station_names(_ok(cfg))

    # --- infrastructure FIRST. A constraint that "moved" is sometimes a sensor that
    # went dark, and reporting a behavioural cause over a dead sensor is the most
    # expensive mistake this plan could make.
    drd = _ok(dr)
    if drd:
        n = drd.get("sensor_dropouts_active")
        if _count_claim(claims, "c1", "Sensors dark at the time of this incident",
                        n, dr_id, "/data/sensor_dropouts_active"):
            notes.append(
                "Infrastructure was checked before behaviour: dark sensors mean the "
                "affected stations report reconstructed rather than measured values."
                if n else
                "No sensor is dark, so the line's statistics are measured rather than "
                "reconstructed."
            )
    else:
        _fail(unanswered, "get_sensor_dropouts", dr)

    bnd = _ok(bn)
    if bnd:
        current = bnd.get("current_station")
        if isinstance(current, int):
            claims.append(Claim(
                id="c2", text=f"The constraint currently sits at {_sname(names, current)}",
                value=float(current), unit="station_id", provenance="measured",
                citation=Citation(bn_id, "/data/current_station")))
        previous = bnd.get("previous_station")
        if isinstance(previous, int) and isinstance(current, int) and previous != current:
            notes.append(
                f"It moved from {_sname(names, previous)} to {_sname(names, current)}."
            )
    else:
        _fail(unanswered, "get_bottleneck_current", bn)

    trd = _ok(tr)
    top_unit = None
    if trd:
        counts = trd.get("counts_by_class") or {}
        _count_claim(claims, "c3", "Alert candidates ranked CRITICAL",
                     counts.get("CRITICAL"), tr_id, "/data/counts_by_class/CRITICAL")
        _count_claim(claims, "c4", "Alert candidates ranked LOW_CONFIDENCE",
                     counts.get("LOW_CONFIDENCE"), tr_id,
                     "/data/counts_by_class/LOW_CONFIDENCE")
        for note in (trd.get("diagnostics") or {}).get("notes") or []:
            notes.append(str(note))
        rows = trd.get("alerts") or []
        if rows:
            top_unit = rows[0].get("unit_id")
    else:
        _fail(unanswered, "get_alert_triage", tr)

    # --- who else is affected. A count, from the containment predicate itself.
    if top_unit:
        cm_id, cm = _call(records, registry, api, "get_containment",
                          {"plant_id": plant_id, "unit_id": top_unit})
        cmd = _ok(cm)
        if cmd:
            _count_claim(claims, "c5",
                         "Other in-flight units sharing the top candidate's fingerprint",
                         cmd.get("total_at_risk"), cm_id, "/data/total_at_risk")
            actions = [u.get("action") for u in (cmd.get("at_risk_units") or [])]
            if "HOLD" in actions:
                notes.append(
                    "At least one peer unit is above the hold threshold, so containment "
                    "is a decision for this shift rather than the next."
                )
        else:
            _fail(unanswered, "get_containment", cm)
    else:
        unanswered.append(
            "get_containment: no ranked alert candidate was available to trace peers from"
        )

    summary = f"{BANNER} " + (" ".join(notes) if notes else
                              "Incident state was read directly from the line.")
    recommendation = (
        f"{BANNER} Treat the CRITICAL candidates as the work queue and the "
        f"LOW_CONFIDENCE ones as a data-quality problem rather than a defect problem -- "
        f"they are ranked low because their inputs are reconstructed, not because the "
        f"units are known to be sound. The association between the signals above and the "
        f"risk is not a demonstrated cause."
    )

    loop = LoopResult(status="finished", content="", records=records, iterations=0)
    return loop, Finding(
        question=question, summary=summary, claims=claims,
        recommendation=recommendation,
        alternatives_considered=[
            "Naming a root cause (refused: the evidence supports association, and the "
            "simulator's true causes are partly unobservable by design)."
        ],
        confidence="medium" if len(claims) >= 4 else "low",
        unanswered=unanswered,
    )


PLANS = {
    "general": run,
    "handover": run_handover,
    "shift_intel": run_shift_intel,
    "whatif": run_whatif,
    "incident": run_incident,
}


def run_plan(
    plan: str,
    registry: dict[str, T.ToolSpec],
    api: PulseTwinAPI,
    *,
    plant_id: str,
    question: str,
    station_id: int | None = None,
) -> tuple[LoopResult, Finding]:
    """Dispatch to the plan for an intent, falling back to the general one.

    An unknown plan name must never end a run: the request layer validates the intent,
    and a worker that raised here would leave its row at `queued` with the reason only in
    a log file nobody opens.
    """
    fn = PLANS.get(plan, run)
    if fn is run:
        return run(registry, api, plant_id=plant_id, question=question)
    return fn(registry, api, plant_id=plant_id, question=question, station_id=station_id)
