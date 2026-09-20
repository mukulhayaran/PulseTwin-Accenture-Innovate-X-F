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
