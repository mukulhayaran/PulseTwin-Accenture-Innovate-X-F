# Operations panel

## Who this panel is for

This page is for the team that keeps PulseTwin itself reliable. It focuses on API and
database health, incoming data, sensors, model drift, audit history, and controlled model
retraining—not on running the assembly line.

It is also the only page designed to demonstrate role-based access live.

## Plant and "Viewing as" controls

The plant picker changes all plant-specific health and model information.

The **Viewing as** selector can switch between:

- Supervisor
- Manager
- Leadership
- System

Panels immediately show either their content or a lock message based on the chosen role.
The API performs the real permission check; the lock card is only a visible mirror of
that server rule.

A fresh direct visit normally begins with Leadership access. If the current role is
already Manager, Leadership, or System, the page keeps it; a Supervisor role is moved to
Leadership so the page is not initially all locks.

## Access summary

| Feature | Minimum role |
|---|---|
| Basic API reachability | Any role |
| System status | Manager |
| Agentic triage | Manager |
| Sensor health | Manager |
| Ingestion lag | Manager |
| Model health | Leadership |
| Drift monitor | Leadership |
| Audit log | Leadership |
| Retraining and model promotion | System |

Changing the selector is a useful demo of least privilege. It is not real sign-in; the
demo role is still sent in a request header.

## System status strip

The top strip summarizes:

- **Operational** or **Degraded** state.
- API uptime.
- Database type, connectivity, response time, and connection use when available.
- Predictions made in the last hour.
- Number of active sensor dropouts.
- Time of the latest prediction.

Any active sensor dropout makes the backend report the overall system as degraded.

Example: `Degraded · postgres · 4.2 ms · 2/20 conns · 2 sensor dropouts` means the API
and database are responding, but sensor input is incomplete.

## Agentic triage panel

This is the compact investigation console used on the Operations page.

### What the user can do

- Type a plain-language question and press Enter or **Investigate**.
- Choose a suggested question generated from the current bottleneck.
- See whether the run uses an external AI **Agent** or the no-LLM **Deterministic** path.
- Watch the list of API tools used during the investigation.
- See the verification verdict, checked-claim count, runtime, and provider cost where
  available.

### What the answer contains

- Short summary.
- Individually cited numeric claims.
- Recommendation.
- Items the system could not determine.
- Notice of any sentence or claim removed by verification.

If support is too weak, the whole finding is discarded instead of showing an answer with
a small warning.

Example question: "The constraint moved from S12 to S19. Check whether this is a process
change or a sensor problem, then recommend the next check."

When the plant changes, this panel also loads the most recent saved run for that plant,
if one exists. Questions must contain at least four non-space characters; shorter input
is rejected by the backend.

The fuller multi-turn version is in the Copilot tab.

## Model health

This panel shows the model currently marked as champion in the registry:

- Champion model ID and registry status.
- Model age.
- Champion PR-AUC.
- Largest calibration deviation.
- Training-data description or range. A committed champion usually shows replay dates;
  a retrained model may show training/validation/test counts instead.
- SPC fallback status with measured shadow precision and recall.
- A backend recommendation such as "no action" or "consider retraining."

The fallback is a simple statistical-process-control model. It exists so scoring can
continue if the main model file is missing or the scorer fails. The panel derives its
fallback label from overall drift state, so **Active** here does not prove that current
predictions are being served by the fallback. The prediction endpoint actually switches
when the primary scorer is unavailable or throws an error.

If a challenger exists, the panel can display a **View comparison report** button. On the
current Operations page that button has no connected click action; the actual comparison
and decision controls are in Retraining console.

## Drift monitor

The drift panel shows the latest saved check, not an invented historical series.

It includes:

- Shifted features out of total features.
- Worst shifted feature and its PSI value.
- PSI band: stable up to 0.1, warning above 0.1, alert above 0.2.
- Latest calibration condition and evaluation window.
- Sensor-coverage status.

In simple terms, PSI asks whether today's input distribution looks different from the
training data. A drift warning means "investigate and compare," not "automatically deploy
a new model."

## Sensor health

This station-by-station grid lists expected sensors:

- Green sensor chip: receiving data normally.
- Amber sensor chip: current dropout.
- Manual label: no continuous sensor is expected; values are inferred.

Click a sensor to see its current condition or how long it has been down. The API does
not expose a full sensor-history series, so the page does not fabricate a sparkline.

Example: `S8 vibration — down since 14:03:12 (128s)` tells the operator exactly which
input is missing and for how long.

## Ingestion lag

This panel measures how far incoming plant events are behind real time. It shows:

- Current lag.
- Mean lag.
- Configured five-second threshold.
- Number of samples.
- **Keeping up** or **Behind** status.

The API supplies the current aggregate reading. The browser stores up to 60 recent polls
to draw the small trend chart. That graph resets after a reload or plant switch and must
not be treated as server-side history.

If the real ingestion service has not run for the selected plant, the panel says that no
telemetry exists instead of inventing zero lag.

## Audit log

The audit table displays the latest 50 recorded actions with:

- Time.
- Acting role.
- Action type.
- Affected resource.

Filters for role and action re-query the backend, so filtering is not limited to whatever
rows happen to be visible in the browser.

Recorded actions include feedback, what-if runs, alert acknowledgement, triage starts,
retraining, model promotion, and rollback. Raw sensor readings and worker identity are
not stored in these audit payloads.

## Retraining console

Only the **System** role can use this panel.

### Normal flow

1. The current champion model is shown.
2. Press **Trigger retrain** to start a detached offline job.
3. The page polls until the job completes or fails.
4. Compare champion and challenger on:
   - PR-AUC.
   - Precision at the operating alert budget.
   - Calibration deviation.
5. Read the generated **PROMOTE** or **REJECT** recommendation.
6. A human must press **Promote challenger** before serving changes.

Promotion is recommended only when PR-AUC is strictly better and neither precision at
budget nor calibration gets worse. Promotion retires the old champion in the registry,
then attempts to hot-swap the runtime scorer and reports whether that swap succeeded. A
missing or invalid artifact can leave the registry update committed while the live swap
reports failure.

### Important distinction

The **Reject** button only dismisses the review in this browser. An unpromoted challenger
already has no effect on live predictions, so there is no backend reject operation to
undo. A challenger left from an older run is shown as a side-by-side comparison without a
reconstructed recommendation.

The backend has a rollback endpoint, but this page currently has no rollback button.

## Floating Copilot

The shared dock uses the same conversation component as the full Copilot page and can
open a thread there. It is intended for Manager or above and remains read-only.

One current UI detail: the dock reads its permission when it mounts. If **Viewing as** is
changed afterward, the dock's visible permission message may not refresh immediately,
although the server still applies the new role and rejects forbidden calls.

## Connectivity and current edge states

- A public health check runs every ten seconds and alone decides whether the whole twin
  is online. Permission errors from a protected panel do not create a false offline
  warning.
- The header freshness indicator is the time of that public health check, not proof that
  every protected panel fetched successfully.
- If the API becomes unreachable, the last loaded data remains visible with an offline
  banner and retry behavior.
- Failure of one protected endpoint can leave a loading skeleton while the page still
  correctly says the overall API is online.
- Audit-fetch failure currently looks like an empty audit table.
- Ingestion-fetch failure can look the same as "no telemetry yet."
- Switching to Supervisor leaves the ungated status-strip shell in a loading state even
  though the protected detail panels correctly show locks.
- Retrain or promote request failures do not yet have a dedicated error panel.

## Main code behind this panel

- Page and role gating: `frontend/app/ops/page.tsx`
- System status: `frontend/components/ops/SystemStatusBar.tsx`
- Model and drift: `frontend/components/ops/ModelHealthPanel.tsx` and `DriftMonitor.tsx`
- Sensors and lag: `frontend/components/ops/SensorHealthGrid.tsx` and `IngestionLagChart.tsx`
- Audit: `frontend/components/ops/AuditLogViewer.tsx`
- Retraining: `frontend/components/ops/RetrainingConsole.tsx`
- Backend health/model/admin routes: `serve/routers/system.py`,
  `serve/routers/model_health.py`, and `serve/routers/admin.py`
