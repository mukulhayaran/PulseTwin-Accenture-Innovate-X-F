# Demo Script — 5 minutes (Round 2)

> **This is the Round 2 script.** For the finals, use `docs/FINALS_DEMO_SCRIPT.md`, which
> assumes the judges have already seen this arc and spends its seven minutes on what the
> production upgrade added — graceful degradation, drift, RBAC, scaling. Act 1 of that
> script is a two-minute compression of this one, so keep this file current: it is where
> the hero unit and the seeded timestamp are recorded.

**Setup**

```bash
make dev-backend      # :7860
make dev-frontend     # :3000
```

Open `http://localhost:3000`. Set plant to **Plant A**, speed to **10×**.

To land on the hero moment quickly, start the stream at the seeded timestamp:

```
http://localhost:7860/api/stream?plant_id=plant_a&speed=10&from_ts=2026-01-28T22:31:34
```

The hero unit alerts ~8 simulated minutes later (~50 s of wall time at 10×).

---

## 0:00 — The line is running

Land on `/supervisor`.

> "Thirty stations, three zones. Green is working, red is blocked, amber is starved."

Point at **S10, S15, S22, S27** — the hatched tiles with the amber `INF` badge.

> "These four have no continuous sensors at all. They're legacy stations you cannot
> retrofit on a running line. Everything we show for them is **inferred**, and the UI
> never lets you forget which is which — that badge follows the value from the database
> through the API to this tile."

Point at the alert counter.

> "Alerts: 2 of 6 this shift. That budget is computed on the server from the replay
> clock — refresh the page and it's still 2 of 6, because the budget belongs to the
> shift, not to your browser tab."

## 0:45 — The cascade builds

> "It's Tuesday. The schedule is 40% EV instead of 15%. EV units need extra battery work
> at S19, which turns a 55-second station into an 82-second station."

Point at the paint zone backing up (S15–S18 going red).

> "S19 can't take units fast enough, so the paint zone blocks. Units are sitting in the
> cure buffer past their window."

## 1:15 — The alert fires

**Unit U030468** — a **SEDAN**.

> "Risk 41%. And look at *why*."

Click the alert → the Unit Tracker opens with two root causes:

```
Station 15 checklist completed in 22s (normal ~45s)        +2.09
Unit spent 598s in the paint buffer (normal ~120s)         +1.52
```

> "Two different defect modes on one unit. The paint dwell is chemistry — the cure window
> was blown. The 22-second checklist is human — the block cleared, a queue of work
> appeared, and the operator compressed a 45-second prep to work it off.
>
> **And this is a sedan.** It isn't an EV. It got damaged by an EV-heavy schedule,
> through a paint buffer it happens to share. No single station metric on this line would
> ever have shown you that."

Point at the lead time: **14 minutes** before it reaches the final gate.

## 1:45 — Containment

Click **Show units ↗**.

> "The same causal fingerprint — same lot, same time window — is on units still in
> flight. HOLD, INSPECT, MONITOR, by risk."

Point at the footer.

> "Operator identity is shown as a group, never an individual. That's a hard rule in the
> code, not a setting — it's the union-acceptance commitment in the business proposal,
> and the containment endpoint enforces it."

## 2:30 — The two thresholds *(the strongest 40 seconds)*

Open `/api/metrics?plant_id=plant_a` or the Leadership panel.

> "The brief says false alarms erode trust. Here's what we found when we actually did the
> arithmetic.
>
> Cost-optimal threshold: **p ≥ 0.0065**. A miss costs about $7,000, an alarm costs $45,
> so the economics say alarm on essentially everything — **431 alerts per shift**.
>
> Human factors research caps a supervisor at 6 to 8. That's **p ≥ 0.41**.
>
> These are two orders of magnitude apart and no single number satisfies both. So we
> publish both, we operate on the budget, and we price the gap: doubling the alert budget
> would take recall from 28% to 35% — **$3.9M of value for $119k of response cost.**
>
> The binding constraint on this system is human attention, not model quality."

## 3:30 — Honest metrics

> "PR-AUC 0.256, confidence interval 0.21 to 0.31.
>
> That's low, and I want to explain it rather than dress it up. We also score the
> **simulator's own latent propensity** — the true data-generating probability, including
> the two causes the model can never observe. That's **0.481**, and no model can beat it.
> We capture **53%** of it.
>
> The brief asked for 0.75 to 0.88. That band is arithmetically incompatible with the
> brief's own requirement of unobservable causes — to reach it, 4% of units would need a
> 70% defect rate, which means one near-deterministic condition, not a multi-causal
> intermittent problem. Hitting 0.85 here would have meant deleting the confounders."

## 4:45 — Close

> "Detection accuracy on the constraint is 86% — scored against
> a deliberately *different* definition than the detector uses, because scoring a
> 15-minute argmax against a 15-minute argmax gives you 100% and means nothing.
>
> Rollout is shadow mode first: predict silently, measure precision against real
> inspection outcomes, tune the alarm budget, and only then put an alert in front of a
> supervisor. The twin never writes to a production system."

---

## If a judge asks

**"Why is PR-AUC only 0.26?"**
Because two of the causes are unobservable by construction, and we measured the ceiling
that creates rather than assuming one. 53% of 0.481. `docs/AUDIT.md` §B34 has the
arithmetic showing the brief's own band is unreachable under its own realism constraints.

**"Isn't the alert precision of 35% too low?"**
One in three alerts is a real defect against a 2.5% base rate — a 14× enrichment on where
a supervisor spends attention. And the alternative is quantified: doubling the budget buys
7 points of recall for $119k.

**"How do I know there's no leakage?"**
`tests/test_no_leakage.py` corrupts every data point after an instant T and asserts that
features for units predicted before T are bit-identical. A feature that reads forward in
time cannot survive that. It also catches the specific trap in `lot_trailing_defect_rate`,
where selecting the trailing window by index rather than by inspection timestamp would
quietly ingest labels from 25–40 minutes in the future.

**"Is the soft sensor real?"**
S9 and S21 are fully instrumented. We hide their sensors, predict them from neighbours and
checklist proxies alone, and report R² and MAE against the readings we withheld.
