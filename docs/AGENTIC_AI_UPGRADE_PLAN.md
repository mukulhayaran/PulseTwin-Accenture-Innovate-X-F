# Agentic AI upgrade plan — Incident Commander (ADR-067)

Written before implementation, after reading the code. Updated at the end with what shipped.

## 1. Current architecture (what already exists and is reused)

| Capability the brief asks for | Already in the repo | Reuse decision |
|---|---|---|
| Telemetry / SENSE | live twin replay (`serve/live_simulation.py`), MQTT projection (`ingestion/`), both land in `simulation_events` | **The commander's evidence source.** One event schema for backend- and edge-produced runs. |
| DETECT | `serve/watch.py` (starvation, silence, paint_env, tool_wear) | Firings open an incident when the run is commander-routed. New: a defect-escape detector at the inspection gate. |
| Specialists | `genai/orchestrator.py` Reliability → Production → Risk → Simulation planner → Synthesizer (fixed order, LLM narrative) | Kept for the `incident` *report* intent. The commander reuses the same four domains as deterministic evidence probes and chooses among them dynamically. |
| Deterministic verification | `genai/verifier.py` | Unchanged. The commander never lets a model write a number: every number is computed by a probe or the simulator. |
| Counterfactual simulation | `offline/sim_experiment.py` + `serve/routers/experiments.py` (paired seeds, bootstrap CIs, catalogue-bounded interventions, budgets) | **The hypothesis-testing engine.** The commander submits experiments through the same validation and budgets. |
| Human approval | `serve/routers/proposals.py` approve/reject | Kept for ADR-051 proposals. Commander recommendations get their own richer decision surface (approve / reject / modify / more investigation). |
| Outcome learning | `serve/outcomes.py` monitoring windows + `incident_learning_records` | Reused as-is: an executed action with a twin effect opens the SAME monitoring window, evaluated by the SAME thresholds. |
| RBAC / audit | `serve/middleware/auth.py`, `audit_log` | Every new mutating route uses `require_role` + `log_action`. |
| Provider fallback | `offline_triage.py`, capability ladder | The commander is deterministic-first; the LLM is an optional *planner* whose plan the server validates. |
| Model health | `drift_metrics`, `model_registry`, fallback scorer | Read by the governor. New: window PSI of live signals vs the recorded baseline. |

## 2. Missing capabilities

1. Stateful investigation with hypotheses, evidence and a plan — nothing persisted beyond a finding.
2. Dynamic specialist selection — the orchestrator's coordinator is a fixed list (ADR-045).
3. Transparent hypothesis scoring and abstention states.
4. Genealogy / temporal root-cause trace across stations.
5. Sensor-maturity-aware confidence caps.
6. Autonomy governor with levels L0–L4 and a hard OT prohibition.
7. Enterprise action gateway (CMMS/MES/QMS/notification adapters, idempotency, rollback).
8. Maintenance-window planning.
9. Cross-plant case memory with hybrid retrieval.
10. Agent-run observability and a reliability scorecard.
11. Business-impact rollup that labels ACTUAL / ESTIMATED / SIMULATED / PROJECTED.
12. Incident workspace UI and a demo launcher that runs the real backend.

## 3. Design

**Where it runs.** The commander is deterministic code in `serve/` (numpy + stdlib — inside
the import wall). The LLM tier stays in `genai/` and never gets imported: `genai/commander.py`
is an optional driver that reads investigation state over HTTP, asks a model for a JSON
plan, and POSTs it to `/continue`, where the server validates it against the same schema,
allowlists and budgets as its own planner. An invalid plan is recorded and replaced by the
deterministic plan (`fallback_used`). No provider key → the deterministic planner runs.

**No new framework.** The state machine is ~10 explicit phases in one module. LangGraph/CrewAI
would add a dependency to the serving image (banned by the import wall) for a graph this small.

**State machine.**
`observe → plan → collect → update → (plan …) → test (awaiting_simulation) → decide → policy → recommended | abstained`

**Hypothesis scoring (transparent, documented).** Mutually exclusive hypotheses, log-odds:
`logit_h = prior_h + Σ_e quality_e · weight_{e,h}`, posterior = softmax. Weights come from
effect sizes (z-scores clipped to ±2) — never from a model. Evidence quality: measured 1.0,
simulated 0.8, inferred 0.6, manual 0.5, missing 0. Confidence is capped by the implicated
station's sensor maturity. Full table in `docs/AGENT_ARCHITECTURE.md`.

**Abstention.** CONFIDENT, LOW_CONFIDENCE, INSUFFICIENT_DATA, CONFLICTING_EVIDENCE,
MODEL_DEGRADED, SENSOR_UNRELIABLE — computed, never chosen by a model.

**Governor.** Per option: required level from the action catalogue, granted level from
assessment, data quality, model health, simulation significance, reversibility, cost and
the caller's role. `plc_write`, `setpoint_change`, `safety_override`, `emergency_stop` are
refused at validation and at the gateway (defence in depth).

## 4. Files

New:
- `configs/agentic.yaml` — budgets, thresholds, autonomy, action catalogue, maintenance windows.
- `serve/agentic_config.py` — the serve-side mirror (import wall: no YAML); a test pins both.
- `serve/incident_evidence.py` — baselines, window statistics, probes, genealogy trace, sensor maturity, PSI.
- `serve/hypotheses.py` — hypothesis catalogue, scoring, status, assessment.
- `serve/governor.py` — autonomy governor.
- `serve/action_gateway.py` — adapter interface, reference adapters, idempotent execution.
- `serve/commander.py` — investigation state, plan schema + validation, planner, state machine, options, maintenance windows, business impact, case memory, agent-run records.
- `serve/routers/incidents.py` — incidents, investigation, recommendations, actions, maintenance windows, case memory, AI-ops metrics, demo scenarios.
- `genai/commander.py` — optional LLM planner driver.
- `migrations/022_incident_commander.sql` (+ SQLite mirror in `serve/db.py`).
- `frontend/app/incidents/page.tsx` + `frontend/components/incidents/*`.
- `tests/test_incident_commander.py`, `tests/test_commander_security.py`.
- Docs: `AGENT_ARCHITECTURE.md`, `AUTONOMY_AND_SAFETY.md`, `INCIDENT_LIFECYCLE.md`,
  `ENTERPRISE_INTEGRATION.md`, `AI_EVALUATION.md`, `GRAND_FINALE_DEMO.md`, ADR-067.

Changed:
- `serve/live_simulation.py` — commander-routed firings open incidents; scenario-induced defects
  for `torque_degradation` (so a delayed defect exists to trace); defect-escape detector;
  executed actions reach the twin through the existing mitigation path.
- `serve/routers/experiments.py` — `_create` split so the commander submits through the same
  validation and budgets without a Request.
- `serve/main.py` — router + background advancer thread.
- `serve/routers/simulations.py` — `commander` flag on run start.
- `frontend/lib/api.ts`, `frontend/lib/routeAccess.ts`, persona pages (links / panels only).

## 5. Data model (migration 022)

`incidents`, `investigations` (full state JSON + version), `investigation_steps`,
`investigation_evidence`, `recommendations` (hypotheses live in the investigation
state, rescored from all evidence on every step: one source),
`autonomy_decisions`, `enterprise_actions` (unique `idempotency_key`), `incident_cases`,
`agent_runs`. Indexed on plant/time and on every foreign key used for a lookup.
Outcomes reuse `action_outcomes`; the action id stands in the `proposal_id` column.

## 6. API

```
POST /api/incidents                                   open (manual)             supervisor
GET  /api/incidents                                   list                       supervisor
GET  /api/incidents/{id}                              incident + investigation   supervisor
POST /api/incidents/{id}/investigate                  start the commander       supervisor
POST /api/incidents/{id}/investigation/continue       one step (optional plan)  manager
GET  /api/incidents/{id}/investigation                state, steps, evidence    supervisor
GET  /api/incidents/{id}/hypotheses                                              supervisor
GET  /api/incidents/{id}/recommendations                                         supervisor
GET  /api/incidents/{id}/similar-cases                                           supervisor
GET  /api/incidents/{id}/trace                        genealogy trace            supervisor
POST /api/recommendations/{id}/approve|reject|modify|request-investigation      manager
POST /api/actions/{id}/execute    GET /api/actions/{id}    POST /api/actions/{id}/rollback  manager
GET  /api/maintenance/windows                                                    manager
GET  /api/ai/operations/metrics   GET /api/ai/operations/runs                    manager
GET  /api/business/impact                                                        leadership
GET  /api/demo/scenarios          POST /api/demo/scenarios/{key}/start           manager
```

## 7. Security implications

- Approval authority is server-side (`require_role("manager")`), re-checked by the governor at
  execution; a supervisor cannot approve, a leadership token cannot execute (execution is an
  operations duty: manager exactly or system).
- Plant isolation: every incident route checks the incident's plant against `plant_id` scope;
  a plan that names a station outside the incident's topology scope is rejected.
- LLM output is untrusted: plan JSON is parsed by pydantic with `extra="forbid"`, agent/probe
  names are checked against allowlists, free text never reaches a tool call, and operator
  notes / case text are quoted as data inside a delimited block in the planner prompt.
- OT: no adapter has a code path that writes to a PLC; prohibited action types are refused
  before any adapter is chosen.

## 8. Phases

P0 (backend core + UI workspace + approval + gateway + audit), P1 (memory, scorecard,
outcomes, maintenance windows, trace, sensor maturity), P2 (impact rollup, demo scenarios,
docs). Everything in P0/P1 ships; section 9 is filled in after implementation.

## 9. What shipped

See `docs/STATE.md` (2026-09-27 entry) and ADR-067 for the ledger, measured behaviour of
each scenario, and the limitations list.
