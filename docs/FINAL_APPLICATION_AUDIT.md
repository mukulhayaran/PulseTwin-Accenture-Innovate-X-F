# PulseTwin — Final Application Audit

2026-09-26 · base commit `06080a7` plus uncommitted working tree · ADR-063 · machine-readable
results: `artifacts/final_application_audit.json`

---

## 1. Executive verdict

**Demo-ready on the Postgres stack, with three follow-ups for you. Not production-ready
(the demo credentials and synthetic data are the reasons, by design).**

- **Verified by running it:**
  - The whole application ran on the production-shaped stack (Postgres, Redis, MQTT,
    ingestion, backend, static UI).
  - Four roles plus anonymous, on desktop and on mobile.
  - The full plant A alert lifecycle, from alert to CLOSED.
  - Eight deterministic agent intents, each verified by the gate.
  - An exact reproduction of the ML metrics.
- **Found and fixed, 14 defects.** Every one lived at a seam that the 821-test suite could
  not see, and each fix is pinned by a test or a measurement. Among them:
  - a privilege escalation;
  - a stream that showed alerts before the model could have scored the unit;
  - a float4 rounding that made every alert recommend "Monitor";
  - an uncapped job spawner that took the whole host down.
- **Not done, and why:**
  - **The `reliability_test` registry row** needs its repair SQL (§15.1). The tool
    permission for writing to the stack's database was declined.
  - **Six of the eight incident scenarios** did not run, because permission to drive more
    simulations on the stack was declined.
  - **The closing-turn fix for the agent** is unit-tested but has not been re-verified
    against the live provider.

## 2. Environment

| Item | Value |
|---|---|
| Host | Windows 11 Home 10.0.26200, Docker Desktop (WSL2 VM, 3.7 GiB) |
| Stack | `docker-compose.prod.yml`: postgres, redis, mqtt, ingestion, migrate, load-data, backend :7860, frontend :3000 |
| Backend | Python 3.11 image, FastAPI, `PULSETWIN_AUTH_MODE=session`, `PULSETWIN_REQUIRE_POSTGRES=1` |
| Host tooling | Python 3.13 `.venv` (pytest, ruff), Node + Next 15 static export, Python Playwright + Chromium |
| GenAI | **agent mode**: `genai: agent mode -- provider key present`, OpenRouter `nvidia/nemotron-3-ultra-550b-a55b:free` |
| Migrations | 001–019 applied (017 float8 risk, 018 `triage_run_id`, 019 `opened_wall_at` are new) |

## 3. Startup commands

```bash
# the full stack (make is not installed on this host; this is what `make up` runs)
docker compose -f docker-compose.prod.yml up -d --build
# UI: http://localhost:3000   API: http://localhost:7860   health: /api/health
# local credentials: supervisor/sup2026 manager/mgr2026 leadership/lead2026 system/sys2026
# (the cloud deploy uses PULSETWIN_PASSWORD_<ROLE> from render.yaml; these do not work there)
docker compose -f docker-compose.prod.yml down        # stop (keeps volumes)

# tests (host)
OMP_NUM_THREADS=1 PULSETWIN_OPS_DB=<scratch>/ops.db .venv/Scripts/python.exe -m pytest tests/ -q
.venv/Scripts/python.exe -m ruff check .
cd frontend && npx tsc --noEmit -p . && npm run build
```

## 4. Tests and results

| Check | Result |
|---|---|
| Full pytest suite (821 collected) | **811 passed, 10 failed** in 31 min. All 10 failures had one cause: plant B's replay day had no alert (§13, #11) |
| After that fix: the affected files (api, intelligence, genai_tools, stream_causality, no_leakage, live_simulation) | 122 passed, 1 failed. The failure was the ADR-029 guard catching a 100% agreement; fixed (§13, #12) |
| Touched files after every fix (intelligence, proposals, auth_session_mode, serve_imports, alert_metrics_clock, risk_precision) | **131 passed** |
| New tests this audit | `test_auth_session_mode.py` (4), `test_stream_causality.py`, `test_risk_precision.py`, `test_alert_metrics_clock.py`, two closing-turn tests in `test_genai_loop.py`, the forecast-cap test, the steady-shift agreement test |
| `ruff check .` | All checks passed |
| `tsc --noEmit` | clean |
| `next build` (static export) | 10/10 pages, clean |
| Import wall (`test_serve_imports.py`) | passes; no new third-party import in `serve/` |

The full suite was **not** rerun end to end after the last three fixes; the touched files
were. Budget 31 minutes for it on this host.

## 5. Browser matrix

Python Playwright + Chromium against the stack's static UI. There were 5 identities × 2
viewports (1440×900 desktop, 390×844 mobile, `is_mobile`) × 7 routes, which is 70 route
loads. Every signed-in identity signed in through the real `SignInGate` form. Screenshots
were taken and looked at.

| Check | Result |
|---|---|
| Access control vs `routeAccess.ts` (gate shown ⇔ role not allowed) | **70/70** |
| Console errors / uncaught page errors | **0 / 0** (after the fix in §13, #13) |
| Horizontal overflow at 390 px | **0** on every route |
| Session stored after sign-in | 8/8 |
| Session restored after a hard reload | 8/8 |
| Sign-out clears the session and re-gates `/manager` | 8/8 |
| Median / max DOMContentLoaded | 40 ms / 834 ms |

| Route | anonymous | supervisor | manager | leadership | system |
|---|---|---|---|---|---|
| `/` landing | open | open | open | open | open |
| `/overview` | open (degrades per panel) | open | open | open | open |
| `/supervisor` | open | open | open | open | open |
| `/manager` | gate | gate | **open** | gate | gate |
| `/leadership` | gate | gate | gate | **open** | gate |
| `/ops` | gate | gate | gate | gate | **open** |
| `/copilot` | gate | gate | open | open | open |

Visual defects found in the screenshots and fixed:
- **Mobile KPI values truncated** ("$10.27…", "2,245/…"). Fixed and verified: no
  `.text-numeric.truncate` element overflows on the mobile manager or leadership views.
- **Colliding x-axis ticks** on the mobile constraint-migration chart. Fixed and verified
  by screenshot.
- **"Not signed in" chip clipped** at 390 px. Fixed.
- **Value-bridge bar labels overlapping** on mobile. Fixed in source and in a clean build,
  but **not yet deployed** (§18).

## 6. API matrix

There were 40 GET routes from `/openapi.json` × 5 identities, plus an auth probe. Timings
are for the system role.

| Identity | 200 | 401 | 403 | 422 | 503 |
|---|---|---|---|---|---|
| anonymous | 23 | 1 | 14 | 1 | 1 |
| supervisor | 24 | – | 14 | 1 | 1 |
| manager | 32 | – | 6 | 1 | 1 |
| leadership | 38 | – | – | 1 | 1 |
| system | 38 | – | – | 1 | 1 |

- **Every 403** matches `require_role`:
  - `audit`, `economics` and `model` need leadership;
  - `triage`, `proposals`, `shift` and `watch` need manager.
- **The single 422** is `/api/knowledge/search` probed without its required `query`
  parameter, which is correct.
- **The single 503** is `/api/demo/readiness`. It reports the optional MQTT publisher
  profile (`make up-mqtt`) as not running, which is correct and not a bug.
- **Auth probe:**
  - login 200 for all four roles, and session restore 200 for all four;
  - wrong password 401;
  - a token after logout 401;
  - a bare `X-PulseTwin-Role: system` 401;
  - a header above the session's role 403.
- **Latency:**
  - median 37 ms;
  - slowest before fixes: `data-quality` 2.3 s and `stations/stats` 0.88 s;
  - after the SQL aggregation: about 1 s and 0.47 s (§12).

## 7. End-to-end result (plant A, live synthetic, speed 500)

One run through the real SSE feed:

| Step | Result |
|---|---|
| First alert | U029759 at S21, 08:32:52 replay time, calibrated risk 0.4061, `lead_time_min` 10.4, budget 1/6. The alert event's ts equals `prediction_ts`, so it is causal |
| Alert status | OPEN |
| Pause | paused |
| Acknowledge | acknowledged |
| Containment | 200; 12 at-risk units. Shared factors: `same_lot`, `same_operator_group`, `same_time_window`, `same_variant`. Operator appears only as a group |
| Decision (INSPECT) | 200; status **ACTIONED** |
| Resume | running |
| Inspection outcome | at S30, 9.3 min after the alert: U029759 `actual_defect=0` → status **VALIDATED**. The false alert is counted |
| Close | 200, **CLOSED** |
| Run metrics | 4,184 events · 6 alerts · 1 acknowledged · **mean time to acknowledge 0.09 s, to contain 2.16 s** (was 20,812,517 s, §13 #5) · 1 validated decision · 1 false alert · 1 confirmed defect |
| Audit trail | `alert_acknowledged` → `containment_decision_recorded` → `alert_closed` |

The run reports 1 false alert **and** 1 confirmed defect, and that is correct. The flagged
U029759 was clean, but the decision targeted U029758 from its containment set, and
U029758 was a real defect that the model never alerted on (`actual_defect=1`,
`alert_fired=0`). This is the containment trace doing its job, and it is a better demo
beat than a true positive.

**Incident scenarios** (1,500 events each, affected station S12):

| Scenario | Result |
|---|---|
| `normal` | no down or blocked state; one `tool_wear` watch firing (S3) |
| `station_breakdown` | S12 reports **down** for all 50 of its events, while S11 and S13 stay idle. Watch firings: `starvation` at S12 and `tool_wear` at S3 |
| `buffer_congestion`, `torque_degradation`, `high_humidity`, `rushed_checklist`, `sensor_dropout`, `bad_material_lot` | **NOT RUN** in the final state. The first attempt starved the host (§13, #10). After the fix, a rerun was declined by the tool-permission layer. In an earlier stack build, `high_humidity` produced the `paint_env` firing at S18 (83.4% zone humidity, +18.3 over ambient) |

The live simulation is a **scripted perturbation of the recorded replay**, not physics.
Physics lives in `offline/live_twin`, which drives the counterfactual and the evaluations.
Neighbouring stations therefore do not starve or block in response to the perturbation
during a live sim. The live map mostly shows idle and starved, because the replay carries
each station's state from before the unit arrived.

## 8. ML findings

Reproduced from `data/plant_a` using the numpy scorer, not trusted from the docs:

| Metric (plant A, test split) | Value |
|---|---|
| Threshold | 0.4060785366, **selected on validation** |
| Alerts / TP | 178 / 63 |
| Precision / recall | 0.354 / 0.276 |
| Alerts per shift | 8.48 (budget 6: the tied plateau is admitted whole) |
| PR-AUC / ROC-AUC | 0.2559 / 0.8831 (oracle ceiling 0.481) |
| Brier | 0.0206 (base rate 0.0252) |
| Lead time | min 9.0 min, median 11.2 min, all positive |

Checks:
- **Split is chronological:** train days 1–14, validate 15–21, test 22–28.
- **No leakage:** no feature is taken from a station after the S20 cutoff.
- **Feature order is identical** between training and serving.
- **Scorer parity:** numpy vs the trained model agree to 3.9e-16; explanations are
  additive to 8.9e-16.
- **Known collapse, reproduced:** all 178 alerted units share raw 0.880117 / calibrated
  0.406079 through **one** leaf path in all 32 trees (root split `paint_buffer_dwell_s`).
  The model finds the at-risk set but cannot rank within it. The presentation shows a
  band rather than a rank; the model was not retrained.
- **Plant B:** its threshold of 0.1898 was chosen on validation (ADR-062), giving 1.19
  alerts per shift on test. Not retuned.
- **XGBoost challenger: NOT PROMOTED on either plant** (ADR-061/062). The registry shows
  both as `challenger`.

## 9. Bottleneck and station-state findings

- **Bottleneck detection accuracy** (self-consistency against a 2-hour sustained truth):
  - plant A 85.9%, plant B 90.2%;
  - neither reads 100%;
  - both are unchanged by the plant B window move.
- **Independent benchmark** (`docs/BOTTLENECK_BENCHMARK.md`):
  - The `plant_a/baseline` rerun at `06080a7` **reproduced its earlier numbers exactly**
    (top-1 0.93).
  - The run also measured `s12_constraint`, which had been listed as not run. Every
    rate-based detector scores top-1 1.00 on that engineered, obvious constraint, which
    is the expected result. The doc says so.
  - The 12-hour rerun of the remaining scenarios was stopped: it was starving the host,
    and its inputs had not changed.
- **Found and fixed:** `/api/shift/summary` reported **"detector agreement 100%"** for
  plant B's last shift. That shift had 32 windows and **zero** bottleneck moves, so it
  was n=1 rather than an accuracy. A no-move shift now returns `null` plus a note
  (§13, #12).
- **State rules** (`line_model`): STARVED means an empty input buffer; BLOCKED means a
  full output buffer; DOWN comes from the failure process. All three were verified in the
  code.
- **Active share** counts working plus down time.

## 10. Agentic findings

| Check | Result |
|---|---|
| Mode | **agent** (OpenRouter Nemotron, key present, loaded from `.env.local`) |
| Deterministic path: every intent | shift_intel 5/5 · handover 7/7 · incident 5/5 · starvation 1/1 · paint_env 4/4 · tool_wear 4/4 · whatif 4/4 · ask 8/8 claims verified. Every run is labelled **DETERMINISTIC**, and none is presented as AI output |
| Start a run as supervisor or anonymous | 403 / 403 |
| `acting_role: system` requested by a manager | capped to manager |
| Worker credentials | a per-run session in env `PULSETWIN_API_TOKEN`, with TTL = the worker stale limit. Not on argv, not in logs |
| Live agent run (shift_intel) | **Defect found.** The model looped on `simulate_whatif` for 24 tool calls and hit the wall-clock cap with no answer. **Fixed:** a closing turn withdraws the tools at the last iteration or the wall-clock reserve (75 s), and 2 unit tests cover it. **Not re-verified live.** The free tier allows 50 requests per day, and further stack runs were declined |
| Watch → agent | agent runs are already capped per simulation (`max_open_investigations`). Twin **forecast** jobs were not capped until this audit (§13, #10) |

## 11. Security findings

| Finding | Status |
|---|---|
| **Dev mode trusted `X-PulseTwin-Role: system` from anyone** | **Fixed.** Session mode is used in compose and in Render; the header can only lower the role (ADR-063) |
| GenAI worker ran with a header role | **Fixed.** It now uses its own short-lived session token, passed in the environment |
| Provider key in the frontend export, backend logs, job logs or tracked files | **None found** (a key-prefix scan across `frontend/out`, container logs, `/tmp`, `artifacts/` and `git grep`) |
| `.env.local` | gitignored (`.gitignore:20`) |
| Operator privacy | containment emits `same_operator_group` only; the privacy tests pass on both plants, including plant B, which had previously been skipped by its empty window |
| SQL | parameterised. The two new aggregation queries take only column names from the code's own `VALID_RANGES`/allowlist, never from the request |
| Demo credentials | fixed demo accounts, documented as such; the store is process-local (one worker) |
| **Remaining** | Session mode is demo authentication, not OIDC; the JWT mode is the upgrade path (`docs/SECURITY.md`). There is no rate limit on `/api/auth/login` beyond the global role:IP limiter |

## 12. Performance

| Item | Before | After |
|---|---|---|
| Live producer throughput | ~7.5 events/s | **~17 events/s** (one transaction per event) |
| `/api/system/data-quality` | 2.3–3.7 s | **~1 s** (one grouped SQL query; output verified identical) |
| `/api/stations/stats` | 0.88 s | **0.47 s** (SQL aggregation with the interval) |
| API median (40 routes) | 37 ms | – |
| Backend idle RSS | 82 MB | 56–82 MB |
| Host saturation under a scenario sweep | 32 forecast jobs, 2.47 GB, API down | capped at 2 jobs |
| First-load JS | 232–344 kB per route (106 kB shared) | – |
| DOMContentLoaded | median 40 ms, max 834 ms | – |

## 13. Bugs found

| # | Severity | Bug | Where it lived |
|---|---|---|---|
| 1 | **Critical (security)** | A bare role header granted any role, including system | `serve/middleware/auth.py` in dev mode on a shared deployment |
| 2 | High | Replay alerts appeared at S1, 20 stations before the model could score the unit; containment returned 409 | `offline/build_artifact.py` stream |
| 3 | High | float4 risk columns rounded the threshold above the plateau, so every alert got "Monitor" | Postgres schema |
| 4 | High | `/api/alerts/{unit}/status?run_id=` returned 500: column missing in Postgres | migrations vs `_OPS_ADDED_COLUMNS` |
| 5 | Medium | Mean time to acknowledge was 20.8 M s: a replay clock subtracted from a wall clock | `serve/routers/simulations.py` |
| 6 | Medium | The alert feed showed one card per station event, not per vehicle | `frontend/app/supervisor/page.tsx` |
| 7 | Medium | Live producer throughput was capped near 7.5 events/s | `serve/live_simulation.py` |
| 8 | Medium | `stations/stats` and `data-quality` took 1–3.7 s | Python-side aggregation |
| 9 | Medium | The agent could burn every tool call and time out with no answer | `genai/loop.py` |
| 10 | **High (availability)** | Uncapped twin forecast jobs (32 × ~160 MB) starved the host; the API and UI stopped answering, and the Docker engine returned 500s | `serve/routers/proposals.py` |
| 11 | High (demo) | Plant B's replay day had no alert: no alert feed, no explanations, no containment, and 10 failing tests | replay-window rule vs the ADR-062 threshold |
| 12 | Medium | Plant B's shift summary read "detector agreement 100%" | `serve/routers/intelligence.py` |
| 13 | Low | `/overview` issued a manager-gated request for anonymous users, so the public landing view logged a red 403 in the console | `frontend/app/overview/page.tsx` |
| 14 | Low | Mobile: KPI values truncated, axis ticks collided, the sign-in chip clipped, value-bridge labels overlapped; the account button had no accessible name | `Kpi.tsx`, manager chart, `IngestionLagChart.tsx`, `AppShell.tsx`, leadership |
| 15 | Medium (data) | A stray `plant_a_defect_risk_reliability_test` row (v99, no metrics) is **champion** in the live registry. Ops shows PR-AUC "—" | Postgres volume state from an ad-hoc probe on 2026-09-21; no code writes it |

## 14. Bugs fixed

Fixed: 1–14. Each fix is pinned as follows.

| # | Fix | Pinned by |
|---|---|---|
| 1 | `PULSETWIN_AUTH_MODE=session`; per-run agent tokens | `test_auth_session_mode.py` |
| 2 | `_risk_fields` emits nothing before `prediction_ts`; artifacts rebuilt | `test_stream_causality.py` |
| 3 | migration 017 (DOUBLE PRECISION) | `test_risk_precision.py` |
| 4 | migration 018 | `test_risk_precision.py` (every added column has a PG migration) |
| 5 | migration 019 `opened_wall_at` | `test_alert_metrics_clock.py`; E2E reads 0.09 s |
| 6 | feed deduplicated by `unit_id` | browser run |
| 7 | `db.execute_batch` per event | measured |
| 8 | SQL aggregation | output diffed against the old code |
| 9 | closing turn | `test_genai_loop.py` (2 tests) |
| 10 | `MAX_FORECAST_JOBS = 2`; past the cap → `failed: twin busy`, which the UI renders as "decide on the evidence alone" | `test_forecast_jobs_are_capped_and_a_refusal_says_why` |
| 11 | replay window prefers a day with an alert: selected on model output, no metric moved | the 10 previously failing tests |
| 12 | a no-move shift returns `detector_agreement: null` plus a note | `test_a_shift_without_a_bottleneck_move_reports_no_agreement` |
| 13 | role check before the fetch | browser matrix: 0 console errors |
| 14 | `text-lg sm:text-2xl`; `preserveStartEnd` + `minTickGap`; compact chip; `COMPACT_USD` labels; `aria-label` on the account button | screenshots, overflow probe |

Bug 15 is **not fixed**; see §15.1.

## 15. Remaining limitations

1. **Registry repair (bug 15), for you to run.** The tool permission for this write was
   declined, so nothing has been changed. It is reversible, and it retires the row rather
   than deleting it:
   ```sql
   -- docker exec -it pulsetwinaccenture-postgres-1 psql -U <user> -d <db>
   BEGIN;
   UPDATE model_registry SET status='retired', retired_at=now(),
          notes = notes || ' -- retired by the final audit: an ad-hoc probe row (ADR-063)'
    WHERE model_id='plant_a_defect_risk_reliability_test' AND status='champion';
   UPDATE model_registry SET status='champion', retired_at=NULL
    WHERE model_id='plant_a_defect_risk_committed';
   COMMIT;
   ```
   Scoring is not affected: the scorer loads the committed artifact, not the registry row.
2. **Six incident scenarios were not run** in the final state (§7).
3. **The agent's closing turn has not been verified live.** The free provider tier allows
   50 requests per day, and a run spends 7–12 of them.
4. **The live simulation is a scripted replay perturbation, not physics.** Blocked and
   down states rarely appear on the live map unless the scenario injects them.
5. **Plant A cannot rank within its alert set:** 178 alerts share one score.
   PR-AUC is 0.256, which is 53% of the oracle ceiling of 0.481.
6. **Plant B's model is weak** (PR-AUC 0.052) and fires only 1.19 times per shift on test.
   Its demo day was moved to where its alerts are (2026-01-31, a Sunday cluster), so it
   no longer shows a humidity-peak day.
7. **The committed artifact's `stream_index.byte_offset`** is correct only for artifacts
   built after the `newline=""` fix; plant A was built before it. The server probes around
   this.
8. **The bottleneck benchmark covers only `plant_a` baseline and `s12_constraint`.**
9. **Demo authentication:** fixed accounts and a process-local session store.

## 16. Industry gap analysis

Compared against commercial manufacturing digital twins and quality-AI products (Siemens
Opcenter/Insights Hub, Rockwell FactoryTalk, Sight Machine, Instrumental, Braincube):

| Capability | Industry norm | PulseTwin | Gap |
|---|---|---|---|
| Data ingestion | OPC-UA / MQTT Sparkplug connectors, historian backfill | MQTT ingestion deployable; integration map written (ADR-058). No OPC-UA client | real connectors |
| Defect prediction | per-station SPC plus ML, retrained on a cadence | one gradient-boosted model per plant, oracle-ceiling framing, gated challenger. Retraining removed (ADR-053) | a retraining pipeline with human approval |
| Bottleneck analytics | active-period / shifting bottleneck | the same method, benchmarked against 6 alternatives with CIs | ahead of most products on evidence |
| Containment / genealogy | MES genealogy, lot holds | genealogy-based containment with group-level privacy | MES write-back (deliberately recommend-only) |
| What-if | discrete-event sim (Plant Simulation, FlexSim) | a precomputed DES grid plus a Monte Carlo counterfactual per proposal | interactive custom DES runs |
| GenAI / agents | copilots over dashboards, usually ungrounded | a closed tool registry plus a deterministic verifier: every number in an answer is re-checked against the API | ahead on grounding; behind on latency (a 70 s agent run) |
| Identity / audit | SSO / OIDC, e-signatures (21 CFR 11-style) | demo sessions, a JWT path, an audit trail | SSO, e-signature |
| Scale | multi-plant, HA | single worker; cost model for 1/5/20 plants | HA deployment, a shared session store |

## 17. Production-readiness verdict

**Hackathon demo: READY**, once the frontend is redeployed and the registry SQL is run
(§18). **Production: NOT READY.** It would need:
- real authentication (OIDC via the existing JWT mode);
- real plant data connectors;
- a shared session store and a job queue before running more than one worker;
- a model retraining and approval loop;
- load testing beyond a single user.

None of these is a hidden defect; each is documented as the upgrade path.

## 18. Next actions

1. `docker compose -f docker-compose.prod.yml up -d --build`. This deploys the
   value-bridge label fix, which is built and checked but not yet in the running image.
2. Run the registry SQL in §15.1, then check that `/ops` model health shows
   `plant_a_defect_risk_committed`.
3. Run the six remaining incident scenarios:
   `python incidents.py http://localhost:7860 out.json 1500` (from the audit scratchpad),
   or the Supervisor view's scenario picker.
4. Run one live agent investigation (Manager → Generate shift intelligence) to confirm the
   closing turn produces a verified finding. It costs about 10 free-tier requests.
5. Rerun the full suite once (31 min).
6. Commit when you are ready. Nothing from this audit is committed.
