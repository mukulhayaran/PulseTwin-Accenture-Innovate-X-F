# Incident Runbook

Specific, actionable procedures — "what does a human actually do at 2 a.m." Each section
names the exact endpoint, table, or UI element involved so a responder is not searching
the codebase mid-incident.

---

## 1. Model drift alert

**Trigger:** `GET /api/model/health` reports `drift.overall_status` as `warning` or
`degraded` (`mlops/drift_detector.py`'s PSI > 0.2 on a majority of top features,
calibration deviation > 15pp, or sensor coverage dropped > 5pp vs training — see
`docs/MLOPS_LIFECYCLE.md` for the exact thresholds).

**Who is notified:** the `system`-role on-call, per `configs/alert_policies.yaml`'s
escalation levels — this is an infrastructure alert, not a floor alert, so it does not go
through the supervisor escalation chain (`EscalationChain.tsx`).

**Decision tree:**

1. Open the Ops view (`/ops`), check `DriftMonitor.tsx` for which feature(s) shifted and
   `ModelHealthPanel.tsx` for calibration status.
2. **Warning only, single feature, PSI 0.1–0.2:** monitor. No action required — this is
   within the range `mlops/drift_detector.py` itself treats as "shift, watch it," not
   "act now."
3. **Degraded (calibration alert, or a majority of tracked features shifted):**
   - There is no retrain to trigger (ADR-053): the training data is a static seed-42
     simulation, so a retrain would reproduce the same model. Treat a degraded verdict as
     a data problem — a sensor dropout, a coverage drop, or (on a live feed) a genuine
     process change — and escalate to the ML engineering team for an offline rebuild
     with new data or features.
4. **While the drift is unresolved:** `serve/main.py`'s `/api/predict/defect` continues to
   serve the current champion — there is no automatic "drift = switch to fallback" wiring
   in this build (only a broken/corrupt model file forces the fallback path, see §4 of
   `docs/ARCHITECTURE.md`'s note and `serve/main.py::predict`'s `except` branch). A human
   decision to force fallback mode during a severe drift event would currently mean
   temporarily removing `artifacts/model_<plant>.npz`, which is a manual, deliberately
   heavy-handed action — documented here as a known gap, not hidden.

---

## 2. Sensor dropout

**Trigger:** `GET /api/system/health`'s `ingestion.sensor_dropouts_active > 0`
(`ingestion/data_quality.py`'s `DataQualityTracker.check_dropouts`: no reading for more
than 3× the station's nominal cycle time).

**Escalation path:** `configs/alert_policies.yaml`'s standard three-level chain —
floor supervisor first (5 min), shift manager if unacknowledged (15 min), plant
leadership if still unacknowledged. Rendered live via `SensorDropoutAlert.tsx` (pulsing
amber border on the affected station tile, plus a line-map banner) and
`EscalationChain.tsx` on the Supervisor view.

**Decision tree:**

1. Identify the affected station/sensor from `dropout_details` (station ID, sensor name,
   duration).
2. **Duration < 5 minutes:** monitor. Transient dropouts (a loose connector, a brief power
   flicker) often self-clear — `ingestion/mqtt_subscriber.py`'s dropout detection clears
   automatically the moment a fresh reading arrives (`tests/test_sensor_dropout.py`'s
   `test_a_later_healthy_row_supersedes_the_dropout` covers exactly this).
3. **Duration > 5 minutes, single sensor:** predictions for units passing that station
   continue but with **reduced confidence** — the UI notes "using last known value" for
   the affected signal. Page maintenance to physically inspect the sensor.
4. **Duration > 5 minutes, multiple sensors at the same station, or a whole zone:**
   escalate immediately to plant leadership regardless of the timer (skip the 15-minute
   wait) — a station-wide dropout suggests a gateway or network failure, not a single
   sensor fault, and the cost of over-escalating a false positive is much lower than the
   cost of an undetected zone-wide blind spot.
5. **Once repaired:** confirm `sensor_dropouts_active` returns to the pre-incident count
   and log the root cause (sensor replaced / gateway rebooted / cable reseated) in the
   maintenance system — this repo's audit log records *that* a dropout occurred, not the
   physical root cause, which lives in the plant's existing maintenance tracking.

---

## 3. Database connection failure

**Trigger:** `GET /api/system/health`'s `database.connected: false`, or the API returning
5xx on any endpoint that queries Postgres.

**Automatic failover:** in the production target (`infrastructure/terraform/main.tf`'s
`aws_db_instance` with `multi_az = true`), RDS fails over to the standby automatically —
typically 60–120 seconds, during which connections fail and should be retried by the
client, not treated as a sustained outage.

**Manual intervention, if failover does not resolve it within 5 minutes:**

1. Check `serve/db_postgres.py::pool_stats()`'s connection-pool metrics
   (`connections_active` / `connections_max`) via `/api/system/health` — a pool
   exhaustion (all connections in use, none failing) is a different problem
   (a slow query or a connection leak) from a genuine database outage, and needs a
   different fix (restart the backend to reset the pool; do not fail over a healthy
   database because of an application-side leak).
2. If Postgres itself is confirmed down (not just unreachable from one pod): the
   documented degraded-read path is `serve/`'s Redis-backed cache of last-known state
   (`docs/DECISIONS.md` ADR-033 — **documented, not built**, in this round; a live
   deployment reaching this point without that cache built would need to fail read
   endpoints with a clear `degraded: true` response rather than a silent 500, which is
   the honest current behaviour to communicate to the floor: "the twin is down," not
   "the twin is lying").
3. `serve/db.py`'s SQLite fallback (`DATABASE_URL` unset) is a **local-development**
   fallback, not a production degraded-mode target — it opens a different, non-replicated
   database and would silently serve stale demo data in a real deployment. Do not treat
   "the backend didn't crash" as "the incident is resolved" if it silently fell back to
   SQLite in production; that indicates `DATABASE_URL` itself was lost (a config/secrets
   problem), which is a separate root cause to chase.

---

## 4. Complete system outage

**Trigger:** the frontend's `OfflineBanner.tsx` is showing on all three (four) persona
views — `/api/health` itself is unreachable, not just one downstream dependency.

**Immediate communication to the floor:** revert to the plant's pre-PulseTwin manual
inspection procedure for the duration of the outage — this system is advisory-only by
architecture (`docs/ARCHITECTURE.md`'s read-only design), so an outage does not stop the
line; it removes early-warning coverage, which is a real but bounded risk the floor's
existing final-gate inspection (92% detection, per `docs/ASSUMPTIONS.md`) still covers.

**Recovery steps:**

1. Check the backend container/pod's health directly (`docker compose logs backend` in
   the demo runtime, or `kubectl logs` against the backend Deployment in a cluster).
2. Common causes, in likelihood order for this codebase: (a) the artifact database file
   is missing or corrupt at startup — `serve/main.py`'s lifespan raises immediately if
   `pulsetwin.db` cannot be opened; (b) `DATABASE_URL` misconfigured, causing every
   query to fail even though the process is "up"; (c) the MQTT broker or Redis being
   unreachable at ingestion startup — ingestion reconnects with exponential backoff and
   logs the gap (per the finals brief's F5 acceptance criteria), but a persistent broker
   outage stops fresh readings from ever reaching Postgres, not the API from serving what
   it already has.
3. Restart the backend. `frontend/components/shared/OfflineBanner.tsx`'s auto-retry
   ticker (15-second interval) reconnects the frontend automatically — no manual browser
   refresh should be necessary once the backend answers `/api/health` again.
4. Confirm recovery: `OfflineBanner` disappears, `StaleDataIndicator` dots return to
   green across all open views.

---

## 5. False alarm spike

**Trigger:** a supervisor-reported run of `false_alarm` verdicts via `/api/feedback`
(watch the ratio in `AuditLogViewer.tsx`, filtered to `action=feedback_submitted`), or a
sudden rise in `alerts_per_shift` against `configs/economics.yaml`'s 6/shift budget.

**Threshold-adjustment procedure:**

1. Confirm this is a genuine precision drop and not a `model_health` drift event already
   covered by §1 — check `GET /api/model/health` first; if drift is already flagged,
   follow §1's procedure instead of re-diagnosing the same root cause twice.
2. If drift is NOT flagged but false alarms are still elevated: the operating threshold
   (`configs/economics.yaml`'s alarm-budget-derived p\*, ADR-004) may need revisiting
   against a genuinely shifted false-alarm cost, e.g. a supervisor headcount change that
   alters `supervisor_response_cost_per_alarm`'s basis. This is a **configuration**
   change (`configs/economics.yaml`), reviewed and approved by plant leadership, not a
   silent code change — every dollar figure in that file requires a `source` field
   (CLAUDE.md's sourced-economics contract), so a threshold change without an updated
   source is itself a review flag.
3. **Root-cause investigation checklist:** (a) has the variant mix shifted (more EV-heavy
   days than the training period)? Check `station_stats`'s variant distribution; (b) has
   a physical process changed at a station contributing heavily to recent alerts — cross-
   reference the root-cause Pareto on the Manager view; (c) is this isolated to one shift
   or team, suggesting a training gap rather than a model problem?
4. **Do not** respond to a false-alarm spike by silently raising the alert threshold to
   make the number look better — that reduces recall on real defects and directly
   contradicts `docs/BUSINESS_PROPOSAL.md` §4's stated trade-off, which this system is
   built to make visible, not hide from a dashboard.
