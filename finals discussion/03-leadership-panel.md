# Leadership panel

## Who this panel is for

This page turns technical performance into investment decisions. It answers:

- What yearly value could PulseTwin create?
- How sensitive is that value to adoption and model performance?
- Why does the system limit alerts even when more alerts could catch more defects?
- Which new sensor is worth buying, if any?
- What is the safest rollout path?

The page uses the **Leadership** role.

## Header controls

### Plant

Switches all economics, model measurements, and sensor recommendations to the selected
plant.

### Scenario

The three scenarios apply different multipliers to the measured catch rate:

- **Pessimistic:** half of the measured catch rate, representing weak adoption or drift.
- **Base:** the measured test-set catch rate.
- **Optimistic:** a higher catch rate after useful instrumentation and retraining.

This is sensitivity analysis, not three separately trained models.

The page checks general API health every ten seconds, but the economics, plan, and model
metrics reload when the plant or scenario changes. The freshness indicator therefore
does not mean every financial value was recomputed on each health poll.

## Investment summary cards

### Net value

Estimated yearly defect benefits minus yearly alert-response cost for the chosen
scenario. Despite the short label, this tile does **not** subtract the one-time system
investment; that cost is handled separately in ROI, payback, and the value bridge.

### System ROI

Return compared with the implementation and first-year cost.

### Payback

Estimated number of months until the benefits recover the investment.

### Defects caught

Expected defects found per year using achieved production, measured defect rate, and
recall at the operating alert budget. In the current UI this tile stays at the measured
base count when the scenario changes; the scenario multiplier affects economic benefit,
ROI, and payback, but is not applied to this displayed count.

All monetary figures come from measured results plus named assumptions in the economics
configuration. They are not free-form values produced by the UI or Copilot.

## How this is derived

Four facts make the headline number inspectable:

- **Units/year:** achieved UPH multiplied by three shifts and 250 operating days.
- **Defect rate:** measured on the held-out test week.
- **Recall at budget:** share of defects found while respecting the six-alert-per-shift
  operating point.
- **Escape rate:** defects the final inspection misses and that may reach warranty.

Example: if the scenario changes from Base to Pessimistic, the expected catch rate and
benefit fall. Production volume is not secretly increased to make the business case look
better.

## Value bridge

The waterfall-style chart shows scrap avoided, rework avoided, warranty escapes
prevented, negative alarm-response cost, then negative system cost and **NET**.
Hovering a bar displays the contribution and its source. This lets a reviewer see which
assumption drives the result instead of receiving one unexplained ROI percentage.

## Human attention trade-off

This section deliberately shows two different thresholds.

### Cost-optimal threshold

Because missing a defect is much more expensive than checking a false alarm, pure cost
math recommends alerting on a very low probability. That would create hundreds of alerts
and would be unusable by a real supervisor.

### Active operating threshold

PulseTwin instead operates at the human-attention budget, about six alerts per shift. The
card shows precision and recall at that point.

### Decision available

The third card compares the extra value from doubling the alert budget with the added
human response cost. This turns "hire more coverage or accept more missed defects" into a
visible staffing decision.

## Instrumentation ROI planner

This table ranks possible sensor investments using measured ablation and scenario tests.
For each candidate it shows:

- Station and sensor type.
- Installed cost.
- Change in PR-AUC.
- Estimated yearly value.
- Payback period.
- Installation window.
- Recommendation such as **PROCEED**, **REVIEW**, or **DEFER**.

The footer totals investment and estimated yearly value for every returned candidate,
including deferred rows. It is not an "approved spend" total.

A zero-uplift recommendation is meaningful. It can mean the model already receives the
useful information through another signal, so installing that sensor would add cost but
not predictive value.

The backend returns detailed cost sources and defer reasons. The current table displays
the cost number and shortens a defer recommendation to **Defer**; it does not expose the
full source/reason text in the row.

Example: if a manual checklist already captures the strongest evidence at a station,
adding temperature and humidity sensors there may be deferred even when the hardware is
cheap.

## Rollout plan

The page shows a four-stage adoption path:

1. **Shadow mode — 8 weeks:** predict silently and compare with real inspections.
2. **Advisory alerts — 12 weeks:** activate one-line supervisor alerts and feedback.
3. **MES integration — 16 weeks:** support quality holds, automated containment lists,
   manager/leadership views, and the first approved sensor retrofit.
4. **Multi-plant — ongoing:** onboard Plant B using plant configuration and a per-site
   model.

This is a proposed rollout roadmap, not a live project-progress tracker. The important
safety principle is that shadow mode comes first and the twin remains read-only.

## Expandable model metrics

Click **Model metrics — the numbers behind every figure above** to reveal:

- PR-AUC and its 95% confidence interval.
- Oracle ceiling: performance available even to the simulator's true hidden probability.
- Percentage of that ceiling captured by the model.
- ROC-AUC.
- Brier score for probability calibration.
- Bottleneck detection accuracy.
- Forecast skill versus persistence.
- Added PR-AUC from evidence at unsensored stations.

The oracle comparison matters because the simulated defects deliberately contain causes
that no sensor can observe. An unrealistically perfect model would contradict that design.
These are stored test/artifact measurements, not the live drift state shown on the
Operations page.

## Copilot

Leadership can open the shared Copilot dock. A Leadership-started run may use economics
and model-health tools because the server caps the agent to the caller's role rather than
always forcing it to Manager access.

## Loading and failure behavior

- Cards and charts show placeholders while data loads.
- If ROI cannot be loaded, the page does not render made-up financial values.
- Connectivity is checked every ten seconds, with stale/offline state shown globally.
- If ROI fails, the page can remain on its loading skeleton without a specific error
  message. Instrumentation and metrics failures similarly appear as loading or blank
  values.
- A plant or scenario change can briefly show the previous values while the new request
  is running.

## Main code behind this panel

- Page and calculations: `frontend/app/leadership/page.tsx`
- ROI and instrumentation endpoints: `serve/main.py`
- Economic assumptions: `configs/economics.yaml`
- Investment scenarios: `offline/ml/scenarios.py`
- Model measurements and limitations: `docs/MODEL_CARD.md`
