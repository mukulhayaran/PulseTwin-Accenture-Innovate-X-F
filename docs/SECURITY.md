# Security

Answers to the finals judges' question "show me the security architecture" — what is
actually built, what is documented-but-not-built for the demo, and the upgrade path
between the two. Every claim here names the file that backs it; nothing below is aspirational
without saying so.

## Authentication

**Built:** a header, `X-PulseTwin-Role: supervisor | manager | leadership | system`, read
by `serve/middleware/auth.py::get_role`. No password, no token, no session. This is
authorization infrastructure without authentication in front of it — deliberately, per
the finals brief's anti-pattern guidance ("do not implement full OAuth/SSO — a role
header is sufficient for the demo").

**Production upgrade path:** replace the header with a JWT issued by the plant's OIDC
provider (Azure AD, Okta), validated with `python-jose` (already on the import
allowlist, ADR-033, specifically for this). The swap is small and isolated:

```python
# serve/middleware/auth.py, production version
def get_role(request: Request) -> str:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    claims = jwt.decode(token, OIDC_JWKS, algorithms=["RS256"], audience=API_AUDIENCE)
    role = claims.get("pulsetwin_role")          # mapped from an AD group at token-issue time
    if role not in ROLES:
        raise HTTPException(403, "no valid role claim")
    request.state.role = role
    return role
```

Every router already calls `Depends(require_role(...))` (`serve/middleware/auth.py`), so
no router code changes — only this one function's body does. `require_role`'s hierarchy
(`supervisor < manager < leadership < system`) does not change either.

## Authorization (RBAC)

Strict hierarchy — each role can do everything the one before it can (`serve/middleware/auth.py`):

| Role | Additional access | Backing endpoints |
|---|---|---|
| `supervisor` | stream, predict, containment, feedback, bottleneck (read), alert acknowledge | `/api/stream`, `/api/predict/defect`, `/api/trace/containment/*`, `/api/feedback`, `/api/alerts/*` |
| `manager` | + what-if, station stats, ingestion lag, system health | `/api/whatif`, `/api/stations/stats`, `/api/system/health`, `/api/system/ingestion-lag` |
| `leadership` | + economics, instrumentation plan, model health, audit log | `/api/economics/roi`, `/api/economics/instrumentation-plan`, `/api/model/health`, `/api/model/registry`, `/api/audit/log` |
| `system` | + admin: retrain, promote, rollback | `/api/admin/trigger-retrain`, `/api/admin/promote-model`, `/api/admin/rollback-model` |

Enforcement is server-side only and cannot be bypassed from the client — `require_role`
raises `403` regardless of what the UI renders. `frontend/components/shared/RoleGate.tsx`
is a client-side *mirror* of this table, not a second enforcement point: its own comment
says so ("this does NOT replace server enforcement"). `frontend/app/ops/page.tsx` is the
one view built to demonstrate the hierarchy live — a "Viewing as" selector lets a demo
drop from `system` to `manager` and watch the model-health, drift, audit-log, and
retraining panels gate themselves off in real time via `RoleGate`, rather than only
being described in this document.

**Rate limiting** (`serve/middleware/rate_limit.py`) is per-role, in-memory, single-process
— correct for the demo's `--workers 1` deployment (ADR-009). A multi-replica production
deployment would move the counters to Redis (already in the stack for the ingestion
feature buffer) so limits are shared across pods; documented here rather than built,
since the demo never runs more than one backend replica.

## Audit trail

Every mutating action is logged to `audit_log` (`migrations/002_audit_log.sql`,
`serve/middleware/audit.py::log_action`): timestamp, role, action, resource type/id, a
JSON payload, IP address. Covered actions: `feedback_submitted`, `whatif_simulation_run`,
`alert_acknowledged`, `retrain_triggered`, `model_promoted`, `model_rolled_back`.

The audit log is queryable at `GET /api/audit/log` (`leadership`/`system` only — CLAUDE.md's
operator-privacy and sourced-economics contracts extend to *who can see the audit trail
at all*), filterable by role/action/time range, and rendered live in
`frontend/components/ops/AuditLogViewer.tsx`. `frontend/components/shared/AuditToast.tsx`
fires a visible "✓ Action logged" confirmation at every mutating call site, so a judge can
watch the trail being written rather than take it on faith.

**Payload sanitisation:** logged payloads carry IDs and outcomes (`unit_id`, `verdict`,
`job_id`, `model_id`), never raw sensor readings or operator identity — the same
group-bucketing rule CLAUDE.md's operator-privacy contract already enforces on the
containment endpoint applies here too.

**Immutability:** `audit_log` is `INSERT`-only from application code — no `UPDATE`/`DELETE`
statement targeting it exists anywhere in `serve/` or `mlops/`. A production deployment
would additionally revoke `UPDATE`/`DELETE` grants on the table at the database-role
level and ship it to a separate log sink (see Data Governance's 7-year retention) rather
than relying on the absence of application code as the only guarantee.

## Data encryption

| | Demo | Production |
|---|---|---|
| In transit | Plaintext HTTP/MQTT (localhost, Docker network) | TLS 1.3 at the ingress/load balancer; MQTT over TLS (8883) — see `docs/PRODUCTION_ARCHITECTURE.md` |
| At rest | SQLite file, no encryption | `infrastructure/terraform/main.tf`'s `aws_db_instance` sets `storage_encrypted = true` (AES-256) |

## Network segmentation

`infrastructure/kubernetes/network-policy.yaml` is the enforcement layer, not this
document — see its own extensive header comment for the full policy-by-policy reasoning.
The one-sentence version: `ingestion` is the only pod that can reach MQTT; `serve`
(backend) can reach Postgres and Redis but has no rule granting it MQTT egress at all;
every pod is default-deny-egress with the internet excluded entirely. This is a control
enforced by the CNI, not by application code — even a fully compromised backend process
cannot open a connection to the OT network.

## Secrets management

**Demo:** plaintext environment variables in `docker-compose.prod.yml` (`pulsetwin_dev`
placeholder password) — acceptable only because nothing here is a real credential and the
stack is never exposed outside localhost.

**Production:** Kubernetes `Secret` objects (`infrastructure/kubernetes/backend-deployment.yaml`'s
`pulsetwin-secrets`), with a note in that manifest that a real rollout populates them via
a secret manager (AWS Secrets Manager / External Secrets Operator) at deploy time, never
checked into a manifest. `infrastructure/terraform/main.tf`'s `aws_db_instance` uses
`manage_master_user_password = true` specifically so Terraform state never contains a
plaintext database password.

## Vulnerability management

Not built for the demo (no CI pipeline exists to run it in), but the shape is: container
image scanning (Trivy/Grype) on every `serve/Dockerfile` and `ingestion/Dockerfile` build,
and dependency auditing (`pip-audit` against `serve/requirements.txt` /
`ingestion/requirements.txt`) on a schedule — both are a natural fit for the CI step this
repo does not currently have, not a redesign of anything that exists.

## Incident response

See `docs/INCIDENT_RUNBOOK.md` for the actual procedures (model drift, sensor dropout,
database failure, complete outage, false-alarm spike). This document states the posture;
that one states what a human does at 2 a.m.

## Compliance

- **IATF 16949** (automotive quality management) requires traceable evidence for quality
  decisions. The audit trail above, plus `docs/DECISIONS.md`'s ADR record of every
  modelling and threshold decision, is the traceability artifact — every alert, every
  acknowledgement, every model promotion has a timestamped, attributable record.
- **GDPR-adjacent operator data handling.** No real personal data is collected — operator
  identity is bucketed to an opaque group at the API boundary and never stored
  individually (CLAUDE.md's operator-privacy contract, ADR-017). `docs/DATA_GOVERNANCE.md`
  covers retention and the right-to-deletion process for the shift/team-level aggregates
  that do exist.
