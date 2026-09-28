# Live Synthetic Simulation

PulseTwin does not claim to be connected to a real vehicle plant. The Supervisor has two
explicit sources:

- **Recorded Replay** reads the committed, reproducible synthetic shift.
- **Live Synthetic Simulation** creates one server-owned run observed by every browser.

The live mode reuses the committed simulator output as its deterministic base and applies
seeded scenario effects while events are emitted. This preserves the trained model's
documented data-generating process without importing pandas/sklearn or the offline
simulator into the thin runtime API.

## Common event envelope

```json
{
  "run_id": "SIM-20260922T103015-a1b2c3",
  "plant_id": "plant_a",
  "unit_id": "U029618",
  "station_id": 12,
  "event_type": "station_update",
  "source": "live_synthetic",
  "scenario": "torque_degradation",
  "ts": "2026-01-28T06:03:15.120000"
}
```

An MQTT, OPC-UA, Kafka, MES, or QMS adapter can later emit the same envelope with
`source: "real"`. Downstream current-state and alert consumers do not need a new schema.

## Lifecycle API

```text
POST /api/simulations
GET  /api/simulations/active?plant_id=plant_a
GET  /api/simulations/{run_id}
POST /api/simulations/{run_id}/pause
POST /api/simulations/{run_id}/resume
POST /api/simulations/{run_id}/stop
POST /api/simulations/{run_id}/incidents
GET  /api/simulations/{run_id}/stream
GET  /api/simulations/{run_id}/metrics
GET  /api/demo/readiness
```

Supported scenarios are normal production, torque degradation, bad material lot, high
humidity, rushed checklist, sensor dropout, station breakdown, and buffer congestion.
A fixed seed reproduces the same values; changing the seed changes the synthetic
variation. All tabs read the same `simulation_runs`, `simulation_events`,
`unit_current_state`, `station_current_state`, and `active_alerts` state.

The production-shaped stack also supports the same live view through MQTT. The optional
publisher puts a run lifecycle marker and station messages onto the Unified Namespace;
the ingestion service validates readings, then projects station state, alerts, and
inspection outcomes into those same shared PostgreSQL tables. The API and UI do not need
a separate MQTT-only endpoint.

```text
make up-mqtt
PULSETWIN_REQUIRE_FULL_DEMO=1 python scripts/smoke_demo.py
```

Complete readiness is intentionally separate from the backend container health check.
It verifies PostgreSQL, Redis, MQTT, an advancing publisher run, the ingestion projection,
at least one online model score, and the frontend. This avoids a startup cycle where the
frontend waits for the backend while backend health waits for the frontend.

The Supervisor automatically joins the active run and labels it **MQTT live**. Pausing
or stopping that view only disconnects that browser; lifecycle control remains with the
external publisher.

## Temporal truth

Unit and containment endpoints accept `run_id` and `at_ts`. They return only journey
steps/readings available at that event time, calculate the occupied station at that time,
and hide inspection truth until the unit has completed inspection.

## Three-minute demo

1. Run `make up-mqtt`, then `PULSETWIN_REQUIRE_FULL_DEMO=1 python scripts/smoke_demo.py`. Open
   `http://localhost:3000/supervisor` (or the configured `FRONTEND_PORT`, such as `3001`).
2. Select **Live synthetic**. Point out the banner: no real factory data is connected.
3. Stop/disconnect the MQTT feed if you want backend-owned controls. Choose **Torque
   degradation**, seed `42`, speed `10x`, severity `80%`, station `12`, and press
   **Start**. Set **Starts** to `120s` to demonstrate a delayed incident, or `0s` now.
4. Show the shared run ID, simulation clock, line movement, event count, and alert budget.
5. Open an alert. Explain the risk drivers and time-correct journey.
6. In related units, choose **INSPECT**, enter a privacy-safe reason, owner, and due
   station/time, then save it. The system stores both the recommendation and human choice.
7. When that unit reaches inspection, show the persisted `VALIDATED` result, alert
   correctness, decision history, time-to-contain, and sourced synthetic avoided-cost
   estimate. Press **Close alert** to complete the lifecycle.
8. Pause and resume the run to show that the server—not the browser—owns its state.
9. Open a second browser tab and show that it joins the same active run.

## Remaining production boundary

The MQTT path is connected, but its publisher still replays the committed synthetic
artifact. A factory deployment replaces only that publisher with an authenticated
OPC-UA/MES/QMS gateway. Broker TLS, topic ACLs, schema-version governance, dead-letter
handling, and plant-specific connector validation are still required before production.
