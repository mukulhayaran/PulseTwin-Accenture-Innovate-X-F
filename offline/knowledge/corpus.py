"""The synthetic engineering-knowledge corpus (ADR-046).

Ten short SOP/fault-code documents, written for THIS project's actual simulated
vocabulary -- torque_nm, vibration_mm_s, weld_current_a, paint_buffer_dwell_s, sensor
tiers -- so retrieval surfaces genuinely relevant chunks for a real incident question
instead of generic filler. This is explicitly a SYNTHETIC demo knowledge base, the same
"illustrative, not real company data" posture `docs/BUSINESS_PROPOSAL.md`'s ROI figures
already carry -- never presented as a real OEM's actual procedures.

Pure data module: no I/O, no numpy, nothing that could import outside the wall.
`offline/build_knowledge_index.py` is what turns this into the retrievable artifact.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    section_id: str
    heading: str
    text: str


@dataclass(frozen=True)
class Document:
    doc_id: str
    title: str
    sections: tuple[Section, ...]


CORPUS: tuple[Document, ...] = (
    Document(
        "SOP-01", "Torque Specification & Fastening Verification",
        (
            Section(
                "SOP-01-1", "Scope",
                "Applies to every station whose sensor manifest includes torque_nm. "
                "Covers fastening operations on body and closure joints where a torque "
                "wrench or a servo-driven nutrunner records a peak torque reading per "
                "cycle.",
            ),
            Section(
                "SOP-01-2", "Nominal range and drift",
                "Nominal torque for a standard M8 body fastener is 80-92 Nm. A reading "
                "trending upward across consecutive cycles without a corresponding "
                "process change (new tool, new batch) indicates tool wear or a "
                "calibration drift, not a single bad fastener. Compare the current "
                "torque_nm reading against the station's own trailing baseline before "
                "concluding a defect risk increase, not against the nominal alone.",
            ),
            Section(
                "SOP-01-3", "Action on sustained drift",
                "If torque_nm drifts more than 8 percent above baseline for three or "
                "more consecutive cycles, flag the station for inspection at the next "
                "scheduled maintenance window. Do not halt the line for a torque drift "
                "alone; verify against vibration_mm_s at the same station first, since "
                "a worn tool typically elevates both signals together.",
            ),
        ),
    ),
    Document(
        "SOP-02", "Vibration Monitoring & Equipment Degradation Thresholds",
        (
            Section(
                "SOP-02-1", "Signal definition",
                "vibration_mm_s is the RMS vibration velocity measured at the tool "
                "head or fixture during the active cycle window, sampled continuously "
                "at HIGH-tier stations and periodically at MEDIUM-tier stations.",
            ),
            Section(
                "SOP-02-2", "Degradation pattern",
                "A slow, monotonic rise in vibration_mm_s over days to weeks is the "
                "characteristic signature of bearing wear or fixture looseness. A "
                "sudden step change within one shift is more consistent with a part "
                "batch defect or an operator process change than equipment wear; "
                "check part_batch and shift before assuming the equipment itself has "
                "degraded.",
            ),
            Section(
                "SOP-02-3", "Threshold guidance",
                "Vibration more than 40 percent above a station's 30-day trailing "
                "median warrants an equipment-health inspection. This is guidance for "
                "human review, not an automatic shutdown threshold; production "
                "continues under monitoring unless a quality event is also observed.",
            ),
        ),
    ),
    Document(
        "SOP-03", "Paint Buffer Dwell Time & Paint Quality Procedure",
        (
            Section(
                "SOP-03-1", "Dwell window definition",
                "Paint buffer dwell is measured from the paint-entry station exit to "
                "the paint-booth-entry station entry. This window exists because "
                "paint flash-off quality depends on how long a body sits in the "
                "buffer before entering the booth, not on any single station's cycle "
                "time.",
            ),
            Section(
                "SOP-03-2", "Nominal vs. threshold dwell",
                "Nominal dwell is short, on the order of two minutes under normal "
                "flow. A dwell approaching or exceeding the configured threshold, "
                "several times nominal, indicates upstream congestion is holding "
                "bodies in the buffer long enough to risk paint quality, independent "
                "of any single station's own health.",
            ),
            Section(
                "SOP-03-3", "Ambient sensitivity",
                "Paint-zone signals are strongly ambient-driven: booth temperature "
                "and humidity explain most of the variation in paint-related "
                "readings. Before attributing a paint-quality signal to equipment or "
                "process, rule out an ambient excursion, temperature or humidity "
                "outside the booth's controlled range, first.",
            ),
        ),
    ),
    Document(
        "SOP-04", "Preventive Maintenance Schedule by Sensor Tier",
        (
            Section(
                "SOP-04-1", "Tier-based cadence",
                "HIGH-tier stations with full telemetry are inspected on a "
                "data-driven cadence: an inspection is scheduled when a signal "
                "crosses its guidance threshold, not on a fixed calendar. LOW and "
                "MANUAL-tier stations, which lack continuous telemetry, remain on a "
                "fixed calendar cadence because there is no continuous signal to "
                "trigger an earlier one.",
            ),
            Section(
                "SOP-04-2", "Maintenance window constraint",
                "Any inspection or adjustment at a station that is currently in "
                "production must wait for the next scheduled maintenance window. "
                "Live production systems are not modified outside these windows, "
                "regardless of how confident a diagnosis is; a confident diagnosis "
                "changes the priority of the next window's work, not whether the "
                "window rule applies.",
            ),
        ),
    ),
    Document(
        "SOP-05", "Sensor Calibration Procedure",
        (
            Section(
                "SOP-05-1", "Instrumented stations",
                "HIGH and MEDIUM-tier stations' torque, vibration and weld-current "
                "sensors are recalibrated against a certified reference at each "
                "scheduled maintenance window. A station whose readings are flagged "
                "as newly INFERRED rather than MEASURED should be checked for a "
                "sensor that has gone dark before assuming a real process change.",
            ),
            Section(
                "SOP-05-2", "Manual/checklist stations",
                "LOW and MANUAL-tier stations have no continuous sensor to "
                "calibrate. Their data quality instead depends on checklist "
                "completeness and consistency across operators; variation here is "
                "monitored at the shift or team level, never attributed to an "
                "individual.",
            ),
        ),
    ),
    Document(
        "FC-01", "Fault Code Reference",
        (
            Section(
                "FC-01-1", "FC-101 Torque out of range",
                "FC-101: torque_nm outside the station's configured band for a "
                "single cycle. Likely cause: fastener seating issue or a transient "
                "tool fault. Action: re-verify the specific unit at the next "
                "inspection gate; no line action required for an isolated "
                "occurrence.",
            ),
            Section(
                "FC-01-2", "FC-102 Sustained torque drift",
                "FC-102: torque_nm trending outside baseline across three or more "
                "consecutive cycles. Likely cause: tool wear or calibration drift, "
                "see SOP-01. Action: schedule an equipment inspection at the next "
                "maintenance window.",
            ),
            Section(
                "FC-01-3", "FC-201 Vibration threshold exceeded",
                "FC-201: vibration_mm_s exceeds the 40 percent above baseline "
                "guidance, see SOP-02. Likely cause: bearing wear, fixture "
                "looseness, or a part batch issue. Action: cross-check part_batch "
                "and shift before concluding equipment wear.",
            ),
            Section(
                "FC-01-4", "FC-301 Weld current anomaly",
                "FC-301: weld_current_a outside its validated range. Likely cause: "
                "electrode wear or a material thickness variation in the current "
                "part batch. Action: compare against sibling stations on the same "
                "batch before isolating a single station as the cause.",
            ),
            Section(
                "FC-01-5", "FC-401 Paint buffer dwell exceeded",
                "FC-401: measured paint buffer dwell exceeds the configured "
                "threshold, see SOP-03. Likely cause: upstream congestion, not a "
                "paint-zone equipment fault. Action: trace the upstream bottleneck "
                "rather than inspecting the paint booth itself.",
            ),
        ),
    ),
    Document(
        "SOP-06", "Quality Inspection & Defect Classification Procedure",
        (
            Section(
                "SOP-06-1", "Final inspection gate",
                "Final quality inspection occurs at the last station in the "
                "quality and inspection zone. A defect confirmed here may have "
                "originated many stations earlier; inspection confirms the outcome, "
                "and genealogy tracing back through the affected unit's station "
                "history is required to establish where it likely began.",
            ),
            Section(
                "SOP-06-2", "Confirmed vs. suspected",
                "A defect is confirmed only after the inspection gate records it. "
                "Upstream signals such as torque drift, vibration, or dwell are risk "
                "indicators, not confirmed defects, until inspection outcome is "
                "available; do not describe a risk-flagged unit as defective before "
                "that confirmation.",
            ),
        ),
    ),
    Document(
        "SOP-07", "Model Changeover / Mixed-Model Line Procedure",
        (
            Section(
                "SOP-07-1", "Mixed-model cycle time impact",
                "Different vehicle variants carry different nominal cycle times at "
                "the same station. A station's average cycle time will shift with "
                "the shift's variant mix even with no equipment change; compare "
                "cycle time against the variant-specific nominal, not a single "
                "plant-wide nominal, before concluding a station has slowed down.",
            ),
            Section(
                "SOP-07-2", "Bottleneck migration",
                "The line's constraint station can migrate between variants "
                "because different variants load different stations more heavily. "
                "A constraint that appears to have moved is sometimes the expected "
                "consequence of the shift's variant mix, not a new equipment issue; "
                "check the mix before investigating equipment at the new "
                "constraint.",
            ),
        ),
    ),
    Document(
        "SOP-08", "Manual Station Checklist Standard",
        (
            Section(
                "SOP-08-1", "What a checklist station records",
                "A MANUAL or LOW-tier station has no continuous telemetry; its "
                "record is an operator checklist plus whatever downstream "
                "inspection signals can be attributed back to it. Treat its state "
                "as INFERRED, never MEASURED, and expect a wider confidence "
                "interval than an instrumented station.",
            ),
            Section(
                "SOP-08-2", "Checklist consistency",
                "Checklist completion is tracked at the shift and team level for "
                "consistency review. Individual operator identity or performance is "
                "never surfaced in any report; variation is discussed as a team or "
                "shift pattern only.",
            ),
        ),
    ),
    Document(
        "SOP-09", "Floor Escalation & Containment Cross-Reference",
        (
            Section(
                "SOP-09-1", "When to escalate",
                "A floor supervisor escalates a flagged unit when its risk score "
                "crosses the shift's alert budget threshold and at least one "
                "corroborating signal is present, such as a sustained torque or "
                "vibration trend, an active sensor dropout, or a paint dwell "
                "excursion. A single borderline score with no corroborating signal "
                "is monitored, not escalated.",
            ),
            Section(
                "SOP-09-2", "Containment scope",
                "Containment identifies other in-flight units sharing the same "
                "causal fingerprint as a flagged unit: same station, same time "
                "window, same shared factor. Containment is an association, not a "
                "confirmed defect; affected units are held or inspected, not "
                "automatically scrapped.",
            ),
        ),
    ),
)

__all__ = ["Section", "Document", "CORPUS"]
