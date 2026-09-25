# Assumptions

Every economic, physical and behavioural assumption, with its justification. When a judge
asks "where did that number come from?", the answer is here.

## Economic

| Assumption | Value | Basis |
|---|---|---|
| Scrap cost per unit | $4,800 | Industry average, mixed-model automotive body-in-white, IHS Markit 2024 |
| Rework cost per unit | $1,200 | Plant engineering estimate: body/paint rework labour + materials |
| Warranty escape cost | $8,500 | NHTSA recall cost data, averaged across powertrain/body/paint campaigns |
| Supervisor response cost | $45/alarm | 15 min × $180/hr fully loaded US manufacturing supervisor |
| System implementation | $350,000 | 2 FTE-years engineering + historian integration + edge gateway + year-one cloud and support |
| Discount rate | 10% | Typical automotive manufacturing WACC, 2024–2026 |
| Alarm budget | 6 per shift | Wickens (2008), *Multiple Resources and Mental Workload* — operator alert response degrades beyond 6–8 per shift |

**Annualisation basis.** The simulation runs continuously for 28 days to maximise signal
density across shift patterns and ambient cycles. All annual figures scale by a **250-day,
3-shift year**. These two bases are stated separately and never conflated.

**Throughput basis.** Every economic figure uses **achieved** simulated throughput
(53.7 UPH plant A against a 55 UPH target, 45.3 UPH plant B against a 48 UPH target),
never the nominal target. The original brief derived `downtime_cost_per_hour` from a
nominal figure the station layout could not reach. The two achieved figures are read from
`plant_meta` in the committed artifact, not transcribed — see the correction note in
`docs/STATE.md` for why that distinction is now enforced.

**ROI is derived from measured recall**, not an assumed catch rate. The scrap component
applies a 0.55 factor because not every caught defect would have become scrap — a share
would have been reworked, and counting both would double-count.

## Physical

**Cycle times 45–62 s (plant A), 45–70 s (plant B).** Takt at target is 65.45 s and 75 s
respectively. A station slower than takt caps the line below its target and invalidates
every figure derived from it, so the config validator rejects one.

**S19 base cycle 55 s, EV multiplier 1.50.** An EV takes 82.5 s. What decides the
constraint is the *variant-weighted mean*: 60.1 s normally (below S12's 62.1 s) and 66.8 s
on EV-heavy days (above it). At 58 s the normal-day mean would already exceed S12 and the
bottleneck would never shift, deleting the scenario.

**Tool wear 0.1–0.5%/hour, capped at 6%, reset on repair.** The cap represents routine
tool changes and offset compensation, which real lines perform between breakdowns. Without
it, 504 hours of drift would add 150% to cycle times.

**Paint buffers S15–S18 = 2, 2, 2, 4.** Deliberately tight. They are what lets the S19
block propagate back to S15 within a shift. With roomier buffers (3, 4, 3, 8) the cascade
took ~5.5 hours to reach S15 and Mode B fired on 0.03% of units — too rare to learn.

**Mode A dwell threshold 480 s** (4× the 120 s nominal). Brief delays are harmless to cure
chemistry; sustained ones are not. At 180 s the signal fired on 24% of units at low
intensity, which diluted the hazard so far that even a perfect model could not rank it.

## Behavioural — Mode B

**The rush fires on the UNBLOCK transition, not on the blocked state.** This matters and a
manufacturing-literate reviewer will ask.

An operator at a **blocked** station gains nothing by working faster: the downstream buffer
is full, so a finished unit simply waits longer. Rushing while blocked is irrational and
modelling it that way would be indefensible.

What actually happens is the opposite. The block clears, the buffer drains in a burst, a
queue of waiting work becomes visible, and shift-quota pressure compresses the checklist
from ~45 s to 20–25 s for the next 4–7 units. The simulator implements the trigger on the
unblock transition after a block of ≥ 52 s.

**Measured consequence:** S15 rushes on 4.5% of visits, and **90% of those rushes fall on
EV-heavy days** — which is the cascade signature the scenario claims, arising from the
physics rather than being scripted.

**Operator skill** affects torque bias (expert +0.5%, standard 0%, junior −2.5%) and
checklist thoroughness (×0.92 / ×1.00 / ×1.15). Operators rotate within their zone every
60 units.

## Defect model

**Competing risks, not additive logistic:**

```
P(defect) = 1 − (1 − h_lot)(1 − h_noise) ∏ᵢ (1 − hᵢ)
hᵢ = k · wᵢ · intensityᵢ          intensity normalised by exposure, hazard capped at 0.85
```

`k` is a single scalar calibrated by bisection to hit the configured 3% base rate. Its
fitted value and the intensity normalisation constants are written to `metrics.json` on
every run.

*Why not additive logistic:* the modes fire at very different frequencies (Mode A on ~24%
of units at low intensity, Mode C on ~2.7% at high). Under an additive logit the single
intercept needed to hold the rate at 3% sat so low that no individual mode could clear it
alone, and Mode C produced 4 defects out of 759 against a configured weight of 0.20.

**Measured mode shares:** 44.5% / 31.8% / 17.5% against configured weights of 45/35/20,
with 6.6% unattributed — caused by the latent lot confounder and genuinely unpredictable.

**Unobservable causes (deliberate, and never in the feature matrix):**
- `paint_batch_quality` — changes every ~200 units; 30% of batches are from a weaker
  supplier. Gates Mode A.
- `incoming_lot_quality` — varies by lot; depresses torque and excites vibration together,
  which is what makes the Mode C interaction co-occur 3.3× more often than independence
  would predict.

## Inspection and labels

**Detection 92%, false-reject 0.2%, label noise 3% asymmetric.**

`label_noise_rate` is defined as the probability that a genuine detection is **lost** before
reaching the record — misrouted rework, a missed transcription. It is asymmetric on purpose.
A symmetric 3% flip across all units would push ~291 good units per 10,000 into the positive
class and reintroduce exactly the contamination that dropping the false-reject rate removed.

Resulting label quality per 10,000 units: 287 positive labels, of which **6.8% are wrong**.
The brief's original 2% false-reject rate would have made **41%** of positive labels wrong.

## Bottleneck definitions

Two **different** definitions, deliberately:

- **Detection** — argmax of active time (working + down) over a single 15-minute bucket.
  That is what a live rolling window would actually see.
- **Ground truth** — argmax over a 2-hour smoothed window. A bottleneck is a sustained
  property of the line, not a 15-minute snapshot.

Scoring a 15-minute argmax against a 15-minute argmax is circular and returns 100%. The
first build did exactly that; the honest figures on the committed artifact are **85.9%**
(plant A) and **90.2%** (plant B).

These move between builds — an earlier run measured 87.9% / 79.1% with the same detector
and the same ground-truth definition, neither of which was touched. That spread is the
honest character of the measurement on a stochastic simulation, which is why the number is
always quoted against a specific artifact rather than asserted as a property of the method.
**If it ever reads 100%, ground truth has collapsed back onto the detector** (ADR-029).

## Known weaknesses

- Plant B's realised difficulty is much higher than plant A's (oracle PR-AUC 0.075 vs
  0.481). Its Mode B fires on 13.6% of visits, which dilutes the hazard. Reported as
  measured; not retuned to flatter the result.
- The simulation models continuous 24/7 operation, which no real plant runs. Annualisation
  corrects for this but the shift-pattern statistics are optimistic.
- Ambient temperature and humidity follow smooth seasonal-free profiles; real plants see
  weather fronts and HVAC failures.

## Mitigation effects in the twin (ADR-051)

When a person approves an agent's proposed action, `serve/live_simulation.py::MITIGATIONS`
removes a share of the injected scenario's effect from the running simulation. The replay
stream cannot say how far a real HVAC correction or a tip change would move a signal, so
these are **modelled assumptions**, not measurements:

| Action | Scenario it acts on | Share of the effect removed | Scope |
|---|---|---|---|
| `hvac_setpoint` | `high_humidity` | 100% | the whole paint zone |
| `resequence_paint_queue` | `high_humidity` | 100% of the risk contribution only; humidity unchanged | zone |
| `maintenance_ticket` | `torque_degradation`, `station_breakdown` | 100% | the proposal's station |
| `shift_weld_load` | `torque_degradation` | 100% | the proposal's station |
| `increase_feed_buffer` | `buffer_congestion`, `station_breakdown` | 50% of stalled events relieved | the proposal's station |
| `rebalance_cycle` | `buffer_congestion` | 50% of stalled events relieved | the proposal's station |

"EFFECTIVE" on a proposal means the watch saw a full window of post-approval events back
within its threshold. It confirms the twin behaved as these assumptions say; it is not
evidence about a real line.

### In the counterfactual fork (ADR-055)

`offline/counterfactual.py` applies the SAME shares to the physical twin: an incident whose
share is 100% is ended, and otherwise its severity is scaled by `1 − share`. Three more
modelled assumptions sit on top of that:

| Assumption | Value | Why it is an assumption |
|---|---|---|
| `increase_feed_buffer` adds slots to the firing station's OUTPUT buffer (the feed ahead of the downstream station, as the catalogue means it) | `FEED_BUFFER_SLOTS = 4` | matches the deterministic plan's proposal; an LLM agent's own `buffer_delta` arrives after the job has started, so each arm states what it simulated (`simulated_as`) |
| Severity from tool-wear evidence | abs(deviation %) / 22 (the full-wear deficit) | assumes achieved torque falls in proportion to tool health, which is how `tool_health` is modelled |
| Severity from paint evidence | humidity excess over ambient / 22 pts | linear in the incident's own full-severity magnitude |
| Severity from starvation evidence | the stalled share, capped at 0.8 for congestion | a detector still receiving events is not looking at a zero-capacity (deadlocked) buffer |
