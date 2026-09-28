# Grand finale demo — the Incident Commander (ADR-067)

Everything below runs the production code path. The scenario launcher only starts a
live-twin run in a known physical condition (a real station, a real severity). Detection,
investigation, the twin, the governor and the outcome all happen live. Nothing is scripted
after the start and no result is precomputed.

## Setup (2 minutes before)

```bash
make up            # or: make dev-backend  +  make dev-frontend
# optional LLM planner (otherwise deterministic -- the UI says which):
#   PULSETWIN_COMMANDER_PLANNER=llm  PULSETWIN_GENAI_API_KEY=...
```

Two browser windows (or tabs) side by side:
- **/supervisor** signed in as `supervisor` / `sup2026`: the floor view;
- **/manager** signed in as `manager` / `mgr2026`: the decision view.

The incidents appear on both, from the same backend state (ADR-069). The supervisor sees
what is being investigated and any manual check; the manager decides and executes.

## The strongest arc: scenario A, then B (≈ 6 minutes)

1. **Launch A — Bottleneck** from /supervisor → Active incidents → *Demo preset* (speed 60).
   - Say: *a live run of the plant; S19 will be held by congestion after it.*
2. **Incident creation, about 20 seconds later.** The starvation watch fires and the
   incident appears. The investigation shows *gathering 600 s of post-detection telemetry*.
   - Say: *a detector fires at onset; the commander waits for evidence.*
3. **The commander chooses its specialists.** The steps list the choices and the reasons:
   production first (highest expected information gain, 1.9), then memory, then data
   quality.
   - Point at the reason text: *it picked production because that separates 'S19
     degradation' from 'downstream congestion'.*
4. **Competing hypotheses.** Downstream congestion climbs to about 83%. Expand it to show
   the ✓/✗ evidence: S19 blocked 92% vs 0.1% baseline, S20 normal, torque normal.
   S19 degradation is rejected.
5. **The digital-twin experiment.** The commander reproduces the observed condition in the
   twin and tests the modelled conveyor fix on paired seeds. Read the result honestly: the
   twin does not reproduce scripted blocking, so the test is *inconclusive*. The
   recommendation therefore rests on measured evidence, and the UI says so.
6. **Recommendation and policy.** Open **Why this recommendation?** (every line links to
   stored evidence). Point at the badges: *HUMAN APPROVAL REQUIRED · authority L3 · risk low
   · CMMS · ≈ $2,226 ESTIMATED*. Open Detail on the option to show the maintenance-window
   table: now vs the nightly window vs defer, with sourced costs.
   - On the **Vehicle Timeline**, filter **Incident** and expand a vehicle: its journey
     (SYSTEM), readings (SENSOR vs INFERRED), and an **AI** investigation event that expands
     into observation, evidence, hypotheses and status (ADR-071).
7. **Manager approval.** On /manager the **Decision Queue** shows one Approval Request:
   problem, AI conclusion and confidence, affected vehicles, cost, downtime, and why it
   needs a manager. Click **View Evidence** (the drawer), then **Approve**.
   - The card reports `CMMS workflow created — reference CMMS-WO-…` and the queue empties
     to *No approvals required*.
   - **Decision History** shows the decision, the reference and, later, the outcome.
   - Back on /supervisor, the affected vehicle's incident now reads MANAGER approved →
     SYSTEM sent to CMMS → scheduled → watching whether the fix worked.
8. **Post-action monitoring.** The approved change was applied to the running twin. After
   about 30 simulated minutes the outcome row fills in: *partially successful, stalled
   share 1.00 → 0.56, predicted change +0.00, prediction error −0.44*.
   - Say: *we measure whether it worked and record where the twin was wrong.*
9. **Memory update.** The incident resolves (both views update). Launch A again: the memory step now retrieves
   this case (*similarity 68%, root cause downstream congestion*).
10. **Launch B — Delayed defect.** The gate's defect-escape detector fires on a
    statistically improbable burst (7+ of 30 against the recorded 8.1%). The **genealogy
    trace** shows when the deviation began, when the first defective unit entered, when the
    first defect was observed, and how many in-flight units were exposed. The top option
    is a **quality hold on those units**; confidence is **capped at 85%** by the station's
    MEDIUM sensing.

## Abstention in 90 seconds (C, D, E)

| Scenario | What happens | Say |
|---|---|---|
| **C Sensor-poor** | Data quality goes first ("sensing at S10 is MANUAL_ONLY"). The rushed checklist is found in manual records. Confidence is capped at 65%. **Recommendation withheld**; manual inspection requested. | *It knows what it cannot see.* |
| **D False alarm** | Every probe is within baseline. **No fault 84% → no work order.** | *It refuses to raise a work order the evidence doesn't support.* |
| **E Model drift** | Climate failure leads at 92%, but humidity-excess PSI is past its envelope. **MODEL_DEGRADED → investigation only.** | *High confidence is not permission.* |

## Leadership and trust

Open **Leadership**:
- **Business impact:** every line labelled ACTUAL / SIMULATED / ESTIMATED / PROJECTED /
  NOT_MEASURED. *Investigation time reduced* is NOT_MEASURED, because there is no manual
  baseline.
- **Agent reliability scorecard:** acceptance, abstention, fallback, evidence coverage and
  twin prediction error, each with its n.

## If something goes wrong on stage

| Problem | What to do |
|---|---|
| No incident after 60 s | Check the backend log for `[commander] … opened`. Relaunch the scenario (it stops the old run). |
| Provider down with the LLM planner on | The UI shows the deterministic-mode banner and the investigation finishes. Say so. |
| Experiment slow | The investigation shows `awaiting simulation`. Keep talking through the hypotheses; the twin smoke profile takes ~20–40 s. |
