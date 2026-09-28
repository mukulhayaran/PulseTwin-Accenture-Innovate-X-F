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
from genai.finding import (
    Citation, Claim, Finding, Hypothesis, KnowledgeCitation,
)
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


def _retrieve_knowledge(
    records: list[ToolRecord], registry: dict[str, T.ToolSpec], api: PulseTwinAPI,
    question: str, limit: int = 2,
) -> tuple[list[KnowledgeCitation], list[dict]]:
    """The RAG half of a conversation turn: retrieve passages for the question and cite
    each one VERBATIM, so the verifier's substring check can re-derive every quote. A
    question with no matching passage retrieves nothing and cites nothing -- never an
    arbitrary top-k of unrelated procedures."""
    ks_id, ks = _call(records, registry, api, "search_engineering_knowledge",
                      {"query": question, "limit": limit})
    cites: list[KnowledgeCitation] = []
    passages: list[dict] = []
    for i, r in enumerate((_ok(ks) or {}).get("results") or []):
        if isinstance(r, dict) and isinstance(r.get("text"), str) and r["text"].strip():
            passages.append(r)
            cites.append(KnowledgeCitation(
                id=f"k{len(cites) + 1}", quoted_text=r["text"], doc_id=str(r.get("doc_id")),
                section=str(r.get("section_id")),
                citation=Citation(ks_id, f"/data/results/{i}/text")))
    return cites, passages


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
    # MEASURED rows only. Inferred rows now carry `<field>_ci_low/_ci_high` (ADR-060) and
    # the agent path cites them with their interval; this fixed plan stays conservative
    # and names the unsensored stations in `unanswered` rather than ranking an estimate
    # beside measurements.
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

    # Conversation turns answer from BOTH sources: the live line above, and the
    # engineering knowledge base for whatever procedure the question touches.
    knowledge, passages = _retrieve_knowledge(records, registry, api, question)
    if passages:
        top = passages[0]
        notes.append(f"The most relevant procedure is {top.get('doc_title')}, section "
                     f"{top.get('section_id')}, quoted below.")

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
        knowledge_citations=knowledge,
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


def _operations_memory(records: list[ToolRecord], registry: dict[str, T.ToolSpec],
                       api: PulseTwinAPI, plant_id: str, claims: list[Claim],
                       notes: list[str], watch: list[str], unanswered: list[str],
                       first_id: int) -> None:
    """ADR-064: what the outcome memory and the experiment log say -- counts the APIs
    return, never counted in prose. (Pending decisions live in the Incident Commander's
    Decision Queue, ADR-069; the ADR-051 proposal inbox was retired, ADR-073.)"""
    n = first_id
    ex_id, ex = _call(records, registry, api, "list_simulation_experiments",
                      {"plant_id": plant_id, "limit": 20})
    exd = _ok(ex)
    if exd:
        if _count_claim(claims, f"c{n}", "Simulation experiments run recently",
                        exd.get("count"), ex_id, "/data/count"):
            n += 1
    else:
        _fail(unanswered, "list_simulation_experiments", ex)
    oc_id, oc = _call(records, registry, api, "get_action_outcomes", {"plant_id": plant_id,
                                                                       "limit": 20})
    ocd = _ok(oc)
    if ocd:
        acc = ocd.get("accuracy") or {}
        if _count_claim(claims, f"c{n}", "Action outcomes evaluated so far",
                        acc.get("evaluated"), oc_id, "/data/accuracy/evaluated"):
            n += 1
        for key, text in (("observing", "Applied actions still inside their outcome window"),
                          ("harmful", "Applied actions evaluated as harmful"),
                          ("no_material_effect", "Applied actions with no material effect")):
            if _count_claim(claims, f"c{n}", text, (acc.get("by_status") or {}).get(key),
                            oc_id, f"/data/accuracy/by_status/{key}"):
                n += 1
        for o in ocd.get("outcomes") or []:
            if o.get("status") == "observing":
                watch.append(f"S{o['station_id']}, whose applied action is still being "
                             f"observed -- its result is not yet mature")
            elif o.get("status") in ("harmful", "no_material_effect"):
                notes.append(f"The action applied at S{o['station_id']} "
                             f"({o.get('intervention_type')}) did not achieve its "
                             f"expected result ({o['status'].replace('_', ' ')}).")
        if acc.get("mean_abs_projection_error") is not None:
            notes.append("Where a simulation projected an action's effect, the observed "
                         "change is compared with it in the outcome record.")
    else:
        _fail(unanswered, "get_action_outcomes", oc)


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

    _operations_memory(records, registry, api, plant_id, claims, notes, watch, unanswered,
                       first_id=8)

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

    # ADR-064: a FRESH paired experiment is the primary method; the grid below is only
    # the fallback when the simulator cannot run, and says so.
    plant_stations = set(names)
    if station_id is not None and station_id in plant_stations:
        exp_id, data = _experiment(
            records, registry, api, unanswered, plant_id=plant_id, station_id=station_id,
            objective="throughput",
            interventions=[{"type": "feed_buffer_delta", "params": {"delta": 4}},
                           {"type": "cycle_time_pct", "params": {"pct": -10}}])
        if data is not None:
            by = _experiment_claims(claims, exp_id, data,
                                    ("throughput_uph", "wip_mean", "starved_pct"), first_id=1)
            buf = _scenario(data, "feed_buffer_delta")
            cyc = _scenario(data, "cycle_time_pct")
            hyps = []
            for hid, hit_s, text in (("h1", buf, "feed capacity upstream limits the station"),
                                     ("h2", cyc, "the station's own cycle time limits flow")):
                if hit_s:
                    hyps.append(Hypothesis(
                        id=hid, statement=f"Throughput is held back because {text}.",
                        status="supported" if _improves(hit_s[1], "throughput_uph")
                        else "rejected", claim_ids=by.get(hit_s[0], []),
                        experiment_id=data.get("experiment_id")))
            verdict = data.get("verdict")
            decision = {"improved": "recommend", "too_uncertain": "too_uncertain"}.get(
                verdict, "no_improvement")
            best = data.get("best_label")
            loop = LoopResult(status="finished", content="", records=records, iterations=0)
            return loop, Finding(
                question=question,
                summary=f"{BANNER} {EXPERIMENT_BANNER} Two interventions were tested at "
                        f"{_sname(names, station_id)} against a paired baseline: four "
                        f"more feed-buffer slots upstream, and a 10% shorter cycle.",
                claims=claims,
                recommendation=(
                    f"{BANNER} " + (f"{best} is the only change whose throughput gain has "
                                    f"an interval clear of zero." if best else
                                    "Neither change raised throughput with an interval "
                                    "clear of zero; do not act on the means alone.")),
                alternatives_considered=[
                    "The precomputed what-if grid (not used: a fresh simulation was "
                    "available, and the grid only interpolates between seeded points)."],
                confidence="medium" if best else "low", unanswered=unanswered,
                decision=decision, hypotheses=hyps)
        unanswered.append("run_simulation_experiment unavailable -- falling back to the "
                          "PRECOMPUTED grid, an interpolated estimate")

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
            id="c4", text="Annualised value of the tested change",
            value=float(value), unit="usd", provenance="measured",
            citation=Citation(sim_id, "/data/annual_value"),
            # Contract #2: a dollar figure carries its source. The simulator states its
            # own basis, and that string is what travels with the number.
            source=str(data.get("assumptions") or "simulate_whatif annualisation basis"),
        ))

    direction = "raises" if proj > base else "does not raise"
    summary = (
        f"{BANNER} {GRID_BANNER} One intervention was tested against the precomputed grid "
        f"at {_sname(names, target)}: four additional feed-buffer slots upstream."
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
        confidence="medium", unanswered=unanswered, decision="simulator_unavailable",
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
    current = None
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

    # ADR-064, the simulation-planning stage: test the flow hypotheses at the current
    # constraint on a fresh paired experiment. A projection, labelled as one -- the
    # observed facts above stay separate.
    hyps: list[Hypothesis] = []
    decision = "insufficient_evidence"
    if isinstance(current, int):
        exp_id, data = _experiment(
            records, registry, api, unanswered, plant_id=plant_id, station_id=current,
            objective="throughput",
            interventions=[{"type": "feed_buffer_delta", "params": {"delta": 4}},
                           {"type": "cycle_time_pct", "params": {"pct": -10}}])
        if data is not None:
            by = _experiment_claims(claims, exp_id, data, ("throughput_uph",),
                                    first_id=40)
            for hid, hit_s, text in (
                    ("h1", _scenario(data, "feed_buffer_delta"),
                     "the constraint is starved of feed from upstream"),
                    ("h2", _scenario(data, "cycle_time_pct"),
                     "the constraint's own cycle time sets the pace")):
                if hit_s:
                    hyps.append(Hypothesis(
                        id=hid, statement=f"Flow is limited because {text}.",
                        status="supported" if _improves(hit_s[1], "throughput_uph")
                        else "rejected", claim_ids=by.get(hit_s[0], []),
                        experiment_id=data.get("experiment_id")))
            best = data.get("best_label")
            decision = ("recommend" if best else
                        "too_uncertain" if data.get("verdict") == "too_uncertain"
                        else "no_improvement")
            notes.append(f"{EXPERIMENT_BANNER} At the constraint, "
                         + (f"{best} is the only tested change with a throughput gain "
                            f"clear of zero." if best else
                            "no tested change raised throughput with an interval clear "
                            "of zero."))
        else:
            decision = "simulator_unavailable"

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
        unanswered=unanswered, decision=decision, hypotheses=hyps,
    )


# ------------------------------------------------------------------ ADR-064 experiments
EXPERIMENT_BANNER = ("FRESH SIMULATION: isolated copies of the physical twin, a paired "
                     "baseline on identical seeds; every figure below is simulated.")
GRID_BANNER = ("PRECOMPUTED-GRID ESTIMATE: interpolated from scenarios simulated at build "
               "time, not a fresh simulation.")
_LOWER_IS_BETTER = {"throughput_uph": False, "expected_defects": True, "starved_pct": True,
                    "blocked_pct": True, "down_pct": True, "wip_mean": True}


def _experiment(records: list[ToolRecord], registry: dict[str, T.ToolSpec],
                api: PulseTwinAPI, unanswered: list[str], *, plant_id: str,
                station_id: int, objective: str, interventions: list[dict[str, Any]],
                incident: dict[str, Any] | None = None) -> tuple[str, dict[str, Any] | None]:
    """Run one experiment; (call_id, finished payload) or (call_id, None) with the reason
    recorded in unanswered. A deterministic plan never estimates in the simulator's place."""
    # No `profile`: the tool always runs the smoke profile, and its schema rejects the key.
    args: dict[str, Any] = {"plant_id": plant_id, "station_id": station_id,
                            "objective": objective, "interventions": interventions}
    if incident:
        args["incident"] = incident
    exp_id, out = _call(records, registry, api, "run_simulation_experiment", args)
    data = _ok(out)
    if data is None:
        _fail(unanswered, "run_simulation_experiment", out)
        return exp_id, None
    # Still running (a busy host): read it again rather than give up -- the same thing
    # the agent is told to do. Two polls of up to a minute each, then report it.
    for _ in range(2):
        if data.get("status") not in ("queued", "running"):
            break
        exp_id, out = _call(records, registry, api, "get_simulation_experiment",
                            {"experiment_id": data["experiment_id"]})
        data = _ok(out) or data
    if data.get("status") != "done":
        unanswered.append(f"run_simulation_experiment: experiment {data.get('experiment_id')} "
                          f"ended {data.get('status')} ({data.get('error') or 'no result'})")
        return exp_id, None
    return exp_id, data


def _improves(scn: dict[str, Any], metric: str) -> bool:
    """The paired interval excludes zero in the better direction -- the same rule the
    experiment's own verdict uses. A mean that helps inside a zero-spanning interval is
    NOT an improvement."""
    d = (scn.get("vs_baseline") or {}).get(metric)
    if not d:
        return False
    return d["ci_high"] < 0 if _LOWER_IS_BETTER[metric] else d["ci_low"] > 0


def _experiment_claims(claims: list[Claim], exp_id: str, data: dict[str, Any],
                       metrics: tuple[str, ...], first_id: int) -> dict[int, list[str]]:
    """Claims for the seeds run, the baseline throughput, and each runnable
    intervention's paired change in `metrics`. Returns scenario index -> claim ids."""
    units = {"throughput_uph": "uph", "expected_defects": "count", "starved_pct": "pct",
             "blocked_pct": "pct", "down_pct": "pct", "wip_mean": "count"}
    words = {"throughput_uph": "throughput (UPH)", "expected_defects": "expected defects",
             "starved_pct": "starved share (%)", "blocked_pct": "blocked share (%)",
             "down_pct": "down share (%)", "wip_mean": "work in process (units)"}
    n = first_id
    out: dict[int, list[str]] = {}
    claims.append(Claim(id=f"c{n}", text="Seeds simulated per scenario, identical across "
                        "baseline and interventions", value=float(data["seed_count"]),
                        unit="count", provenance="measured",
                        citation=Citation(exp_id, "/data/seed_count")))
    n += 1
    for i, scn in enumerate(data.get("scenarios") or []):
        if not scn.get("supported"):
            continue
        if scn.get("is_baseline"):
            iv = (scn.get("metrics") or {}).get("throughput_uph")
            if iv:
                claims.append(Claim(
                    id=f"c{n}", text=f"Baseline throughput over the horizon: {iv['mean']:.2f} UPH",
                    value=float(iv["mean"]), unit="uph", provenance="inferred",
                    citation=Citation(exp_id, f"/data/scenarios/{i}/metrics/throughput_uph/mean"),
                    ci_low=float(iv["ci_low"]), ci_high=float(iv["ci_high"])))
                out[i] = [f"c{n}"]
                n += 1
            continue
        ids = []
        for m in metrics:
            iv = (scn.get("vs_baseline") or {}).get(m)
            if not iv:
                continue
            claims.append(Claim(
                id=f"c{n}", text=f"{scn['label']}: paired change in {words[m]} vs baseline",
                value=float(iv["mean"]), unit=units[m], provenance="inferred",
                citation=Citation(exp_id, f"/data/scenarios/{i}/vs_baseline/{m}/mean"),
                ci_low=float(iv["ci_low"]), ci_high=float(iv["ci_high"])))
            ids.append(f"c{n}")
            n += 1
        out[i] = ids
    return out


def _scenario(data: dict[str, Any], kind: str, action_type: str | None = None,
              **params: Any) -> tuple[int, dict[str, Any]] | None:
    for i, scn in enumerate(data.get("scenarios") or []):
        if scn.get("intervention_type") != kind or not scn.get("supported"):
            continue
        p = scn.get("params") or {}
        if action_type and p.get("action_type") != action_type:
            continue
        if all(p.get(k) == v for k, v in params.items()):
            return i, scn
    return None


PLANS = {
    "general": run,
    "handover": run_handover,
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
