# Scalability Review — 1 / 5 / 20 Plants

Production-readiness pass, 2026-09-21. This is an analysis against measured single-plant
behavior, not a claim that 5 or 20 plants were actually stood up — that infrastructure
does not exist and was not built for this pass (it would require cloud credentials and
incur real cost, both outside this pass's mandate).

## What is genuinely already built for multi-plant scale

Confirmed by reading the schema and code, not assumed from the docs:

- **Postgres partitioning by `plant_id`** on `genealogy` and `station_readings`
  (`migrations/001_initial_schema.sql`), with a `DEFAULT` partition catching any
  plant added later without a schema change.
- **One model artifact per plant** (`model_<plant>.npz`), selected on `plant_id` in
  `serve/scorer.py` — adding a plant is one config file, not a code change (ADR-006).
- **`model_registry`, `drift_metrics`, `sensor_health`, `triage_runs` are all keyed by
  `plant_id`** — the schema was designed for N plants from the start, not retrofitted.
- **The import wall and precompute-heavy/serve-thin split** mean adding a plant does not
  add weight to the runtime image — the heavy simulate/train/build pipeline is offline,
  once per plant, not per-request.

## What does NOT yet scale past a small number of plants, found this pass

1. **The champion-scorer map is per-process, not per-plant-partitioned across replicas.**
   `STATE["scorers"]` holds every plant's scorer in the memory of every backend replica.
   At 20 plants with a ~1MB npz each (`model_plant_a.npz` is roughly this size, confirmed
   by file listing), this is trivial memory-wise — not a real bottleneck. The REAL
   bottleneck is the promotion-propagation gap already named in ADR-041: a promotion for
   ANY one plant only reaches the one replica that received the API call. At 5-6 replicas
   this is already a real operational hazard (silent split-brain serving); at 20 plants
   each with independent retrain/promotion cadences, the odds of a stale replica at any
   given moment approach certainty. **This is the single most important thing to fix
   before scaling replica count, independent of plant count.**
2. **In-memory rate limiting does not partition by plant, only by role:ip.** At 20 plants
   sharing one backend fleet, a burst of legitimate traffic against one plant's dashboard
   could exhaust a role's shared budget and start rejecting requests for a DIFFERENT
   plant's supervisor, who did nothing wrong. Confirmed by reading
   `serve/middleware/rate_limit.py`'s key: `f"{role}:{ip}"`, no `plant_id` component.
   Moving to Redis-backed limiting (already recommended in SECURITY.md for the
   multi-replica reason) should also add `plant_id` to the key while it's being touched.
3. **MQTT ingestion is fixed at 1 replica** (`docs/PRODUCTION_ARCHITECTURE.md`'s own
   table says so: "MQTT shared-subscription scaling is a backlog item, not built"). At 20
   plants' worth of sensor traffic through one `mqtt_subscriber.py` process, this becomes
   the first real ingestion-side bottleneck. MQTT shared subscriptions (multiple consumer
   instances splitting one topic's load) is the standard fix and needs no broker change,
   only a subscriber-side rework.
4. **Prometheus metrics use `plant_id` as a label** (`predictions_total`,
   `drift_psi`, etc.) — bounded by the number of real plants (low cardinality, correctly
   designed), unlike the historical bug (#44, already fixed) where an unvalidated
   `plant_id` could mint unbounded series. At 20 real plants this is still a small,
   safe label cardinality. No concern found here.
5. **Model registry entries per plant per retrain cadence** — at a monthly retrain
   cadence (the documented recommendation) across 20 plants, that's 20 new rows/month,
   trivially small. `mlops/retrainer.py`'s artifact-pruning (keep 10 most recent
   challenger `.npz` per plant) already anticipated this and does not need to change.

## Projected growth (order-of-magnitude, from measured single-plant numbers)

| Metric | 1 plant (measured) | 5 plants (linear projection) | 20 plants (linear projection) |
|---|---|---|---|
| Events/sec (MQTT, at simulator's real cadence) | Not sustained-load-tested this pass; a short burst wrote 55 sensor_health rows in <20s | ~5x | ~20x — first ingestion bottleneck (see #3 above) |
| Stored rows/day (`station_readings`, `genealogy`) | 173,020 rows currently in the test Postgres volume (bulk-loaded, not a daily rate) | ~5x storage | ~20x storage — partitioning already handles this |
| DB size | `artifacts/pulsetwin.db` is ~38MB per plant (offline artifact; the live Postgres equivalent will differ) | ~5x | ~20x — well within a single managed Postgres instance (RDS/Cloud SQL), no sharding needed at this scale |
| SSE connections | Not load-tested past 1 connection this pass (see performance-benchmark.md) | Depends on operator headcount, not plant count directly | Same |
| Model registry rows | 2 (bootstrap, this pass) + N per retrain | ~5x | ~20x — trivial |
| Backend replicas needed | 1 (demo) | 2-3 (per documented HPA range) | Upper end of documented range (2-6) or beyond — **only safe once the promotion-propagation gap (#1) is closed** |

## What should scale which way

| Component | Scaling axis |
|---|---|
| `serve/` backend | Horizontal (replicas behind a Service) — vertical scaling is capped hard by `--workers 1`'s single-process design |
| `ingestion/mqtt_subscriber.py` | Horizontal, but NOT built yet (MQTT shared subscriptions) — currently a hard single-replica ceiling |
| PostgreSQL | Vertical first (managed instance sizing), horizontal (read replicas) only if read load on `units`/`station_readings` becomes the bottleneck — no evidence of that yet |
| Redis | Vertical — it is a cache/buffer, not a source of truth; no HA requirement stated or needed at this scale (`docs/PRODUCTION_ARCHITECTURE.md` already says so) |
| Model artifacts | Per-plant, already partitioned by design — no change needed |
| Rate limiting | Needs to move from per-process to Redis-backed BEFORE horizontal backend scaling is safe, or limits become effectively meaningless (5 replicas = 5x every budget) |

## Recommended topology change before scaling replica count

Do not add backend replicas until the promotion-propagation gap (ADR-041 alternative 2) is
resolved. Recommended, in order of complexity:

1. **(S) Operational runbook step**: document "restart all backend pods after any
   promotion" as a required manual step in `docs/CHANGE_MANAGEMENT.md` / the promotion UI
   itself. Zero code change, closes the gap today, costs a brief availability blip per pod.
2. **(M) Poll-based reconciliation**: each replica re-checks `model_registry` for its
   plants' champions on an interval (e.g., every 30-60s) and hot-swaps if changed — reuses
   `_resolve_champion_artifact` (already written, ADR-041), needs only a background
   `asyncio` task in `serve/main.py`. No new infrastructure.
3. **(L) Redis pub/sub signal**: promotion publishes an event, replicas subscribe and swap
   immediately. Lowest staleness window, highest complexity, needs the Redis wiring
   `docs/SECURITY.md` already recommends for rate limiting to happen anyway — worth doing
   both at once if this route is chosen.

This project's own stated philosophy ("do NOT over-engineer into microservices unless
measured bottlenecks justify it") argues for (1) now and (2) before the next real
multi-replica deployment, not (3) speculatively.
