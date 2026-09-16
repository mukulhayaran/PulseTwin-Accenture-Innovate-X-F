# PulseTwin — Business Proposal

## 1. The problem, with numbers

A mixed-model assembly line building three variants across 30 stations produces a **2.99%
defect rate**. Inspection at the end of line catches 92% of them; the remaining **~10%
escape** into the field as warranty exposure.

The costly part is not the defect rate. It is the **delay between cause and detection**.

A defect created in the paint zone is not discovered until the final quality gate, 14–22
minutes and a dozen stations later. By then 30+ units have passed through identical
conditions. The plant does not learn it has a problem until it already has thirty of them.

### Why existing tooling does not find this

Our simulated line reproduces a failure that station-level monitoring is structurally
blind to:

1. On EV-heavy days the constraint moves from **S12 to S19** — extra battery work turns a
   55-second station into an 82-second one.
2. S19 cannot accept units fast enough, so the **paint zone blocks**. Units sit past their
   cure window.
3. When the block clears, the buffer drains in a burst. An operator at **S15 — a station
   with no continuous sensors at all** — compresses a 45-second checklist to 22 seconds to
   work off the visible queue.
4. Two different defect modes result: **paint micro-blistering** (chemistry) and
   **adhesion failure** (human behaviour). One shared cause, two different root causes.

Every station individually reports normal. S19's utilisation is high but not alarming.
S15 has no sensor data to report at all. **The signal exists only in the relationship
between them**, and only in data spanning both zones.

**Measured evidence this is real, not narrative:** 90% of S15 checklist rushes occur on
EV-heavy days. The behaviour emerges from the simulated physics; it was not scripted.

---

## 2. The solution

A read-only digital twin at ISA-95 Level 3 that:

- **predicts** per-unit defect risk from data available two-thirds of the way down the
  line, leaving 14–22 minutes of lead time;
- **explains** each alert in floor language with exact additive contributions;
- **contains** — given one flagged unit, finds every other in-flight unit sharing its
  causal fingerprint;
- **infers** what unsensored stations would have read, with confidence intervals and a
  visible provenance badge;
- **locates the constraint** and tracks its migration at 86% accuracy.

The twin **never writes to a production system**. It reads a historian replica and
publishes advice to humans.

---

## 3. Three stakeholders, three jobs

| Persona | Job to be done | What they get |
|---|---|---|
| **Line supervisor** | "Should I stop this unit, right now?" | Live line map, ranked alerts with root causes in plain language, containment list with HOLD/INSPECT/MONITOR, alert budget counter |
| **Production manager** | "Where is my line actually losing throughput, and what should I change?" | Constraint migration timeline, station league table by lost UPH, root-cause Pareto, what-if console priced in dollars |
| **Plant leadership** | "Is this worth the money, and where does the next dollar go?" | Value bridge with every figure sourced, the alarm-budget trade-off quantified, instrumentation planner ranked by payback |

---

## 4. The business case

### Derived from measurement, not assumption

Every figure traces to either a measured model output or a sourced constant. The catch
rate is **not assumed** — it is the recall measured on a held-out test week at the
operating threshold.

| Input | Value | Source |
|---|---|---|
| Achieved throughput | 53.8 UPH | Simulated, 97.8% of the 55 UPH target |
| Units per year | ~323,000 | 53.8 UPH × 8h × 3 shifts × 250 days |
| Defect rate | 2.52% | Measured, held-out test week |
| Recall at operating threshold | 28% | Measured at ~6 alerts/shift |
| Escape rate | ~10% | Measured inspection miss rate |
| Scrap / rework / warranty | $4,800 / $1,200 / $8,500 | IHS Markit 2024, plant engineering, NHTSA recall data |
| System cost | $350,000 | 2 FTE-years + integration + year-one operation |
| Discount rate | 10% | Automotive manufacturing WACC |

### Sensitivity

Three scenarios, differing only in catch-rate multiplier — pessimistic 0.5× (shadow-mode
ramp, partial adoption), base 1.0× (measured), optimistic 1.35× (post-retrofit retrain).
All three are shown live in the Leadership view; none require re-deriving the model.

### The honest part

**Recall at the operating threshold is 28%.** We catch roughly one defect in four before
the final gate. That is a real limit and we do not hide it:

- Pure cost optimisation says alarm at **p ≥ 0.0065** — a miss costs ~155× a false alarm,
  so the economics say alarm on essentially everything. That is hundreds of alerts per
  shift and **not an operating policy**.
- Human factors research caps a supervisor at 6–8 actionable alerts per shift (Wickens,
  2008). That is **p ≥ 0.41**.
- These differ by two orders of magnitude. No single number satisfies both.

We publish both, operate on the budget, and **price the gap**: doubling the alert budget
takes recall from 28% to 35% — worth **+$3.9M/year against +$119k of response cost**.

That is the single most useful output of this system for a decision-maker. **The binding
constraint on quality here is human attention, not model quality.**

---

## 5. Roadmap

| Phase | Duration | Scope | Exit criterion |
|---|---|---|---|
| **1 — Shadow** | 8 weeks | Predict silently on one line. Zero operator exposure. | Measured precision within 20% of modelled; alarm budget tuned against real response capacity |
| **2 — Advisory** | 12 weeks | Supervisor UI live on one line. Feedback loop on. | Supervisor-rated precision ≥ 30%; containment trialled on ≥ 20 events |
| **3 — Integration** | 16 weeks | MES quality holds, auto-containment lists, Manager + Leadership views, Phase 1 sensor retrofit | Hold decisions traceable end to end; retrofit payback confirmed |
| **4 — Multi-plant** | ongoing | Plant B onboard by config; per-site models; central dashboard | Second site live with zero pipeline code changes |

Shadow mode first is not caution for its own sake: it is what lets the system be evaluated
against real inspection outcomes before anyone is asked to trust it.

---

## 6. Risks and mitigations

| Risk | Why it matters | Mitigation |
|---|---|---|
| **Alarm fatigue** | The fastest way to kill adoption. Ignored alerts are worse than no alerts. | Hard budget of 6/shift enforced server-side; thumbs up/down feeds a live precision estimate; the cost of the budget is quantified rather than hidden |
| **Model drift** | Paint suppliers, tooling and product mix all change. | Weekly PR-AUC with bootstrap CI, PSI on top features, calibration slope. Known weak regime (low-humidity weeks) monitored explicitly |
| **OT security** | A write path into line control is an unacceptable risk. | Read-only by architecture: the artifact DB is opened `mode=ro` so a write raises. No OPC-UA/MQTT client in the serving layer. One-way historian replication |
| **Operator privacy / union acceptance** | Per-operator defect attribution would be a legitimate reason to reject this system, and rightly so. | Operator identity is bucketed at the API boundary and never surfaced individually — enforced in code, not policy. Aggregation is by shift or opaque group only |
| **Over-trust in inferred values** | An inferred reading presented as measured is a safety problem. | Provenance propagates DB → API → UI with a visibly different treatment. Measured masked-station R² is published per signal, including where it is poor |
| **Vendor lock-in** | Plants outlive vendors. | Artifacts are SQLite + numpy + JSONL. The serving layer is five dependencies. The model exports to flat arrays readable without any ML library |
| **Overstated capability** | The fastest way to lose a plant's trust is a number that does not survive scrutiny. | Every headline metric carries a confidence interval; the achievable ceiling is measured (oracle) rather than asserted |

---

## 7. What we deliberately did not claim

The brief targeted PR-AUC 0.75–0.88. We measured **0.256** and are not presenting it as
anything else.

That band is arithmetically incompatible with the brief's own requirement of unobservable
causes. To reach 0.75 at a 3% base rate, roughly 4% of units would need to carry a **70%+
defect rate** — one near-deterministic condition, which is the opposite of a multi-causal
intermittent problem. Reaching it would have required deleting the latent confounders.

Instead we **measure the ceiling**: scoring the simulator's own latent propensity — the
true data-generating probability including the unobservable causes — gives **0.481**. No
model can beat that. We capture **53% of it**.

Similarly, masked-station validation showed soft sensors reconstruct ambient-driven
signals well and **tool-wear-driven signals not at all** (a neighbouring station's torque
correlates 0.06 with the target's). We publish both results. The negative one is more
useful: it tells you which retrofits are unnecessary and which are unavoidable.

A system that quietly reports only its wins cannot be trusted with a quality decision.
