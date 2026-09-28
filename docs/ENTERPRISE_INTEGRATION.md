# Enterprise integration (ADR-067)

`serve/action_gateway.py` defines one interface that every adapter implements:
`validate`, `execute`, `get_status`, `rollback` and `request` (the payload).

**Adapter mode.** The adapters in this repository are **reference adapters**
(`adapter_mode: reference_mock`). They validate and shape requests exactly as the target
API expects and return deterministic external references, but they call no external
system: none is configured in this deployment. Every receipt says so, and so does the UI.

| Adapter | Action types | Production target | Endpoint shape a real adapter calls |
|---|---|---|---|
| `cmms` | maintenance_work_order, maintenance_inspection, conveyor_buffer_adjustment, sensor_calibration, manual_inspection, hvac_correction | SAP PM, IBM Maximo, ServiceNow EAM | SAP PM: create a maintenance notification (M1/M2) on the functional location `plant/S<n>`, convert it to an order and schedule it into the planned window. Maximo: `POST /maximo/oslc/os/mxwo` |
| `mes` | line_rebalance | the plant MES (e.g. Siemens Opcenter, Rockwell Plex) | A **change request** that requires engineering sign-off, never a recipe or parameter write |
| `qms` | quality_hold, increase_inspection | QMS (e.g. SAP QM, ETQ) | A hold on named unit IDs, and an inspection-plan change |
| `notification` | notify_supervisor | Microsoft Teams (Graph `POST /teams/{id}/channels/{id}/messages`), e-mail | Role channels only (`shift-supervisors`, `maintenance-planning`, `quality-engineering`), never a named person |

## Request lifecycle

```
ActionRequest   recommendations.options[i]  (governor: HUMAN_APPROVAL_REQUIRED)
ActionApproval  POST /api/recommendations/{id}/approve  -> enterprise_actions (approved)
ActionExecution POST /api/actions/{id}/execute          -> executing -> executed
ActionReceipt   enterprise_actions.external_reference + audit_metadata.receipt
ActionFailure   status=failed, last_error, retry_count (a retry re-executes idempotently)
ActionRollback  POST /api/actions/{id}/rollback  (reversible actions only) -> rollback_reference
```

Each row carries the following fields: action_id, incident_id, recommendation_id,
option_id, plant_id, station_id, action_type, parameters, requested_by, approved_by,
approval_timestamp, risk_level, autonomy_level, adapter, external_reference,
idempotency_key, created_at, executed_at, status, retry_count, rollback_reference,
audit_metadata, predicted and outcome_id.

## Connecting a real system

1. Implement the adapter class with the vendor SDK or REST call inside `execute`. Keep
   `validate` strict: an adapter that sends a request the target would refuse is a bug.
2. Put credentials in environment variables (never in configs), and keep the adapter's
   network egress to that system alone (`configs/deployment.yaml` `network:`).
3. Keep the external reference the vendor returns. Pass the idempotency key as the
   vendor's idempotency or external-ID field (SAP `ExternalReference`, Maximo
   `externalrefid`), so a retry deduplicates on their side as well as ours.
4. Map the vendor's status callbacks or polling into `get_status`.

## Other connections

- **OT historian** (OSIsoft PI / AVEVA, Ignition). It feeds `ingestion/` — the only
  component that crosses the OT/IT boundary (`docs/PRODUCTION_ARCHITECTURE.md`). The
  commander reads only `simulation_events`, the projected stream, so the historian needs no
  commander change.
- **Cloud.** Postgres (Neon) holds every commander table (migration 022). The backend is
  stateless apart from the per-process investigation lock; with more than one worker,
  replace it with a Postgres advisory lock (marked `ponytail:` in the code).
- **Spare parts** are not modelled: no inventory system is connected, and
  `/api/maintenance/windows` says so.
