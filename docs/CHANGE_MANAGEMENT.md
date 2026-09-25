# Change Management

Answers "how do you actually get a factory floor to adopt this?" — a technically correct
system that nobody trusts on the floor is a demo, not a deployment. This document is the
adoption plan; `docs/BUSINESS_PROPOSAL.md` §5 has the phase-by-phase rollout this plan
executes against, and §6 has the risk register this plan is largely a mitigation for —
this document does not re-derive either, it operationalises them.

## Stakeholder map

| Stakeholder | Role in the rollout | What they need from this project |
|---|---|---|
| Plant manager | Sponsor, budget owner | The business case (`docs/BUSINESS_PROPOSAL.md` §4) and a rollback plan they can point to when someone asks "what if it's wrong" |
| Floor supervisors | Daily users (`supervisor` role) | A system that reduces false alarms over time, not one more thing that pages them; their feedback thumbs-up/down (`POST /api/feedback`) must visibly change something |
| Union representative | Operator-concern advocate | Written proof — not a verbal assurance — that individual operator performance is never surfaced. CLAUDE.md's operator-privacy contract and `docs/DATA_GOVERNANCE.md`'s anonymisation section are exactly the artifact this stakeholder should be handed, not a summary of them |
| IT security | Approval gate before any network touches the plant | `docs/SECURITY.md` and `infrastructure/kubernetes/network-policy.yaml` — the one-way OT/IT bridge argument has to survive their review on its technical merits, not on trust |
| Maintenance team | Sensor installation (Phase 3 retrofit) | `docs/SCALING_COST_MODEL.md`'s instrumentation costs and `/api/economics/instrumentation-plan`'s ranked retrofit list, so their work is prioritised by measured value, not guesswork |

## Communication plan

- **Before Phase 1 (Shadow):** a town hall on the floor, run by the plant manager with a
  PulseTwin engineer present — not an email. The single message: "this predicts, it does
  not decide, and it cannot stop the line." Read-only architecture (`docs/ARCHITECTURE.md`)
  is the technical fact that message rests on.
- **During every phase:** a weekly written update to the stakeholder list above —
  precision/recall against the alarm budget, false-alarm count, any drift alert
  (`GET /api/model/health`). The same numbers a judge sees in the Ops view are what
  stakeholders see; there is no separate, friendlier internal dashboard with different
  numbers.
- **Standing channel:** an open feedback channel (a physical suggestion point on the floor,
  not only the in-app thumbs-up/down) for concerns the structured feedback loop does not
  capture — union and operator sentiment is not fully observable from `/api/feedback`
  verdicts alone.

## Training plan

| Audience | Duration | Content |
|---|---|---|
| Floor supervisors | 2 hours | Reading the line map and alert cards, containment sweeps, what a HOLD/INSPECT/MONITOR action means, how to submit feedback and why it matters (it is recorded durably and audited, and labels the next offline training build) |
| Production managers | 1 hour | Constraint migration timeline, the what-if console, reading the station league table |
| Plant leadership | 30 minutes | The value bridge, the alarm-budget trade-off (`docs/BUSINESS_PROPOSAL.md` §4's "binding constraint is human attention, not model quality"), the instrumentation planner |
| System/ops team (new in the finals scope) | 2 hours | The Ops view, reading a drift alert, triggering and reviewing a retrain, the incident runbook (`docs/INCIDENT_RUNBOOK.md`) |

## Success criteria per phase

Reusing `docs/BUSINESS_PROPOSAL.md` §5's phase names and exit criteria verbatim — this
document adds the *measurement mechanism* for each, so "exit criterion met" is checkable
rather than asserted:

| Phase | Exit criterion | Measured by |
|---|---|---|
| 1 — Shadow | Measured precision within 20% of modelled; alarm budget tuned against real response capacity | `GET /api/model/health`'s live PR-AUC vs training PR-AUC; supervisor feedback verdicts once Phase 2 begins collecting them |
| 2 — Advisory | Supervisor-rated precision ≥ 30%; containment trialled on ≥ 20 events | `/api/feedback` verdict ratio; count of `POST /api/trace/containment/{unit_id}` calls in the audit log |
| 3 — Integration | Hold decisions traceable end to end; retrofit payback confirmed | `GET /api/audit/log?action=alert_acknowledged` linked to inspection outcomes; `/api/economics/instrumentation-plan`'s payback-months vs actuals |
| 4 — Multi-plant | Second site live with zero pipeline code changes | A new `plant_c.yaml` passing `offline/config_schema.py`'s validators and `make generate && make build-artifact` succeeding unmodified — the same claim `docs/ARCHITECTURE.md` §Multi-plant already makes about Plant B, extended to a genuinely new site |

## Rollback plan

If precision drops below 60% for two consecutive weeks (measured from live supervisor
feedback, not the training-time metric), revert to shadow mode: predictions continue to be
computed and logged but stop being surfaced as actionable alerts, exactly as
`docs/MLOPS_LIFECYCLE.md`'s rollback trigger states. This is an organisational decision
(who is allowed to declare it, and un-declare it). There is no model-rollback endpoint:
retraining and promotion were removed (ADR-053), so the served model only changes with a
new offline build and deploy — this plan covers who decides and how the floor is told.

## Operator engagement

Positioned explicitly as **augmentation, not surveillance**, and the architecture backs
that framing rather than only asserting it: operator identity is never surfaced
individually (CLAUDE.md, `docs/DATA_GOVERNANCE.md`), so there is no mechanism by which this
system COULD be used to rank or discipline an individual operator even if someone wanted
to. What supervisors see instead is their own feedback directly shaping the next model —
a thumbs-down on a false alarm is recorded durably (the `feedback` table) and audited, not
a complaint that disappears into a ticket queue. It is labelled evidence for the next
offline training build; there is no online retrain to feed (ADR-053). Making that loop visible (the audit log
entry, the toast confirmation) is deliberate: `frontend/components/shared/AuditToast.tsx`
exists so the floor can *see* that their input was recorded, not just be told it was.
