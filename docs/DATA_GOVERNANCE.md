# Data Governance

The narrative rendering of `configs/data_governance.yaml`, which is the actual source of
truth — this document explains the *why*, the config states the *what* in a form a script
could validate against. Where the two ever disagree, the config wins and this document has
a bug.

## Classification

| Data | Level | Contains PII | Handling |
|---|---|---|---|
| Telemetry (torque, vibration, temperature, humidity, weld current, film thickness) | Internal | No | Standard internal access |
| Operator identity | Restricted | Yes | **Never stored.** Hashed into an opaque bucket at the API boundary before it reaches any table (ADR-017) — there is no restricted-tier table to retain or delete, because the raw identity never lands anywhere in this system in the first place |
| Inspection outcomes | Internal | No | Standard internal access |
| Economics (`configs/economics.yaml`) | Confidential | No | `leadership`/`system` roles only |

The operator-identity row is the one worth reading twice: this is not "we delete operator
identity after N days," it is "operator identity is never written to a persistent table at
all." `offline/build_artifact.py` writes only the hashed `operator_group` column — the raw
`operator_id` exists solely in the simulator's in-memory state during generation and is
discarded. CLAUDE.md's operator-privacy contract makes this a non-negotiable rule, not a
retention policy that could later be loosened.

## Retention

| Data | Retention | Source |
|---|---|---|
| Raw telemetry | 2 years | IATF 16949 traceability minimum for process records |
| `station_readings` (replay window) | 48 hours | ADR-008 — only the demo replay window ships in the artifact; full history would live in hourly rollup tables, not raw readings, in a production deployment |
| Audit log | 7 years | Sarbanes-Oxley-aligned retention commonly applied to manufacturing quality audit trails |
| Model artifacts (`.npz`, `model_registry` rows) | Indefinite | Needed for regulatory reproducibility of any historical prediction — if a 2027 audit asks "what did the model say about unit U04412 in 2026," the artifact that produced that number must still exist |
| Drift metrics | 2 years | Matches raw telemetry — a drift measurement is meaningless without the telemetry window it was measured against |

## Access control

Enforced by `serve/middleware/auth.py::require_role()` — the table below is the *policy*
the code implements, not a second, independent enforcement mechanism that could drift out
of sync with it:

| Table | Read | Write |
|---|---|---|
| `units` | supervisor, manager, leadership, system | system (offline pipeline only — no live write path in `serve/`) |
| `economics` | leadership, system | system |
| `audit_log` | leadership, system | system (only `serve/middleware/audit.py` writes here) |
| `model_registry` | leadership, system | system |

## Anonymisation

**Method:** `sha256(operator_id) mod N`, where `N` is `configs/plant_*.yaml`'s
`operators.operator_bucket_count`. **Irreversible** — there is no stored mapping from
bucket back to identity, by construction (ADR-017). The containment endpoint
(`/api/trace/containment/{unit_id}`) reports `shared_factors` as `"operator_group_B"` or
similar, never a real or stable operator ID — this is enforced in code, checked by
`tests/test_audit.py`'s and the original prototype's operator-privacy tests, not merely
described here.

## Right to deletion

**There is no stored operator identity to delete.** This section exists anyway because a
real deployment *will* be asked the question, and "there is nothing to delete" needs to be
a checkable claim, not an assumption a judge has to take on faith:

- **Applies to:** nothing, currently — `applies_to: []` in `configs/data_governance.yaml`.
- **Verification process a deletion request would trigger:** confirm the requester's raw
  identifier does not appear in any table dump. It structurally cannot, because
  `offline/build_artifact.py` only ever writes the hashed `operator_group` column — there
  is no code path that could have written the raw value, so "verify absence" is a grep, not
  an investigation.
- **What this does NOT cover:** if a future revision adds a feature that legitimately needs
  operator-level data (e.g. individual training-completion tracking), that feature would
  need its own governance review before shipping — this document covers the system as
  built, not a blank check for anything added later.

## Cross-border

Plant telemetry and every table derived from it stay within the plant's own geographic
region / cloud region. No cross-region replication of raw telemetry.
`infrastructure/terraform/main.tf`'s `aws_db_instance` is provisioned per plant
(`for_each = toset(var.plant_ids)`) specifically so a multi-region rollout keeps each
plant's data resident to one region rather than centralizing it — the "config-driven,
one YAML file per plant" model (CLAUDE.md, `docs/ARCHITECTURE.md` §Multi-plant) extends to
where the data physically lives, not only to how the pipeline is configured.
