# Scaling & Cost Model

Answers "what does it cost to run this at 20 plants?" Every row below states whether it is
**measured** (computed from this repo's actual artifacts/config), **derived** (arithmetic
on measured or sourced inputs), or an **engineering estimate** (labelled, with a stated
basis, same discipline as `configs/economics.yaml`) — never an unsourced number.

## The headline table

|  | 1 Plant | 5 Plants | 20 Plants |
|---|---|---|---|
| Infrastructure (cloud, monthly) | ~$500 | ~$1,400 | ~$5,000 |
| Sensor retrofit (one-time) | ~$2,700 | ~$13,500 | ~$54,000 |
| Implementation (one-time) | $350,000 | $590,000 | $1,490,000 |
| Annual operations | $75,000 | $225,000 | $600,000 |
| **Annual value (conservative)** | **$1.60M** | **$7.98M** | **$31.9M** |
| Net ROI (year 1) | ~333% | ~1,211% | ~1,997% |

Read the assumptions below before quoting any of these numbers — the ROI figures are large
because the underlying per-defect cost figures in `configs/economics.yaml` are large
(warranty escapes at $8,500/unit), not because this model is optimistic about catch rate.
`docs/BUSINESS_PROPOSAL.md` §7 makes the equivalent honesty argument about PR-AUC; this
table is where that same discipline applies to dollars instead of a model score.

## Infrastructure (cloud, monthly) — engineering estimate

Derived from `configs/deployment.yaml`'s resource sizing × AWS on-demand list pricing
(us-east-1, 2026 rates) — a planning estimate, not a vendor quote:

| Component | Basis | 1 Plant | 5 Plants | 20 Plants |
|---|---|---|---|---|
| EKS control plane | $73/mo flat, shared across all plants in one cluster | $73 | $73 | $73–150 (a second regional cluster becomes reasonable near this scale) |
| Worker nodes | `configs/deployment.yaml` backend/ingestion sizing; HPA scales the workload pool (`hpa.yaml`, 2-6 replicas) as request volume grows with plant count | ~$180 | ~$305 | ~$850 |
| Postgres (RDS, Multi-AZ) | **One instance per plant** — `infrastructure/terraform/main.tf`'s `aws_db_instance` is `for_each`'d over `var.plant_ids`, so this line scales linearly, unlike the shared-cluster compute above | ~$204 | ~$1,020 | ~$4,080 |
| Redis + load balancer | Shared, scales slowly | ~$28 | ~$28 | ~$80 |
| **Total** | | **~$485/mo** | **~$1,426/mo** | **~$5,083/mo** |

The scaling shape matters more than the exact dollar figures: **compute is shared and
scales sub-linearly; the database is per-plant and scales linearly** — a direct
consequence of the config-driven, one-database-per-plant architecture
(`docs/PRODUCTION_ARCHITECTURE.md`), chosen for data-residency reasons
(`docs/DATA_GOVERNANCE.md` §Cross-border), not for cost.

## Sensor retrofit — measured finding, then priced

**Not** "instrument every manual station." `docs/DECISIONS.md` ADR-031 measured that
**three of plant A's four manual stations score zero uplift from instrumentation** —
including S15, the second-strongest driver in the entire model, because its operator
checklist already captures the signal that matters there. Only stations the ranked
instrumentation plan (`GET /api/economics/instrumentation-plan`) actually recommends get
priced here.

**Estimate:** ~1 station worth instrumenting per plant (this project's own measured
ratio), at the simple average of `configs/economics.yaml`'s five listed sensor costs
(torque $2,800 / vibration $1,800 / temp-humidity $1,200 / film thickness $4,500 / weld
current $3,200 → average $2,700, installed). Blindly instrumenting all four manual
stations at every plant would cost **~4× this** — roughly $162,000 more at 20-plant scale
for zero measured benefit. That gap is the instrumentation planner's entire value
proposition, made concrete in dollars.

## Implementation — sourced first plant, estimated marginal plants

- **First plant: $350,000** — `configs/economics.yaml`'s `system.implementation_cost`,
  sourced as "2 FTE-years engineering, historian integration, edge gateway, first-year
  cloud + support."
- **Marginal plant: ~$60,000 (engineering estimate, not measured).** The platform itself
  — `serve/`, `mlops/`, all three persona dashboards — ships unmodified per
  `docs/ARCHITECTURE.md` §Multi-plant's "adding a plant is one YAML file" claim. What
  does NOT come for free is per-site edge-gateway installation, historian integration,
  and a validation pass against that plant's own physical line — the $60,000 covers that,
  not a second platform build.
- 5 plants: $350,000 + 4×$60,000 = **$590,000**. 20 plants: $350,000 + 19×$60,000 =
  **$1,490,000**.

## Annual operations — engineering estimate

Ongoing MLOps support (`docs/MLOPS_LIFECYCLE.md`'s monthly retrain cadence, drift
monitoring, incident response) at a fully-loaded engineering rate, with the same
economies of scale as implementation — a shared support team, not one dedicated engineer
per plant: 0.5 FTE for a single plant (~$75,000/yr), a 1.5-FTE shared team at 5 plants
(~$225,000/yr), a 4-FTE team at 20 plants (~$600,000/yr).

## Annual value — measured, and reported at its conservative bound

Computed directly from this repo's shipped artifacts via the same formula
`GET /api/economics/roi` implements (`net = scrap_avoided + rework_avoided +
warranty_avoided − alarm_response_cost`, base scenario, i.e. catch-rate multiplier 1.0×
— the *measured* test-set recall, not an assumed one):

| Plant | Annual value (measured) | Why it differs |
|---|---|---|
| Plant A | **$10.27M** | Oracle PR-AUC 0.481 — an easier detection problem; the model captures 53% of a ceiling that itself leaves a lot of catchable signal |
| Plant B | **$1.60M** | Oracle PR-AUC 0.075 (`docs/AUDIT.md` B34) — a much harder line by construction, not by a weaker model. Mode B fires on 13.6% of visits, diluting the hazard signal plant-wide |

**The scaling table above uses Plant B's figure, not Plant A's**, for every multi-plant
row. This is deliberate: a portfolio-level business case built on the easy plant's economics
would overstate every subsequent site. Using the harder, more conservative measured number
as the scaling baseline is the same discipline `docs/BUSINESS_PROPOSAL.md` §7 applies to
PR-AUC — report the number that survives scrutiny, not the one that looks best in a deck.
A real 20-plant portfolio would fall somewhere between $31.9M (all Plant-B-like) and
$205.4M (all Plant-A-like); the true figure depends on how many sites look like which
plant, which is exactly the kind of thing Phase 1 Shadow mode (`docs/BUSINESS_PROPOSAL.md`
§5) at each new site is for finding out before committing capital.

## Net ROI — derived, first-year, undiscounted

`ROI = 100 × (annual_value − annual_operations − 12×infrastructure − implementation) / implementation`
— the same implementation-cost-normalized convention `/api/economics/roi` uses for a
single plant, extended here to include the recurring infra/ops lines that endpoint's
single-plant formula does not carry (a single plant's monthly cloud bill is small enough
next to a $350,000 implementation cost to round away; it stops being roundable at 20
plants, so this document's ROI line is not simply copied from the API's). This is a
year-one payback-style figure, not a discounted multi-year NPV — see
`docs/BUSINESS_PROPOSAL.md` §4 for the 3-year NPV treatment using
`configs/economics.yaml`'s 10% discount rate.
