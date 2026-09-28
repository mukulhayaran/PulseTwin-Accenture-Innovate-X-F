# Architecture

> **Scope note.** This document describes the Round 2 prototype: ISA-95 placement, the
> read-only argument, precompute-heavy/serve-thin, and the streaming design. All of it is
> still true. What it does **not** cover is the finals upgrade's production topology —
> PostgreSQL, the MQTT ingestion bridge, the OT/IT network boundary, RBAC and the audit
> trail. That is `docs/PRODUCTION_ARCHITECTURE.md`, which extends this file rather than
> replacing it. Read this one for *why the twin is safe to attach to a plant*, and that
> one for *how it is actually deployed*.

## ISA-95 / Purdue placement

```
 Level 4  ERP / MES                                          (not touched)
 ─────────────────────────────────────────────────────────────────────────
 Level 3  MANUFACTURING OPERATIONS          ← PulseTwin lives here
          ┌──────────────────────────────────────────────┐
          │  Historian replica (read-only)               │
          │        │                                     │
          │        ├─► offline pipeline (batch, nightly) │
          │        │     simulate / featurise / train    │
          │        │     → artifacts (db + npz + jsonl)  │
          │        │                                     │
          │        └─► serve/  FastAPI + numpy + sqlite  │
          │                    │                         │
          │                    └─► dashboards (static)   │
          └──────────────────────────────────────────────┘
                     ▲  READ ONLY. No path writes downward.
 ─────────────────────────────────────────────────────────────────────────
 Level 2  SCADA / line control        OPC-UA, MQTT  → historian
 Level 1  PLCs, sensors
 Level 0  Physical line
```

**The twin never writes to any production system.** It reads from a historian replica and
publishes advice to humans. There is no control path, no setpoint write, and no
interlock. That is an architectural property, not a configuration choice:

- the artifact database is opened with a `file:...?mode=ro` URI, so an accidental write
  raises immediately rather than mutating a committed artifact (ADR-011);
- the only writable store is supervisor feedback, in a separate database outside the
  image;
- nothing in `serve/` imports an OPC-UA, MQTT or PLC client.

**Why read-only.** Operational risk (a bad write stops the line), regulatory traceability
(quality actions must have a human decision-maker), and change management — a read-only
system can be piloted without a safety case, which is what makes shadow-mode rollout
possible at all.

## Precompute heavy, serve thin

```
configs/*.yaml → offline/  (pandas, sklearn, scipy — heavy, batch, offline)
                 mlops/    (same tier: drift detection, retraining, registry)
                    │
                    ▼
              artifacts/   pulsetwin.db 38 MB · model_<plant>.npz · stream_<plant>.jsonl
                    │
                    ▼
                 serve/    fastapi, uvicorn, pydantic, numpy, orjson, stdlib
                           + I/O drivers only: psycopg, redis, jose, prometheus_client,
                             starlette (ASGI base), subprocess (job spawn)
```

`mlops/` sits on the **offline** side of the wall and may use pandas/sklearn freely; it is
run offline (`make drift-check`), never from `serve/`. When `serve/` needs heavy work at
request time — a GenAI triage run — it spawns `python -m genai.triage` as a detached
subprocess rather than importing it (ADR-036). "Precompute heavy, serve thin" extends to
"trigger heavy, don't host it." (Retraining, the other spawned job, was removed: ADR-053.)

The runtime image contains **no model library**. The trained ensemble is flattened to
plain numpy arrays and walked by ~90 lines in `serve/scorer.py`, proven identical to
sklearn across the entire test set to within 1e-6 (`tests/test_scorer_parity.py`).

The import wall is enforced by `tests/test_serve_imports.py`, which parses each file's AST
and checks **first-party imports only** — a transitive check would fail on fastapi's own
dependencies and tempt someone to weaken the test that protects the constraint.

## Data flow at runtime

| Endpoint | Reads | Computes at request time |
|---|---|---|
| `/api/stream` | `stream_<plant>.jsonl` + `stream_index` | nothing — replay with async pacing |
| `/api/units/{id}` | `units`, `unit_factors`, `genealogy`, `station_readings` | nothing |
| `/api/predict/defect` | `model_<plant>.npz` | one ensemble traversal + decision-path attribution |
| `/api/trace/containment` | `units`, `genealogy` | one SQL join |
| `/api/metrics`, `/api/economics/roi` | `metrics`, `economics` | arithmetic only |
| `/api/model/health` | `model_registry`, `drift_metrics` | nothing — `mlops/drift_detector.py` computed it offline |
| `/api/system/health` | `sensor_health`, `model_predictions_log` | latest-status-per-sensor fold, no analytics |
| `/api/audit/log` | `audit_log` | one filtered query |
| `/api/admin/trigger-retrain` | — | spawns a detached subprocess; hosts nothing |

No training, no model fitting, no dataframe library. The finals endpoints hold that line:
`/api/model/health` reports drift it did not compute, and the admin endpoints start work
they do not perform.

## Streaming

Server-sent events, not WebSockets and not polling — one mechanism, and the one that
survives a corporate proxy. Three details matter:

- **`await asyncio.sleep`, never `time.sleep`.** Under `--workers 1` a blocking sleep in
  the stream handler freezes the whole event loop, so a second browser tab would make
  every other endpoint unresponsive. Verified: with a stream held open, `/api/health`,
  `/api/metrics` and `/api/bottleneck/current` all answer in ~330 ms.
- **`X-Accel-Buffering: no`** plus `Cache-Control: no-cache`. Hugging Face Spaces sits
  behind a proxy that buffers streamed responses; without this the stream works perfectly
  on localhost and delivers nothing in the deployed demo.
- **Server-side downsampling above 25×.** At 50× the raw feed is ~1,400 events/second,
  which no browser will render. The client additionally coalesces with
  `requestAnimationFrame`.

## Provenance

Every value carries `measured` or `inferred` from the simulator, through the database,
through the API, to a visibly different UI treatment (amber badge + diagonal hatch).
Inferred values additionally carry `ci_low`/`ci_high` from quantile regression. There is
no code path that renders an inferred number as if it were measured.

## Security posture — stated plainly

**This deployment is a demo, and its posture is not production.** CORS allows all origins,
and while every endpoint now runs through role-based authorisation
(`serve/middleware/auth.py`), the role arrives in an `X-PulseTwin-Role` header that any
caller can set — so this is **authorisation without authentication**. That sits awkwardly
next to the read-only OT argument above, so it is named here rather than left for a
reviewer to find.

What the finals upgrade did add, and what is genuinely enforced today:

- **RBAC** across four roles, checked server-side on every gated endpoint; the UI's
  `RoleGate` mirrors it but does not enforce it.
- **An audit trail** over every mutating action (`migrations/002`,
  `serve/middleware/audit.py`), queryable and restricted to `leadership`/`system`.
- **Per-role rate limiting**, in-memory and therefore single-process only.
- **A one-way OT/IT boundary**: `ingestion` is the only component with MQTT client code,
  and `serve/` cannot import one (the import wall). A network-level policy enforcing it
  was drafted as Kubernetes manifests that were never applied and were removed
  (ADR-054) — on a real plant network that segmentation is a deploy-time requirement.

Production still needs: real authentication (OIDC/JWT — `docs/SECURITY.md` has the swap,
which touches one function body), origin allow-listing instead of `*`, Redis-backed rate
limiting so limits hold across replicas, and the historian replica firewalled to one-way
replication.

## Multi-plant

Feature names are positional (`s3_torque_nm`). A differently configured plant has different stations, zone
boundaries and variants — a genuinely different feature space — so one ensemble cannot
score two plants. Each plant gets its own `model_<plant>.npz` and the scorer selects on
`plant_id` (ADR-006).

What is shared is everything that matters: the same simulator, the same feature
construction, the same training pipeline, the same serving code. Adding a plant is one
YAML file. That is a stronger and more honest claim than one universal model, which no
real multi-site deployment uses anyway.
