# Manager panel

## Who this panel is for

This page is for a shift or production manager. It moves from "what is happening now?"
to "where are we losing output, why, and what operational change should we test?"

The page uses the **Manager** role.

## Header

The header contains the plant selector, the Manager role label, data-freshness state,
navigation to the other panels, and the shared Copilot button. The backend is checked
every ten seconds; an offline banner appears if it becomes unreachable. The freshness
indicator describes that health check—the charts themselves reload on a plant change and
are not refreshed every ten seconds.

## Top performance cards

### Current constraint

Shows the station currently limiting line throughput and its name.

### Detection accuracy

Shows how often the bottleneck detector agrees with the deliberately stricter "two-hour
sustained" ground truth. This should not be 100%; the detector uses a shorter operational
window while the comparison truth waits for a longer sustained condition.

### Constraint shifts

Counts detected movement between bottleneck stations across the loaded 15-minute
windows.

### Forecast skill

Compares the bottleneck forecast with a simple baseline that assumes the current
bottleneck will stay where it is. A positive value means the forecast added useful skill;
a zero or negative value means persistence was as good or better.

## Constraint migration chart

Every point represents a 15-minute window:

- Green point: detector agreed with the two-hour sustained truth.
- Red point: detector and sustained truth disagreed.
- Purple step line: the sustained-truth bottleneck.

The chart answers whether the constraint is stable or migrating. In the repository's
Plant A scenario, the sustained constraint can move from S12 to S19 during an EV-heavy
period. Seeing the movement over time is more useful than only seeing the current station.

## Station league table

The table shows the 12 stations with the greatest lost throughput. Columns are:

- **Util:** percent of time the station was busy.
- **Blocked:** percent of time it could not send work downstream.
- **Starved:** percent of time it waited for upstream work.
- **Lost UPH:** estimated units per hour lost at that station.

An `INF` badge means utilization was inferred for a manual station rather than measured
from continuous sensors.

Example: a highly utilized station is not automatically the problem. If it is often
starved, the cause may be upstream. If upstream stations are blocked, downstream capacity
may be the issue. The columns need to be read together.

## Root-cause Pareto

This chart shows the eight model inputs whose removal hurts held-out PR-AUC the most. In
plain words, it answers "which signals matter most to the model across the test data?"

It is a global model-importance view, not proof that the first bar caused a particular
vehicle's defect. Use the Supervisor unit factors for an individual prediction.

## What-if simulation

This panel tests a limited, safe operational scenario. It does not change the real or
simulated line; it reads from a precomputed grid of short simulations.

### Controls

- **Station:** selects an eligible constraint or nearby station.
- **Feed buffer:** changes upstream capacity from -2 to +8 slots.
- **Cycle time:** changes the selected station's cycle time from -10% to +10%.

Only the two seeded constraints and their neighbors are offered. The grid contains about
90 scenarios per plant, not every possible station/change combination. This keeps the
answer grounded in simulations that were actually run.

Results update automatically about 220 milliseconds after the last change. There is no
separate Run button. The first supported station is selected automatically and the
default `+4` buffer, `0%` cycle-time scenario runs as the page loads.

### Results

The panel compares **Baseline** with **Projected** for:

- Throughput in UPH.
- WIP units.
- Bottleneck station.
- Change in throughput.
- Estimated annual value.
- Interpolation mode, bottleneck-selection mode, and model assumptions.

The bottleneck station is chosen as a category; it is never mathematically averaged
between two station IDs.

Every simulation call is added to the audit log. This matters because the result may
support an operational or investment decision even though it does not change the line.

### Example

Suppose S19 is the current constraint:

1. Select S19.
2. Increase the **feed buffer** by four slots.
3. Leave cycle time at 0% and read the projected UPH and WIP.
4. Then reduce cycle time by 5% and compare again.
5. If throughput barely moves, the real constraint may have moved elsewhere. The
   projected bottleneck field makes that visible.

Remember that feed buffer means space **upstream** of the chosen station. Adding buffer
after a constraint usually does not help a station that is waiting to receive work.

One committed Plant A example for `+4` feed-buffer slots at S19 moves the simulation from
about 52.7 to 54.1 UPH and moves the projected constraint back to S12. This is an example
from the prepared grid, not a guarantee for a real plant.

## Copilot

The Manager role can use the floating Copilot dock. A conversation started here can be
opened in the full Copilot tab and continued with the same thread. The agent is capped at
Manager access and cannot retrieve Leadership-only economics or model-health information
unless the caller's role permits it.

## Loading and failure behavior

- Empty panels use loading placeholders while data is arriving.
- The page keeps an independent health check, so loss of connectivity is visible even
  when the user is not switching plants.
- A failed bottleneck or metrics request shows unavailable values rather than inventing a
  fallback number.
- Plant changes do not clear every old value first, so the previous plant can appear
  briefly while new requests finish.
- If a what-if request fails, the **Recomputing** badge clears but the previous result can
  remain on screen.
- The visible tables deliberately limit the view to the top 12 stations and top eight
  importance features.

## Main code behind this panel

- Page and charts: `frontend/app/manager/page.tsx`
- Shared API client: `frontend/lib/api.ts`
- Bottleneck, station, metrics, and what-if endpoints: `serve/main.py`
- Prepared simulation grid: `offline/ml/scenarios.py`
- Bottleneck analysis: `offline/ml/bottleneck.py`
