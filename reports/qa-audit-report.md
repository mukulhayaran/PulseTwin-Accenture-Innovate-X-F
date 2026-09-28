# PulseTwin — End-to-End QA & Audit Report

**Date:** 2026-09-16 · **Baseline commit:** `c218bae` · **Scope:** full-stack validation of
the Round-2 build plus the finals upgrade (F1–F8), against `pulsetwin_final_prompt.md`,
`docs/SPEC.md` and `CLAUDE.md`.

**Method:** the system was *run*, not read. Every finding below was reproduced against a
live backend, a real browser driving the built frontend, or a direct execution of the
module in question. Nothing here is inferred from source alone.

---

## 1. Headline

The baseline suite was **226 passed, 0 failed** and remained so throughout. It caught
**none** of the 15 defects found here, and that is the most important result in this
report: every defect lives in a **seam between two components**, and the suite tests
components. Four of them silently disabled a feature the finals demo script depends on, and
the failure mode in each case was *quiet* — a swallowed 403, a filtered-out row, a
container serving the wrong file, a NULL sorting to the top of a capital plan — so nothing
looked broken from the inside.

| | |
|---|---|
| Defects found and fixed | **15** — H-1…H-7 (7 high), M-1…M-6 (6 medium), L-1…L-2 (2 low). H-2 turned out to be two separate defects in one code path (H-2, H-2b), so 16 distinct fixes. Logged as bugs 31–45 in `docs/STATE.md` |
| Issues reported but deliberately **not** fixed | 12 (§6), plus L-3 documented rather than changed |
| Regression tests added | `tests/test_qa_regressions.py` — one per defect, each reproduced before it was fixed |
| Suite | baseline **226 passed** → final **250 passed, 0 failed** |
| Never verified, still | Postgres path, `docker compose` — see §7 |

**Three of the four demo-critical defects were in features that had been reported as
"built and wired" in `docs/STATE.md`.** They were built. They were not connected.

---

## 2. What was exercised

| Layer | How | Result |
|---|---|---|
| Backend API | live `uvicorn`, 19 GET + 6 POST endpoints × 5 role contexts | RBAC matrix correct; 6 defects found |
| SSE stream | `curl -N`, 5s at 50× | headers correct (`X-Accel-Buffering: no`), provenance present on every event |
| Frontend | real Chromium (Playwright) over all 6 routes, network + console captured | **first confirmed rendering of all four persona views** |
| Graceful degradation | killed **and restarted** the backend with the browser open | banner in 1s, state retained, cleared in 1s on restart — full cycle (§3a) |
| MLOps lifecycle | real retrain → register → promote → rollback via the API | 4 defects found |
| Ingestion | modules executed directly against the committed artifact | 2 defects found |
| Security | SQLi probes, role escalation, header fuzzing, rate limiting | parameterisation and RBAC hold; 1 defect found |
| Contracts | provenance, sourced economics, operator privacy | all three hold (§5) |
| Postgres / Docker | — | **not verified** (§7) |

---

## 3. Defects found and fixed

### H-1 · The container served the landing page for every persona route
`frontend/Dockerfile`

`CMD ["serve", "-s", "out", ...]`. The `-s`/`--single` flag is single-page-app mode: it
rewrites every path that is not an exact file hit to `index.html`. Next's static export is
**multi-page** — it emits `supervisor.html`, `manager.html`, `leadership.html`, `ops.html`,
`login.html` — and serve@14 applies the single-file rewrite in preference to its own
clean-URL lookup.

**Evidence.** Served `out/` with the exact production command:

```
/            -> 200  8256 bytes
/supervisor  -> 200  8256 bytes   <- byte-identical to /
/ops         -> 200  8256 bytes   <- byte-identical to /
```

Without `-s`: 9454 / 11664 / 7185 / 9560 / 8377 bytes — five distinct pages. A real browser
confirmed it: all six routes rendered the same 1091-character landing page and made one
`/api/health` call each.

Never seen in development because `next dev` does its own routing, and the container had
never been run. **`make demo` shipped a UI where no persona view was reachable.**

**Fix:** drop `-s`. Guarded by `test_frontend_image_does_not_serve_every_route_as_the_landing_page`.

---

### H-2 · The drift monitor reported "healthy" from a run that said WARNING
`serve/routers/model_health.py::_latest_run`

`mlops/drift_detector.py` inserts one `drift_metrics` row per feature, each statement
letting the column DEFAULT stamp its own timestamp. Twenty rows therefore carry twenty
*different* millisecond timestamps. `_latest_run` kept only rows matching
`rows[0]["timestamp"]` — so it kept **one** feature and discarded nineteen.

**Evidence.** Both implementations run against the identical `drift_metrics` table:

```
OLD (exact-timestamp)      features_total=  1  shifted=0  worst=mean_cycle_s (0.019)
NEW (latest-per-feature)   features_total= 20  shifted=7  worst=hour_of_day  (4.691)

drift detector's own verdict on that same run:
  10/20 features shifted, worst=hour_of_day (4.691), overall: WARNING
```

The Ops view rendered **"FEATURES SHIFTED 0 / 1"**, "○ No features currently shifted beyond
threshold", `overall_status: healthy`, `recommendation: "No action needed."` — while the
detector that had just run reported a PSI of 4.69, twenty-three times the alert threshold.

This is the panel that answers *"how do you know when the model is wrong?"* — Act 3 of the
finals demo. It was answering it incorrectly, in the reassuring direction.

**Fix:** group by latest-per-`feature_name` ordered by `id` (monotonic on insert), the same
pattern `system.py::_active_dropouts` already uses for `sensor_health`.

**H-2b, found while fixing it:** `features_shifted` counted only `alert_triggered`
(PSI > 0.2), so the endpoint reported 7 where the detector reported 10 — two different
definitions of "shifted" for one run. Now counts the warning band too, matching both the
detector and the brief's own example response.

---

### H-3 · The supervisor view could never show a sensor dropout
`frontend/app/supervisor/page.tsx` + `serve/routers/system.py`

`/supervisor` pins itself to the `supervisor` role on mount, then polls
`getSystemHealth()` every 10 seconds. `/api/system/health` requires `manager`. Every poll
returned **403**, and the page's `.catch()` — there to stop a transient failure crashing the
view — swallowed it. `dropouts` stayed `[]` forever.

**Evidence.** A real browser session on `/supervisor`:

```
API calls: 7 total, 4 non-200
  !! GET /api/system/health?plant_id=plant_a -> 403
  !! GET /api/system/health?plant_id=plant_a -> 403
  !! GET /api/system/health?plant_id=plant_a -> 403
  !! GET /api/system/health?plant_id=plant_a -> 403
```

`SensorDropoutAlert` and the pulsing station border were structurally unreachable. This is
**Act 2 of the finals demo** — the stated differentiator, "it breaks gracefully".

**Fix:** a new `GET /api/system/sensor-dropouts` gated at `supervisor`, returning only the
dropout list. Widening the `/api/system/health` gate would have been the smaller diff and
the wrong one: connection-pool internals and uptime have no business at floor level, and
`docs/SECURITY.md`'s RBAC table is supposed to mean what it says. Table updated.

**Verified after fix:** 0 non-200 calls, and the banner renders —
*"⚠ 1 sensor dropout active · S8 vibration_mm_s — down 3m"*.

---

### H-4 · The audit log panel was permanently empty
`frontend/lib/api.ts::getAuditLog`

`new URLSearchParams({role: undefined, action: undefined, limit: 50})` stringifies
`undefined` into the literal text `"undefined"`. The request was
`?role=undefined&action=undefined&limit=50`, which the backend faithfully matched as
`WHERE user_role = 'undefined'`.

**Evidence.**

```
?role=undefined&action=undefined&limit=50  -> count: 0
?limit=50                                  -> count: 16
```

The Ops view rendered "No matching entries" against a table holding sixteen real entries.
This is the panel behind *"every action is traceable, IATF 16949 compliant"* — Act 4.

**Fix:** strip undefined/null/empty entries before building the query string.
**Verified after fix:** the panel renders eleven entries including `model_promoted`,
`model_rolled_back`, `retrain_triggered`, `alert_acknowledged`.

---

### H-5 · Model promotion was cosmetic
`serve/routers/admin.py`, `serve/main.py`, `mlops/retrainer.py`

`docs/MLOPS_LIFECYCLE.md` stage 7: *"`POST /api/admin/promote-model` … **is the only code
path that changes what `/api/predict/defect` actually serves**."* It changed a database row
and nothing else. No code path did.

**Evidence.** Same feature vector, across a promotion of a different model:

```
prediction BEFORE promotion : 0.011457011658095886
promote plant_a_defect_v1789554308 -> {"promoted": ..., "previous_champion": ...}
prediction AFTER promotion  : 0.011457011658095886   <- byte-identical
```

Two compounding problems underneath it:

1. **No hot-swap.** `STATE["scorers"]` is populated once at startup from
   `artifacts/model_<plant>.npz` and never touched again.
2. **Every challenger shared one artifact path.** `retrainer` always exported to
   `model_<plant>_challenger.npz`, so each retrain overwrote the weights behind rows already
   marked `champion` — the registry's `artifact_path` pointed at whatever ran last.

**Fix:** challengers export to `model_<challenger_id>.npz` (one file each, capped at the 10
most recent per plant and pruned on each run — one file per retrain with nothing pruning
them fills `artifacts/` on its own, which the test suite demonstrated within an afternoon);
promote and
rollback load the promoted row's `artifact_path` into the live scorer map and report the
outcome in a `serving` block. Deliberately runtime-only — it does **not** rewrite the
committed `artifacts/model_<plant>.npz`, which is a version-controlled build output
(ADR-027), so a restart reverts to the committed champion. That limit is now stated in the
response and in the doc rather than hidden.

**Verified after fix:**

```
prediction BEFORE rollback : 0.011457011658095886
rollback -> "serving": {"swapped": true, "artifact": "model_plant_a_challenger.npz",
                        "persists_across_restart": false}
prediction AFTER rollback  : 0.012057725221102787   <- changed
```

`docs/MLOPS_LIFECYCLE.md` corrected, with the old claim recorded rather than quietly
rewritten.

---

### H-6 · `/api/system/ingestion-lag` 500'd as soon as any sensor dropped out
`serve/routers/system.py`

`sensor_health.timestamp` comes from the column DEFAULT and is UTC-**aware** (`…Z`).
`last_reading_at` is written by `ingestion/` from a replayed event and is **naive**.
Subtracting them raises `TypeError`; the handler caught `ValueError` only.

**Evidence.** After inserting one dropout row:

```
GET /api/system/ingestion-lag -> HTTP/1.1 500 Internal Server Error
```

In the browser this surfaced as a CORS error (a 500 from the exception handler carries no
CORS headers), so the Ops Ingestion Lag panel simply died with a misleading message.

The endpoint looked perfectly healthy right up until a sensor actually dropped out — which
is the exact state the demo's Act 2 creates deliberately.

**Fix:** normalise both operands to UTC-aware before subtracting; catch `TypeError` too.

---

### H-7 · The retrofit capital plan was ranked upside down
`serve/main.py::instrumentation_plan`

`ORDER BY payback_months`. SQLite sorts NULLs **first**, and `payback_months` is NULL
exactly for the candidates worth nothing (annual value $0, payback undefined). The
Leadership view renders `plan.plan` top-to-bottom with no re-sorting.

**Evidence, before — the table as leadership read it:**

```
plant_a                                        plant_b
 1. S15  $1,200   $0/yr   --   DEFER           1. S7   $3,200  $0/yr   --   DEFER
 2. S22  $2,800   $0/yr   --   DEFER           2. S19  $1,200  $0/yr   --   DEFER
 3. S27  $2,800   $0/yr   --   DEFER              ... four more DEFER ...
 4. S10  $3,200  $30,792  1.2mo  PROCEED       7. S13  $3,200 $51,335  0.7mo  PROCEED
                                               8. S28  $2,800 $20,482  1.6mo  PROCEED
```

The endpoint's own docstring calls this *"Retrofit ranking by measured marginal model value
per dollar."* Plant A's single fundable retrofit was listed **4th of 4**; plant B's two were
**7th and 8th of 8**. `docs/SCALING_COST_MODEL.md` builds its capex argument on this table.

**Second defect in the same line:** Postgres sorts NULLs **last**, so the identical request
returned a different order depending on which database answered — precisely what
`serve/db.py` exists to prevent ("callers behave identically no matter which database
answered the query").

**Fix:** `ORDER BY (payback_months IS NULL), payback_months` — a boolean expression, which
both SQLite and Postgres accept. (`NULLS LAST` would not work: SQLite lacks it.)

**Verified after fix:** plant A leads with S10 / 1.2 mo / PROCEED; plant B leads S13
(0.7 mo) then S28 (1.6 mo), both PROCEED, with all six DEFER rows below.

---

### M-1 · The 60-second reorder window buffered nothing
`ingestion/mqtt_subscriber.py::flush_ready`

The window cut on `event_ts <= wall_clock_now - 60`. The publisher replays the committed
artifact verbatim, so `event_ts` is **2026-01-28** while `now` is months later. The
condition was true for every record the instant it arrived.

**Evidence.**

```
heap holds: 2 records
flush_ready() wrote 2 of them IMMEDIATELY; heap now holds 0
=> the 60.0 second reorder window buffered nothing.
```

Out-of-order arrivals were written in arrival order. The F2 acceptance criterion
*"late-arriving messages are correctly reordered"* was not being met, and no test covered
it — `mqtt_subscriber` had no tests at all, and `paho-mqtt` was not even installed.

**Fix:** cut against an **event-time watermark** (newest event timestamp seen), the standard
streaming primitive, which works whether events are months old or live and tracks the
publisher's speed multiplier for free. Wall clock is retained only as an idle drain, so the
tail of the heap is not stranded when the stream goes quiet.

**Verified after fix:** 18 records held in the buffer; a deliberately late-delivered oldest
event was written **first**; idle drain flushed all 40.

---

### M-2 · Every data-quality score was capped at 0.70
`ingestion/data_quality.py` (via the same cause as M-1)

Timeliness compared arrival against the **simulated** event timestamp, making every reading
appear ~231 days late.

**Evidence.**

```
age of a 'fresh' reading as the tracker sees it: 231.4 days
data-quality score for one perfectly valid reading:
  {'completeness': 1.0, 'timeliness': 0.0, 'validity': 1.0, 'overall': 0.7, 'n': 1}
```

`0.4·completeness + 0.3·timeliness + 0.3·validity` with `timeliness` pinned at zero means no
station could ever score above 0.70 no matter how healthy its feed — and a genuine
timeliness problem was indistinguishable from the baseline.

**Fix:** the publisher now stamps `emitted_at` (wall clock at the edge gateway) alongside
`ts` (the event's own time) — exactly the Source/Server timestamp pair OPC-UA carries. The
subscriber measures timeliness against `emitted_at`, falling back to `ts` for a genuinely
live feed.

**Verified after fix:** healthy feed (50 ms lag) → `timeliness: 1.0`; genuinely late
(400 s) → `timeliness: 0.0`. The metric discriminates again.

---

### M-3 · A failed retrain left no trace in the registry
`mlops/model_registry.py::mark_failed`

`retrainer.run()` calls `mark_failed()` from its `except` block, but the most likely failure
— training itself raising — happens **before** `register()` inserts the row. `mark_failed`
was `UPDATE`-only, so it matched zero rows.

**Evidence.**

```
retrain raised as expected: simulated training failure (corrupt feature parquet)
model_registry rows after a FAILED retrain: 0
```

`status='failed'` is one of the four states `migrations/003` defines, and it was unreachable
on the path that actually produces it. `/api/model/registry` — which backs
`RetrainingConsole` — could never show a failed model.

**Fix:** insert-then-update. **Verified:** `plant_a_defect_v… status=failed notes=simulated
training failure`.

---

### M-4 · Models reported a negative age
`offline/ml/train.py`, `mlops/*`, `serve/routers/*`

`time.strftime("%Y-%m-%dT%H:%M:%S")` is **local naive**. The SQLite/Postgres column DEFAULTs
stamp **UTC**. `model_health` computes `age_days` against `datetime.now(timezone.utc)`.

**Evidence.**

```
trained_at : 2026-09-16T15:28:05          (local)
created_at : 2026-09-16T09:58:05.790Z     (UTC, same moment)
age_days as model_health computes it: -1
```

The Ops view displayed **"AGE −1d"**. Cosmetic in isolation, but the same mixed clocks feed
`ORDER BY retired_at DESC`, which is how rollback chooses its target.

**Fix:** UTC at all seven write sites.

---

### M-5 · Rate-limit and RBAC rejections reached the browser as CORS errors
`serve/main.py`

`add_middleware` pushes onto the **top** of the stack, so the last one added is the
**outermost**. `RateLimitMiddleware` was added after `CORSMiddleware` and therefore wrapped
it — its 429 short-circuits and never passes through CORS.

**Evidence, before:**

```
HTTP/1.1 429 Too Many Requests
retry-after: 45
(no access-control-allow-origin)
```

A browser hitting the per-role limit sees an opaque CORS failure, indistinguishable from the
backend being down. The same applied to every 403 the RBAC layer raises — so the one thing
the `/ops` "Viewing as" selector exists to demonstrate would have looked like a crash.

**Fix:** add `RateLimitMiddleware` first so CORS ends up outermost.
**Verified after fix:** 429, 403 and 200 all carry `access-control-allow-origin: *`.

---

### M-6 · `model_registry.rollback()` recorded no approver
The module docstring says its promote/rollback SQL duplicates `serve/routers/admin.py` and
that the two must be kept in sync. They had drifted: `admin.py` sets `promoted_at` **and**
`promoted_by`; the registry copy set only `promoted_at`. The same action recorded an
approver through the API and none through the module — in the table an IATF 16949 audit
would read. **Fixed**, with a text assertion so the two cannot drift silently again.

---

### L-1 · A negative `limit` disabled the cap
`?limit=-1` returns **all** rows in SQLite — `/api/units` returned 1423 and
`/api/bottleneck/history` 2688 against a documented cap. `serve/routers/audit.py` already
clamped; `main.py` did not. **Fixed** (clamped at 2000 / 5000). Low severity only because the
artifact tables are small; the same pattern against a populated Postgres is a different
story.

### L-2 · Unauthenticated input could mint unbounded Prometheus labels
`predictions_total.labels(plant_id=req.plant_id).inc()` ran **before** validation, so a
POST with any made-up `plant_id` created a new time series —
`pulsetwin_predictions_total{plant_id="plant_zzz"} 1.0` was observed. A cardinality
explosion in the monitoring backend, from the outside. **Fixed:** validate first.

### L-3 · A tied comparison reports contradictory criteria *(documented, not changed)*
`compare()` uses `>` for PR-AUC but `>=` for precision, so an identical challenger reports
`beats_pr_auc: false` alongside `beats_precision_at_budget: true`. The asymmetry is
defensible — strictly better on the headline metric, not-worse on the guardrails — but the
key name says "beats" where the test is "did not regress". Left as-is (the frontend and a
test both read the key) with the intent documented at the comparison site.

---

## 3a. Graceful degradation — the one acceptance criterion that passed first time

The finals brief's F5 acceptance reads: *"Kill the backend while the frontend is running →
OfflineBanner appears within 5 seconds. Restart the backend → frontend reconnects and banner
disappears."* Driven for real against `/supervisor` in Chromium:

```
connected state before kill  : SSE LIVE
offline banner before kill   : false
killing backend pid 4888
OFFLINE banner appeared after: 1000 ms          (brief allows 5000)
  banner line                : "⚠ TWIN OFFLINE"
  line map still rendered    : true
  station tiles retained     : true
restarting backend...
OFFLINE banner cleared after : 1000 ms
connected state after restart: SSE LIVE
```

The rendered banner reads *"⚠ TWIN OFFLINE — Last known state from 4:08:58 PM (1s ago) ·
retrying in 14s…"* with a working **[Retry Now]** button; the status pill flips to
`RECONNECTING` and the line map keeps its last known state rather than blanking, exactly as
specified. Recovery is faster than the 15-second application-level auto-retry ticker because
`EventSource` also retries natively — the ticker is the backstop, not the primary path.

This is the only end-to-end acceptance criterion in the finals brief that needed no fix.

---

## 4. Requirements checklist — finals brief F1–F8

| Phase | Requirement | Implemented | Verified here | Notes |
|---|---|---|---|---|
| F1 | Postgres schema, migrations, `db_postgres` | Yes | **Partial** | DDL and the `?`→`%s` translation reviewed by hand; no server available (§7) |
| F1 | SQLite fallback for local dev | Yes | Yes | `/api/health` → `database: "sqlite"` |
| F2 | Unified Namespace topic schema | Yes | Yes | 13 tests |
| F2 | Publisher with fault injection | Yes | **Partial** | code path executed directly; no broker |
| F2 | Subscriber dedup | Yes | No | no broker; sha256(topic+payload) reviewed |
| F2 | **Late-arrival reordering** | Yes | **Yes — was broken** | M-1 |
| F2 | Dropout detection | Yes | Yes | surfaces through the API and the UI |
| F2 | Data-quality scoring | Yes | **Yes — was broken** | M-2 |
| F3 | PSI / calibration / coverage drift | Yes | Yes | detector correct; **its endpoint was not** (H-2) |
| F3 | Champion–challenger retraining | Yes | Yes | run end-to-end via the API |
| F3 | Model registry + **promote/rollback** | Yes | **Yes — was cosmetic** | H-5 |
| F3 | SPC fallback | Yes | Yes | shadow precision 3.0% / recall 100%, published as measured |
| F3 | `/api/model/health`, `/api/system/health` | Yes | Yes | schema differences in §6 R-3 |
| F4 | RBAC over 4 roles | Yes | Yes | full matrix, §2 |
| F4 | Audit trail | Yes | Yes | writes verified; **the viewer was empty** (H-4) |
| F4 | Rate limiting | Yes | Yes | 180 then 429 + `Retry-After` |
| R2 | Instrumentation ROI planner, "ranked by value per dollar" | Yes | **Yes — was inverted** | H-7; not a finals phase, but it is the Leadership view's capital recommendation |
| F5 | OfflineBanner / stale indicators | Yes | **Yes — first time** | banner in 1s, state retained, cleared in 1s |
| F5 | SensorDropoutAlert on `/supervisor` | Yes | **Yes — was unreachable** | H-3 |
| F5 | Prometheus metrics | Yes | Yes | 43 `pulsetwin_*` series |
| F6 | Ops view, 7 panels | Yes | **Yes — first time** | 2 of 7 were misreporting |
| F7 | K8s manifests + Terraform | Yes | No | files reviewed; no cluster |
| F8 | Eight finals documents | Yes | Partial | two contained claims contradicted by the code (H-5, H-3); corrected |

---

## 5. The three contracts (`CLAUDE.md` §3)

| Contract | Verdict | Evidence |
|---|---|---|
| **Provenance** | Holds | every SSE event carries `provenance: {signal: "measured"}`; manual stations render the amber `INF` badge and hatch in the browser |
| **Sourced economics** | Holds | all 4 `/api/economics/roi` components carry a named `source` (IHS Markit 2024, NHTSA recall data, plant engineering estimate, a stated supervisor rate); 0 unsourced. The instrumentation plan states its method. But see H-7 — sourcing the numbers is not the same as ranking them correctly |
| **Operator privacy** | Holds | **21 endpoint responses swept** with a regex for operator/employee/badge/worker identifiers — **0 hits**. `/api/trace/containment` on a real alert unit yielded only `same_lot`, `same_variant`, `same_time_window` |

One caveat on provenance, not a violation: `/api/predict/defect` returns
`provenance: {"measured": [...], "inferred": []}` where `inferred` is a hardcoded empty list
and can never be non-empty (§6 R-9).

---

## 6. Reported, deliberately not fixed

| # | Issue | Why not fixed here |
|---|---|---|
| R-1 | **The production model is not in the registry.** `/api/predict/defect` serves `artifacts/model_<plant>.npz` from `make train`; only `mlops/retrainer` ever calls `register()`. The Ops view's "champion" has never scored a request, and the model that has, has no registry row, no drift attribution and no promotion record. | Needs a decision about whether `build_artifact` should register — a design change, not a bug fix. **The most consequential item on this list.** |
| R-2 | **Real-time scoring ordering race.** `_on_state` scores immediately while telemetry sits in the reorder buffer, so a unit can be scored before its readings are written. Pre-existing; the M-1 fix widens the window. | Fixing it means routing state through the same heap. Unverifiable without a broker + Redis + Postgres; shipping an untested redesign of the scoring path is worse than reporting it. |
| R-3 | `/api/system/health` omits `data_quality_score`, `units_processed_last_hour`, and the `mqtt`/`redis` blocks the brief specifies. `data_quality.py`'s own docstring claims the first one is exposed. | The value lives in the ingestion process's memory; `serve/` cannot reach it. Inventing a number would violate the project's own honesty rule. |
| R-4 | 13 read endpoints have no role check, including `/api/units/{id}`. | Already documented in `SECURITY.md` as a known gap with a stated reason. Confirmed still accurate. |
| R-5 | **Version pin drift.** `serve/requirements.txt` pins fastapi 0.115.0 / pydantic 2.9.0; everything here ran against 0.139.0 / 2.13.4. The image installs versions nothing has tested. STATE.md bug #23 was exactly this class. | Changing pins without a working container to test them is guesswork. |
| R-6 | Postgres path unverified. | A server listens on `:5432` but is not this project's and credentials are unknown; probing someone's database was not appropriate. |
| R-7 | `docker compose` unverified. | No Docker daemon, as at the last two sessions. **H-1 is direct evidence of what that gap costs** — it was found by reproducing the container's serve command by hand. |
| R-8 | `ingestion-lag` semantics: the subscriber writes `last_reading_at = NOW()`, so the computed lag is ~0 by construction rather than a real transport measurement. | Semantic, needs a product decision about what the panel should measure. |
| R-9 | `predict` response `provenance.inferred` is hardcoded `[]`. | The endpoint cannot know which caller-supplied features were soft-sensor derived; the contract needs a request-side field. |
| R-10 | `mqtt_publisher` / `mqtt_subscriber` / `feature_buffer` had **zero** tests and their dependencies were not installed. | Partly addressed — 3 of the 24 new tests cover subscriber ordering and publisher stamping. Full coverage needs a broker fixture. |
| R-11 | Unknown `plant_id` returns 404 on `/api/metrics` and `/api/units` but `[]` with 200 on `/api/stations/stats`, `/api/bottleneck/history`, `/api/whatif/stations`. | Cosmetic inconsistency; changing status codes could break a consumer. |
| R-12 | `docs/FINALS_DEMO_SCRIPT.md`'s pre-flight runs `python -m ingestion.mqtt_publisher` on the host but never says to `pip install -r ingestion/requirements.txt` first. `paho-mqtt` is not in `requirements-dev.txt`. | One-line doc change; flagged so it is made deliberately. |

**Withdrawn during the pass.** The publisher and the frontend are absent from
`docker-compose.prod.yml`, which looked like a gap until `FINALS_DEMO_SCRIPT.md` showed both
are deliberately host-side steps — the publisher standing in for the OT network is
architecturally right. Not defects.

---

## 7. What this pass could not verify

Two gaps remain, both unchanged from `docs/STATE.md`, and both now have a concrete cost
attached:

1. **`docker compose -f docker-compose.prod.yml up` has still never run.** No daemon.
   H-1 — a container in which no persona view was reachable — was found only because the
   production serve command was reproduced by hand. It is a reasonable assumption that a
   real `up` finds more.
2. **The PostgreSQL path has still never executed.** The migration DDL and the `?`→`%s`
   translation were reviewed by hand and the `ON CONFLICT` target in
   `mqtt_subscriber._write_reading` does match `migrations/001`'s composite primary key.
   That is a code review, not a test.

Everything else in this report was executed.

---

## 8. Test results

| Run | Result |
|---|---|
| Baseline (`c218bae`, pre-fix) | **226 passed**, 0 failed, 17m07s |
| After fixes, run 1 | **250 passed**, 0 failed, 16m53s |
| After fixes, run 2 (independent, separate ops DB) | **250 passed**, 0 failed, 16m41s |
| New regression module alone | `tests/test_qa_regressions.py` — **24 passed** in 7s |
| `test_api.py` after the H-7 ordering change | **40 passed** (unchanged from baseline) |
| Frontend `tsc --noEmit` | clean |
| Frontend `npm run build` | clean, 7 routes |
| Import wall (`test_serve_imports.py`) | **39 passed** — the wall still holds after the new `serve.scorer` import in `admin.py` |

Each of the 24 new tests covers a defect that was reproduced before it was fixed. They are
grouped in one module because they share a cause rather than a subsystem: each covers a
**seam** — detector→API, API→page, publisher→subscriber, image→route table. Two assert
against non-Python files (a Dockerfile `CMD`, a TypeScript query-string builder); there is
no JS test runner here, and both defects silently emptied a headline feature, so a cheap
text assertion beats none.

---

## 9. Recommended next actions

1. **Decide what R-1 means.** The registry not knowing about the model that serves traffic
   is the one finding a judge could turn into a hard question — *"which model is actually
   running?"* — that the current answer does not survive.
2. **Run the production stack.** It is still the highest-value unexecuted step, and this
   pass produced direct evidence of what skipping it costs.
3. **Re-run `make build-artifact`** at some point so `trained_at` is written in UTC in the
   committed artifact. Not urgent — the fix is in, the existing row is stale demo state.
4. **Reconcile R-3 or amend the docstring** in `ingestion/data_quality.py` that claims a
   field the API does not expose.
