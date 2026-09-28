# Incident lifecycle (ADR-067)

```
SENSE → DETECT → INVESTIGATE → HYPOTHESISE → TEST IN TWIN → DECIDE → HUMAN / POLICY GATE
      → EXECUTE SAFE ACTION → OBSERVE OUTCOME → LEARN
```

| Stage | Where | Persisted in |
|---|---|---|
| Sense | live twin replay, or MQTT projection → `simulation_events` | `simulation_events` |
| Detect | `serve/watch.py` (starvation, silence, tool wear, paint climate) and the defect-escape detector (binomial excess over the recorded gate rate) | `incidents` (`source` = watch / quality_detector / manual) |
| Investigate | `serve/commander.py` — plan → collect → rescore | `investigations`, `investigation_steps`, `investigation_evidence`, `agent_runs` |
| Hypothesise | `serve/hypotheses.py` | `investigations.state.hypotheses` |
| Test in twin | `offline/sim_experiment.py` via `create_experiment_for` (same limits as the API) | `simulation_experiments`, `simulation_scenarios`, `simulation_scenario_results` |
| Decide | `serve/commander.py::_decide` | `recommendations` |
| Policy gate | `serve/governor.py` | `autonomy_decisions` |
| Human gate | `/api/recommendations/{id}/approve \| reject \| modify \| request-investigation` | `recommendations`, `audit_log`, `incident_cases` |
| Execute | `serve/action_gateway.py` | `enterprise_actions` |
| Observe | the executed action's twin effect → ADR-064 monitoring window | `action_outcomes` (the action id sits in `proposal_id`) |
| Learn | `commander.on_outcome_evaluated` → `case_memory.record_outcome` | `incident_cases` |

## Incident statuses

- **Opening:** `open` → `investigating`.
- **After the decision:** `awaiting_decision` (recommendation proposed), `needs_manual_check`
  (withheld) or `monitoring` (no fault).
- **After a human acts:** `action_approved` → `action_executed` / `monitoring` (twin outcome
  window open).
- **End states:** `resolved` (outcome successful or partial), `outcome_<status>`,
  `recommendation_rejected`.

## Evidence window

| Incident source | Window |
|---|---|
| Operator report | the 15 simulated minutes *before* the report |
| Detector firing | 5 minutes before detection to 10 minutes *after* it. The investigation waits in `awaiting_telemetry` until those 10 minutes exist. |

A detector fires at the onset of a fault. A backward-only window put a +20-point humidity
fault at +1.3 on average, which is why detector-fired incidents look forward.

## Budgets (configs/agentic.yaml)

| Budget | Limit |
|---|---|
| Plan/collect steps | 10 |
| Probe calls | 20 |
| Twin experiments | 2 (the experiment API's own per-run budget of 3 and its concurrency limit of 2 also apply) |
| Wall clock | 900 s |
| LLM planner | 60,000 tokens and $0.50 |

When a budget is exhausted the commander decides with what it has and names the reason in
`stop_reason`.

## Learning, and what it is not

**A case records:**
- the problem signature (kind | zone | station role);
- the symptoms and sensor maturity;
- every hypothesis weighed and the root cause (only when CONFIDENT);
- the options, the twin predictions and the human decision, with its note — including
  rejections;
- the measured outcome, the prediction error, the recovery time and the recurrence count.

**Retrieval** is hybrid and crosses plants. The structured filter is the incident kind. The
score combines zone, station role and plant with TF-IDF cosine over the case text. It
states the important differences (buffer size, sensing, "different plant: re-test in this
plant's twin").

**Operational memory is not model retraining.** A single incident never updates a
model. Retraining stays the governed offline process described in `docs/MLOPS_LIFECYCLE.md`.
