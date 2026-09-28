# Supervisor panel

## Who this panel is for

This is the shop-floor action screen. It helps a supervisor see the line, notice a
problem early, open the affected vehicle, find related vehicles, and record a response.

The page uses the **Supervisor** role and starts with the simulation paused. Nothing
moves until the user presses **Start**.

## Header controls

### Plant

Switches the complete view between Plant A and Plant B. Station layout, names, current
events, and unit details all change with the plant.

### Start, Stop, and Restart

- **Start** begins or resumes the event replay.
- **Stop** closes the live stream but keeps the last line state on screen.
- **Restart** goes back to the beginning of the shift and clears the current line state,
  alert feed, selected unit, and containment result.

A deliberate stop is shown as **Paused**, not as a system failure.

### Replay speed

The choices are **1x, 5x, 10x, and 50x**. This changes how quickly the recorded shift is
replayed. At high speed the browser groups events before drawing them, so the screen
does not try to repaint once for every raw event.

### Alert budget

The **Alerts used / maximum** counter compares this shift's alerts with the configured
human-attention budget of six alerts per shift. Its color changes as the budget fills:

- Green: capacity remains.
- Amber: almost full.
- Red: the budget has been reached or exceeded.

The backend attaches the count to replay events. One current limitation is that the
observed count is kept per streaming connection, so a refresh, reconnect, or speed change
can reset the displayed count even though it is still the same simulated shift.

### Clock and connection state

The header shows replay time and one of **Paused**, **Live**, or **Down**. A freshness
indicator shows whether the last data is becoming stale. If the backend or stream fails,
the page keeps the last known state visible and offers retry behavior.

## Line map

The line map groups stations into plant zones such as Body, Paint, and Final Assembly.
Each station tile shows its number, name, latest cycle time, and state.

| Display | Meaning |
|---|---|
| Green | Working normally. |
| Red | Blocked or down. A blocked station cannot pass work forward. |
| Amber | Starved. The station is waiting for work from upstream. |
| Grey | Idle or no replay event has reached it yet. |
| Amber hatch and `INF` | Manual station with no continuous sensors; values are inferred. |
| Amber ring | A configured sensor at that station is currently offline. |

Hovering a tile shows more detail. Clicking a station with a current unit opens that
unit's detail panel.

Example: if S19 is starved, the problem may be upstream. If S19 is working but S20 is
blocked, the queue may be unable to move forward. The map helps the supervisor inspect
the relationship rather than treating one cycle-time number as the whole story.

## Sensor dropout warning

An amber warning appears when a live sensor is dark. It shows the station, sensor name,
start time, and duration. It continues to refresh every ten seconds even while the line
replay is paused because it represents system health, not replay time.

When a sensor is unavailable, the UI clearly says that affected values fall back to an
inferred estimate. It does not silently present the estimate as a measurement.

## Unit detail panel

The panel opens after selecting a station unit, an alert, or a row in Alert triage. It
shows:

- Unit ID, vehicle variant, and lot.
- Calibrated defect risk and its confidence range.
- Remaining lead time before final inspection.
- Whether the alert fired or remained below the attention-budget threshold.
- Ranked factors that pushed risk up or down.
- The station journey already completed by that vehicle, with cycle time at each stop.

The root-cause bars are model contributions for this unit. A red upward factor increased
risk; a blue downward factor reduced it. They explain the prediction but do not, by
themselves, prove physical causation.

### Escalation and acknowledgement

For a unit whose alert fired, the page shows the configured path:

1. Floor supervisor — five-minute response window.
2. Shift manager — fifteen-minute response window.
3. Plant leadership — remains open until acknowledged.

Pressing **Acknowledge** records who acted, the unit, and the time in the audit trail. It
means the alert was received; it does not claim that the defect was fixed. The current
UI highlights level 1 and displays the policy—it is not a live countdown that visually
advances through the levels.

## Containment panel

When a unit is opened, PulseTwin also searches a 30-minute window for up to 12 other
in-flight units related by lot or elevated risk. It reports the shared time/lot/variant
factors as the unit's containment fingerprint. The table shows:

- Related unit ID.
- Current station.
- Risk.
- Shared factors.
- System suggestion such as **MONITOR**, **INSPECT**, or **HOLD**.
- A separate human decision, if a supervisor has recorded one.

Individual worker names or performance are not exposed.

Example: an alerted SUV experienced excessive paint-buffer dwell and a shortened manual
check. The containment result may find other units from the same period with those same
conditions. The supervisor can inspect that group now instead of waiting for each unit to
fail final inspection.

### Manual containment decision

The system suggestion is advice, not an automatic command. Press **Decide** beside a
related unit to choose **MONITOR**, **INSPECT**, or **HOLD** yourself. A short message is
required, for example: “Inspect paint finish at the next quality gate.” Do not enter an
operator name or other personal data. Known operator-ID formats are rejected by the
backend as an additional privacy check.

After saving, the human choice appears beside the system suggestion. They remain
separate on purpose. For example, the system may suggest **MONITOR**, while a supervisor
who sees a physical paint problem can choose **INSPECT** and explain why. The backend
stores the suggestion, human choice, risk, shared factors, message, role, and time. This
creates evidence that can later be compared with the vehicle's actual inspection result
to see whether the recommendation or the manual decision was useful.

Choosing **Update** records a new decision rather than deleting the earlier one, so the
history remains available for later validation. The general audit log records that a
decision was made but deliberately excludes the free-text message.

## Alert triage

This is the "what should I look at first?" table. Fixed backend rules rank every unit
above the economic inspection threshold, including units that did not fit inside the
smaller attention budget.

The classes are:

- **CRITICAL:** strongest reason for immediate attention.
- **INVESTIGATE:** important enough for active investigation.
- **MONITOR:** worth watching but below the higher classes.
- **LOW CONFIDENCE:** input quality is weak, usually because a relevant sensor is dark or
  station statistics are inferred. The row remains visible so a human can overrule it.

The table includes rank, unit, class, severity relative to this plant's own alert
threshold, implicated station, lead time, number of nearby high-risk peers, and an `INF`
badge where evidence is inferred. Hovering a class explains why the rule selected it.

The list waits for the replay to begin, then refreshes about every eight seconds and only
uses events that have happened by the current replay time. Clicking a unit opens the
same detail and containment panels described above.

### Triage versus the alert feed

- **Alert feed:** alerts that actually fired under the operating attention threshold.
- **Alert triage:** a wider candidate list, ranked so the supervisor can see important
  units that the alert budget could not actively push.

This difference makes the human-capacity trade-off visible instead of hiding it.

## Alert feed

The right side keeps the newest 40 alert cards. Each card shows:

- Risk band (**At risk** or **High risk**) and event time.
- Unit, vehicle variant, and lot.
- Current station, cycle time, and exact calibrated risk.
- **Show units**, which opens unit detail and containment.
- **Useful** and **False alarm** feedback buttons.

Feedback is sent to the backend and written to the operational feedback/audit stores; it
does not retrain the model immediately. In the lightweight SQLite demo, it should not be
treated as permanent production storage. The current client also changes the button
before the request finishes and does not distinguish a non-success HTTP response, so the
audit table is the stronger confirmation that the action was stored.

The feed is ordered by time, not by tiny differences in risk. In the committed Plant A
model, most alerting units land on the same model score, so displaying a precise risk
ranking would be misleading.

## Copilot button

The common **Ask the copilot** button is visible, but the Supervisor role cannot start a
run. Opening it explains that Manager access or above is required. This is intentional:
an agent cannot read around the access level of the person using it.

## A practical example

1. Choose Plant A and press **Start** at 10x.
2. Notice a paint-zone station turn red and the alert counter increase.
3. Open the newest alert or the highest triage row.
4. Read the risk range, lead time, and strongest contributing factors.
5. Check the containment list for other units sharing the fingerprint.
6. Press **Decide** for a related unit, choose an action, and add the reason.
7. Acknowledge the alert, then mark it Useful or False alarm after inspection.
8. If an `INF` badge or sensor-dropout warning is present, treat the result with the
   extra uncertainty shown rather than as a direct measurement.

## Current UI details worth knowing

- Closing a unit card currently hides the detail card but can leave its already-loaded
  containment table visible.
- Acknowledgement state is held in the current browser view. Reloading can show the
  button again, and the backend can record a second acknowledgement.
- If station configuration fails to load, the map can remain in its loading state rather
  than showing a specific error.
- An empty containment result hides the panel; it does not show a "no related units"
  message.

## Main code behind this panel

- Page: `frontend/app/supervisor/page.tsx`
- Alert ranking: `frontend/components/supervisor/AlertTriageTable.tsx`
- Dropout warning: `frontend/components/supervisor/SensorDropoutAlert.tsx`
- Escalation display: `frontend/components/supervisor/EscalationChain.tsx`
- Live stream and unit/containment endpoints: `serve/main.py`
- Alert rules and evidence endpoints: `serve/routers/intelligence.py`
- Acknowledgement and escalation policy: `serve/routers/alerts_mgmt.py`
