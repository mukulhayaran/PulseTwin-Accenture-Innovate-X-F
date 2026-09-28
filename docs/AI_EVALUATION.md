# AI evaluation — how the Incident Commander was checked, and what it got wrong (ADR-067)

## Disclosure first

The evidence weights and thresholds in `serve/incident_evidence.py` were **set while running
the five scenarios below**. This is the same kind of disclosed look ADR-061 records for the
model challenger. The scenario results therefore show the design works on the conditions it
was built against; they do not establish accuracy on unseen faults. No held-out fault set
exists yet, and building one is the first item under "next" below.

## Scenario results (live twin, plant A, deterministic planner, 2026-09-27)

| Scenario | Trigger | Commander result | Governor | Loop closed |
|---|---|---|---|---|
| A bottleneck (buffer congestion at S19) | starvation watch | downstream congestion 83%, CONFIDENT; local degradation rejected (torque normal, S19 blocked 92% vs 0.1% baseline, S20 normal → transfer) | L3 human approval | conveyor adjustment executed; outcome **partially successful** (stalled share 1.00 → 0.56) |
| B delayed defect (torque fault at S23, gate at S30) | defect-escape detector (10/30 vs 8.1% recorded) | defects introduced at S23, 94% raw, **capped at 85%** (MEDIUM sensing); exposure split + case-control | L3 | quality hold + repair work order; outcome **successful** (signal deviation 13.1% → 1.4%) |
| C sensor-poor (rushed checklist at manual S10) | operator report | process deviation leads at 44%, capped at 65% (MANUAL_ONLY); data quality asked first | diagnostic only | **withheld**, manual inspection requested |
| D false alarm (normal run) | operator report | no fault 84% | none | **no_action**, no work order |
| E model drift (high humidity, severity 1.0) | paint watch | climate-control failure 92%, but humidity-excess PSI past its envelope | MODEL_DEGRADED → investigation only | **withheld** |

`tests/test_incident_commander.py` pins every row on synchronously seeded runs (28 tests,
~55 s). Among them:
- equipment degradation, sensor failure, insufficient data, conflicting evidence;
- the twin-evidence rules (supports / contradicts / inconclusive);
- the manager's rejection becoming memory;
- provider-unavailable fallback;
- the security cases;
- the full telemetry → outcome → memory loop.

## Defects found by running it (each one fixed and pinned)

1. **PSI on raw ambient-driven signals** put every normal run at MODEL_DEGRADED (PSI 5.4).
   Fix: stationary drift features, each judged against its recorded normal envelope.
2. **A fixed variant-mix distance** fired on sampling noise. Fix: chi-square with a
   Wilson–Hilferty z.
3. **A twin null result counted against the diagnosis** (+0.00 UPH on every seed). A null
   result is now inconclusive unless another arm recovered something.
4. **A fixed defect-count detector** fired on the plant's ordinary 8.1% gate rate. Fix: a
   binomial excess at α = 0.01 (7 of 30 on plant A).
5. **An absolute-deviation trace** hid a +15% fault under S3's designed −13% torque deficit.
   Fix: a signed case-control contrast, plus the exposure-split test.
6. **Scenario B's first station (S3, the designed Mode C deficit)** made a correct repair
   read as "harmful" to the outcome evaluator. Fix: the demo picks the steadiest torque
   station.
7. **A backward-only evidence window** diluted a fresh fault: a +20-point humidity fault
   read as +1.3. Fix: detector incidents gather 10 minutes of post-detection telemetry.
8. **The evidence window stretched to the run's latest event** when an investigation
   started late. Fix: the window ends at the first event past trigger + 10 min.
9. **A twin projection at S20 was compared with an outcome at S19.** Fix: the prediction
   error is computed only for a same-station experiment.
10. **A resolved incident re-opened on the next defect.** Fix: the watch cooldown also
    applies to incidents.
11. **A hydration mismatch in the workspace** (the session was read during render). Found in
    the browser.
12. **Postgres-only: every investigation failed.** `CAST(actual_defect AS REAL)` and
    `alert_triggered=1` are invalid on BOOLEAN columns; the SQLite tests could not see it.
    Found on the Docker stack. The repo's boolean-SQL guard only scanned one file for three
    columns; it now scans all of `serve/` for every BOOLEAN column and catches `CAST(...)`.
13. **Stale sensor-log rows raised a false "measurement problem"** (24% on a healthy
    station). `sensor_health` is a change log, and the probe now uses the repo's own
    latest-row-per-sensor definition (`_active_dropouts`).
14. **The workspace inherited another page's pinned view role** (e.g. supervisor), which hid
    the manager's controls. It now adopts the signed-in role on mount.

## Honest limitations

- **Twin vs replay.** The replay transform scripts blocked or down states. The physics twin
  models congestion as reduced buffer capacity and does not reproduce S19's blocking, so
  scenario A's twin test is inconclusive and its recorded prediction error is −0.44.
  Treat the twin as trustworthy for maintenance timing and defect exposure where it
  responds (scenario B), not for scripted states.
- **Trace under full exposure.** When nearly every unit through a station is exposed and
  the fault began before the run's inspection history, neither genealogy test can separate
  it. The commander then reports LOW_CONFIDENCE, which is correct.
- **Adjacent stations.** These share exposure timing (S22 and S23 shifted within two
  minutes of each other in one run). The case-control contrast is what separates them.
- **Confidence is not calibrated.** No held-out fault set exists to calibrate against.
- **Induced defects.** A torque fault's escape rate (0.9 × exposure) is a scenario
  assumption (`docs/ASSUMPTIONS.md`, Induced defects).

## Next

- **A held-out fault set:** randomised scenario, station, severity and onset over both
  plants, scored for top-1 root cause, abstention appropriateness and calibration
  (reliability diagram).
- **LLM planner vs deterministic planner** on the same fault set: steps to decision,
  accuracy, and the schema-failure rate. This was not run in this session, because a free
  provider tier allows ~50 requests a day.
