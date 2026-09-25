# PulseTwin — Build State

> **READ THIS FIRST, EVERY SESSION.** Then `docs/SPEC.md` for the build target.
> Update the ledger and the "next action" line at the end of every phase.

## Homepage photo update — 2026-09-25

- Replaced the landing chassis illustration with the user's BMW photograph,
  treated in the existing blue/navy palette and shipped as a local 134 KB WebP.
- Added a concept-labeled vehicle introduction with Quality, Assembly, and
  Traceability controls, SVG connectors, subtle pointer motion and hover zoom.
  Keyboard, touch and reduced-motion paths are supported; no simulated telemetry
  is presented as live data. Added header workspace access and refined link/card
  hover states while retaining the established light/dark section rhythm.
- Verified `npm run typecheck`, the production static export, and local Chrome
  checks at 320, 390, 768, 1024, 1280 and 1440 pixels. No horizontal overflow or
  browser exceptions. Narrow-screen header wrapping and overlay text placement
  were corrected during visual review. QA output: `artifacts/homepage-preview/`.
- Image treatment and final prompt: `docs/bmw-homepage-image.md`.
- Layout refinement: reduced the vehicle panel to at most 560px, cropped excess
  sky with a 3:2 viewport, and blended the edges into the white canvas. Moved the
  explanatory text beneath the photo, simplified the controls and reduced marker
  graphics while keeping 44px touch targets. The header, hero and lower sections
  now share a 1280px container. TypeScript and the six-width interaction checks
  pass, including keyboard, touch, reduced motion and marker crop boundaries.
- Refreshed the local Docker frontend on port 3000 with
  `docker compose -f docker-compose.prod.yml up -d --no-deps --build frontend`.
  The running container had retained its previous build despite the workspace
  changes. Verified the new photo, interactive controls and workspace navigation
  directly at `http://localhost:3000/`, with no browser exceptions.
- Next action for this update: ready for visual review on port 3000; no external
  deployment performed.

---

## Clock — Round 2 CLOSED, Finals upgrade IN PROGRESS

| | |
|---|---|
| Bootstrap session | 2026-08-31 |
| Round 2 build closed | 2026-09-02 |
| Original budget | Under 24 hours, deadline 1 September 2026 |
| Finals upgrade (`pulsetwin_final_prompt.md` §Finals, phases F1-F8) | started, then stalled mid-build; resumed and closed out 2026-09-16 |

**Everything on the Round 2 ladder was built — Tier 0, Tier 1 and Tier 2.** Nothing from
the original brief was cut. The de-scope ladder below is kept as the historical record of
how that work was ordered, not as an outstanding plan.

The ladder did its job: it forced the Supervisor view and the honest-metrics work ahead of
the what-if console and the instrumentation planner, so the parts a judge grades hardest
were finished first and the optional ones landed on top rather than instead.

---

## Finals upgrade — Phases F1-F8

The finals brief (real-time ingestion, MLOps lifecycle, RBAC/audit, graceful degradation,
a 4th Ops persona, K8s/Terraform, and eight new docs) was substantially built in an
earlier session but left in an inconsistent state: most backend and infrastructure work
existed, but 4 tests were red, Prometheus metrics were never wired despite the
dependencies being installed, `frontend/app/ops/page.tsx` did not exist at all despite 4
of its 7 components being built, and several of the finished pages had shared components
(`OfflineBanner`, `SensorDropoutAlert`) imported but never rendered. This session's job
was auditing what was actually there (by reading source, not trusting import lines) and
closing every gap found.

| Phase | What shipped | Evidence |
|---|---|---|
| F1 Postgres | `migrations/001-004`, `serve/db_postgres.py`, `offline/load_to_postgres.py`, SQLite fallback for local dev | ADR-034 (a real FK bug the migration caught); `tests/test_api.py` passes against the SQLite path |
| F2 Ingestion | `ingestion/` — unified namespace, MQTT publisher with fault injection (dropout/late-arrival/duplicate/corrupt) and a live `control/dropout` topic for demo use, subscriber with dedup + reordering, per-station data-quality scoring | `tests/test_ingestion.py` (fixed a completeness-scoring bug this session — see below) |
| F3 MLOps | `mlops/` — PSI + calibration + coverage drift detection, champion-challenger retrainer, model registry, SPC fallback | ADR-035 (published the textbook Nelson-rule fallback's real, poor shadow performance rather than a nicer invented number) |
| F4 Auth/RBAC/audit | `serve/middleware/auth.py` (role hierarchy), `audit.py` (every mutating action logged), `rate_limit.py`; frontend `RoleGate`, per-page role badges, and (new this session) an actual interactive role selector on `/ops` | `tests/test_auth.py`, `tests/test_audit.py` |
| F5 Graceful degradation | Backend: SPC fallback on a broken model file, Prometheus metrics (**wired this session** — `serve/metrics.py`, previously just an unused dependency), Grafana dashboard + Prometheus/Alertmanager configs (**written this session**, none previously existed). Frontend: `OfflineBanner`/`StaleDataIndicator`/`SensorDropoutAlert` **connected to live JSX this session** on `/supervisor` (they existed as dead imports before), and added to `/manager`, `/leadership`, `/ops` | `tests/test_graceful_degradation.py`, `tests/test_sensor_dropout.py` (fixed a test-fixture bug and a real "latest row" ordering bug this session — see below) |
| F6 Ops view | `frontend/app/ops/page.tsx` **did not exist before this session** despite 4 of its 7 components already being built. Built the page plus the 3 missing components (`SensorHealthGrid`, `AuditLogViewer`, `RetrainingConsole`) | manual review; `npx tsc --noEmit` clean across the whole frontend |
| F7 K8s + Terraform | `infrastructure/kubernetes/*.yaml` (namespace, deployments, HPA, OT/IT network policy) already existed and is thorough. `infrastructure/terraform/main.tf` and `infrastructure/monitoring/{prometheus.yml,alertmanager.yml,grafana-dashboards/pulsetwin-overview.json}` **did not exist; written this session** | file review; YAML/JSON parse-validated |
| F8 Docs | All eight — `PRODUCTION_ARCHITECTURE.md`, `SECURITY.md`, `MLOPS_LIFECYCLE.md`, `DATA_GOVERNANCE.md`, `CHANGE_MANAGEMENT.md`, `SCALING_COST_MODEL.md`, `INCIDENT_RUNBOOK.md`, `FINALS_DEMO_SCRIPT.md` — **did not exist; written this session**, sourced against real ADRs/configs, not invented figures | this ledger |

### Real bugs found and fixed while closing out the finals upgrade

16. **`DataQualityTracker.score()` under-counted completeness for a station that hadn't
    been running for a full window yet**, scoring a perfectly healthy 20-reading stream
    at 86.7% instead of ~100% because it divided by a hypothetical full-`window_s` count
    rather than the count expected over the span actually observed. Fixed to measure
    expected count against `events[-1] - events[0]`, which converges to the original
    intent once the window is genuinely full. Caught by `tests/test_ingestion.py`.
17. **`test_sensor_dropout.py` corrupted its own fixture.** Two of its four tests opened a
    second, ad-hoc `TestClient(app)` instead of reusing the module-scoped one — re-entering
    `serve.main`'s lifespan on the same process-global `STATE` and closing
    `app.state.ops` out from under the next test, which then hit
    `sqlite3.ProgrammingError: Cannot operate on a closed database`. Fixed by routing
    every test through the shared fixture, matching every other test module's convention.
18. **`sensor_health`'s "latest row per (station, sensor)" query ordered by `timestamp`
    alone**, which only has millisecond resolution — two status transitions for the same
    sensor landing in the same millisecond (exactly what a fast test, or a real burst of
    ingestion events, produces) picked the wrong "latest" row via SQLite's undefined tie-
    break order. Fixed to order by `id` (strictly monotonic on insert) instead.
19. **Prometheus metrics were never wired**, despite `prometheus-client` and
    `prometheus-fastapi-instrumentator` being installed specifically for this (ADR-033)
    and the finals brief listing seven named series. `serve/metrics.py` now defines them;
    `serve/main.py`, `system.py`, and `model_health.py` update them at the actual call
    sites (`Instrumentator().instrument(app).expose(app)` adds the generic HTTP series
    alongside).
20. **Two import-wall gaps**: `serve/middleware/rate_limit.py` needs `starlette` (fastapi
    has no ASGI middleware base of its own) and `serve/routers/admin.py` needs
    `subprocess` (to spawn the retrain job as a detached process rather than importing
    `mlops` into `serve/`). Both are legitimate, both are now ADR-036, both are in
    `tests/test_serve_imports.py`'s `ALLOWED` set.
21. **`frontend/app/supervisor/page.tsx` imported `OfflineBanner` and
    `SensorDropoutAlert` and declared their backing state, but never rendered either
    component**, and `lastKnownAt` was never set. The dropout-polling `useEffect` was
    real; only the JSX to show it was missing. Now both render, `lastKnownAt` updates on
    every stream event, and a `retryNonce` state re-opens the SSE connection so
    `OfflineBanner`'s [Retry Now] button actually does something.
22. **`serve/Dockerfile` never copied `migrations/`**, even though
    `migrations/migrate.py`'s own docstring says the `migrate` service in
    `docker-compose.prod.yml` runs on that exact image with its `CMD` overridden — that
    service would have built successfully and then crashed at container start with
    `ModuleNotFoundError`. Added `COPY migrations/ ./migrations/`.
23. **Wiring `Instrumentator().instrument(app)` (new this session) crashed almost every
    endpoint** with `AttributeError: '_IncludedRouter' object has no attribute 'path'` —
    this dev environment's ambient FastAPI (0.139.0) is newer than
    `prometheus-fastapi-instrumentator==7.0.0` (pinned in `serve/requirements.txt`)
    understands internally. Bumped the pin to `8.1.0`, which resolves cleanly against a
    modern FastAPI/Starlette (`pip check` clean) and fixed every affected test.
24. **My own `from serve import metrics` collided with `serve/main.py`'s pre-existing
    `def metrics(...)` — the `GET /api/metrics` endpoint function**, both bound to the
    same module-scope name, and the later definition silently won, so every
    `metrics.predictions_total`-style call raised `AttributeError: 'function' object has
    no attribute 'predictions_total'`. Imported as `from serve import metrics as
    prom_metrics` instead.

### Found by auditing the docs against the code (2026-09-16, second pass)

A pass over every `.md` file, checking each factual claim against the source or the
committed artifact rather than against another document. Nothing here changed behaviour;
all of it changed what the repo *claims* about its behaviour, which for a project graded
on credibility is the same class of defect.

25. **Five documents disagreed about the headline metrics.** `README.md` and
    `docs/MODEL_CARD.md` matched the artifact (bottleneck accuracy 85.9% / 90.2%);
    `docs/STATE.md`, `docs/ASSUMPTIONS.md` and `docs/DEMO_SCRIPT.md` (in a line a presenter
    reads aloud to judges) still carried 87.9% / 79.1% from an earlier build, plus
    53.8 / 45.4 UPH against a measured 53.7 / 45.3. The artifact was rebuilt 2026-09-02 and
    only two of the five docs were updated. All five now read from
    `artifacts/pulsetwin.db`, and each says so, so the next rebuild has one obvious list
    of files to update together.
26. **`docs/SECURITY.md`'s RBAC table claimed gating that does not exist.** It listed
    `/api/stations/stats` as manager-gated and implied supervisor-level reads were
    role-checked. In fact **thirteen read endpoints have no role dependency at all**,
    including `/api/units/{id}`, which returns a unit's risk, factors and full journey.
    The table is now generated from the actual `Depends(require_role(...))` declarations
    and the gap is stated as a known gap, with the reason it has not been closed (with no
    authentication in front of the header, a read gate is decorative) and the condition
    under which it must be — the same change that adds real auth.
27. **The operator-privacy primitive was described as something stronger than it is.**
    ADR-017 said "salted hash", `configs/data_governance.yaml` said `sha256`;
    `offline/build_artifact.py` actually uses `pandas.util.hash_pandas_object(...) % 8`
    — unsalted, non-cryptographic. The privacy outcome still holds (the raw ID is never
    stored; 8 buckets make the label describe a group), so no code changed, but a privacy
    claim overstated relative to the implementation is exactly what an auditor pulls on.
    Corrected in all three places.
28. **`docs/SCALING_COST_MODEL.md` priced sensor retrofit from an estimate when the
    artifact had the measured answer.** It assumed "~1 station per plant at a $2,700
    average". The `instrumentation_plan` table actually says: plant A recommends 1 of 4
    candidates ($3,200), plant B recommends 2 of 8 ($6,000), with measured annual values
    and 0.7-1.6 month paybacks. Rewritten from the table; the interesting number — that
    the planner avoids ~$280,000 of capex at 20-plant scale by deferring stations that
    score exactly zero uplift — only appears once the real rows are used.
29. **The soft-sensor ablation was quoted without its own recorded caveat.** The artifact
    stores `"improvement is within confidence intervals - reported as measured"` alongside
    the +0.060 PR-AUC figure; `README.md` and `docs/MODEL_CARD.md` quoted the point
    estimate alone. Both now carry the caveat, as does the prediction-interval
    under-coverage (0.55-0.66 measured against a nominal 90% on tool-wear signals).
30. **The budget threshold overshoots its own budget and no document said so.** Plant A's
    operating threshold is fitted for 6 alerts/shift and realises **8.5** on the test
    split — a ~40% overshoot of a constraint the whole alarm-budget argument rests on.
    `README.md` had the number in a table; nothing explained it. Now called out as a
    generalisation gap in `docs/MODEL_CARD.md`'s failure modes, with the fix (re-fit on a
    rolling window rather than freezing at build time).

**All 4 originally-failing tests, all tests broken while wiring Prometheus metrics, and
the full 226-test suite pass as of this session's close** (`OMP_NUM_THREADS=1 pytest
tests/ -q` → `226 passed`). Findings 25-30 are documentation corrections and changed no
code, so the suite is unaffected by them.

### Found by the end-to-end QA pass (2026-09-16, third pass)

A validation pass that **ran** the system rather than reading it: a live backend probed
across every endpoint × every role, a real browser (Playwright/Chromium) driving all six
routes with network and console captured, the MLOps lifecycle exercised end to end through
the API, and the ingestion modules executed directly. Full write-up with reproductions in
`reports/qa-audit-report.md`; baseline in `reports/baseline-test-report.md`.

**The 226-test suite was green throughout and caught none of these.** All fifteen live in a
**seam between two components** — detector→API, API→page, publisher→subscriber,
image→route table — and the suite tests components. Four of them silently disabled a
feature `docs/FINALS_DEMO_SCRIPT.md` depends on.

31. **`frontend/Dockerfile` served the landing page for every persona route.**
    `CMD ["serve", "-s", "out", ...]` — `-s` is single-page-app mode, which rewrites every
    non-file path to `index.html`. Next's static export is multi-page. `/supervisor` and
    `/` returned byte-identical 8,256-byte responses; without `-s` the five routes return
    9454/11664/7185/9560/8377 bytes. **`make demo` shipped a UI with no reachable persona
    view.** Invisible in dev (`next dev` routes itself) and the container had never run.
32. **`/api/model/health` discarded 19 of 20 PSI features.** `_latest_run` kept only rows
    matching `rows[0]["timestamp"]`, but `drift_detector` inserts one row per feature and
    lets the column DEFAULT stamp each — twenty rows, twenty distinct milliseconds. The
    Ops Drift Monitor rendered `0 / 1 features shifted · healthy · "No action needed"`
    from a run whose own verdict was `10/20 shifted, worst hour_of_day PSI 4.691,
    WARNING`. Fixed to latest-per-feature ordered by `id`, the pattern `system.py` already
    uses for `sensor_health`. Also: `features_shifted` counted only the alert band
    (PSI>0.2) where the detector counts warning+alert, so the two disagreed 7 vs 10.
33. **`/supervisor` could never display a sensor dropout.** The page pins itself to
    `supervisor` and polled `/api/system/health`, which requires `manager` — a 403 every
    10s, swallowed by the `.catch()` that exists to survive transient failures. Confirmed
    in-browser: 4 non-200 calls, all the same 403. This is demo Act 2. Fixed with a narrow
    `GET /api/system/sensor-dropouts` at supervisor level rather than by widening the
    `/api/system/health` gate — least privilege is the point of SECURITY.md's table.
34. **The Ops audit-log panel was permanently empty.** `new URLSearchParams({role:
    undefined})` stringifies to the literal `role=undefined`, which the backend matched as
    `WHERE user_role = 'undefined'`: 0 rows against a table holding 16. This is demo Act 4
    ("every action is traceable, IATF 16949").
35. **Model promotion was cosmetic.** `docs/MLOPS_LIFECYCLE.md` said
    `POST /api/admin/promote-model` "is the only code path that changes what
    /api/predict/defect actually serves". It changed a registry row and nothing else — the
    same feature vector returned `0.011457011658095886` before and after promoting a
    different model. Two causes: no hot-swap of `STATE["scorers"]`, and every challenger
    exporting to one reused `model_<plant>_challenger.npz` so each retrain overwrote the
    weights behind rows already marked `champion`. Fixed: one .npz per challenger (capped
    at 10, pruned), and promote/rollback load `artifact_path` into the live scorer and
    report the outcome. Runtime-only by design — it does not rewrite the committed
    artifact (ADR-027). Doc corrected rather than quietly rewritten.
36. **`/api/system/ingestion-lag` 500'd the moment any sensor dropped out.**
    `sensor_health.timestamp` is UTC-aware (column DEFAULT), `last_reading_at` is naive
    (written from a replayed event); subtracting them raises `TypeError` and the handler
    caught `ValueError` only. The endpoint looked healthy right up until the exact state
    demo Act 2 creates on purpose.
37. **The 60-second reorder window buffered nothing.** `flush_ready` cut on
    `event_ts <= wall_clock_now - 60`, but the publisher replays the artifact's *simulated*
    timestamps (2026-01-28) — true for every record on arrival, so the heap drained
    immediately and late arrivals were written in arrival order. The F2 acceptance
    criterion "late-arriving messages are correctly reordered" was not met. Fixed with an
    event-time watermark plus a wall-clock idle drain.
38. **Every data-quality score was capped at 0.70.** Same root cause: timeliness compared
    arrival against the simulated event timestamp, so every reading looked 231 days late
    and `timeliness` was 0.0 always. The publisher now stamps `emitted_at` (wall clock at
    the edge gateway) beside `ts` — the Source/Server timestamp pair OPC-UA itself carries
    — and the subscriber measures against that.
39. **A failed retrain left no registry trace.** `mark_failed()` was UPDATE-only but
    `retrainer` calls it from an `except` block that usually fires *before* `register()`
    inserts the row, so it matched zero rows. `status='failed'` is one of the four states
    `migrations/003` defines and it was unreachable on the path that produces it.
40. **Models reported a negative age.** `time.strftime` is local naive; the column
    DEFAULTs are UTC; `model_health` computes `age_days` against `now(timezone.utc)`. The
    Ops view displayed **AGE -1d**. The same mixed clocks feed `ORDER BY retired_at DESC`,
    which is how rollback picks its target. UTC at all seven write sites now.
41. **429 and 403 reached the browser as opaque CORS errors.** `add_middleware` pushes onto
    the *top* of the stack, so `RateLimitMiddleware` — added last — wrapped CORS and its
    early return never passed through it. A rate-limited or role-denied browser could not
    distinguish the rejection from the backend being down, which is exactly what `/ops`'s
    "Viewing as" selector exists to demonstrate.
42. **`model_registry.rollback()` recorded no approver** while `admin.py`'s copy did — the
    drift the module docstring explicitly warns about, in the column an IATF 16949 audit
    would read.
43. **A negative `limit` disabled the cap.** SQLite treats `LIMIT -1` as no limit;
    `/api/units?limit=-1` returned all 1423 rows, `/api/bottleneck/history?limit=-1` 2688.
    `audit.py` already clamped, `main.py` did not.
44. **Unauthenticated input could mint unbounded Prometheus labels.**
    `predictions_total.labels(plant_id=...)` incremented *before* validation, so any
    made-up `plant_id` created a permanent new time series.
45. **The retrofit capital plan was ranked upside down.** `/api/economics/instrumentation-
    plan` used `ORDER BY payback_months`, and SQLite sorts NULLs FIRST -- NULL being
    exactly the candidates worth $0. The Leadership view renders the rows in API order, so
    plant A listed its single fundable retrofit (S10, $30,792/yr, 1.2-month payback) **4th
    of 4**, below three DEFER rows, and plant B listed its two **7th and 8th of 8**. The
    endpoint's own docstring calls it "ranking by measured marginal model value per
    dollar", and `docs/SCALING_COST_MODEL.md` builds its capex argument on this table.
    Second defect in the same line: Postgres sorts NULLs LAST, so the identical request
    returned a different order per backend -- exactly what `serve/db.py` exists to prevent.
    Fixed with `ORDER BY (payback_months IS NULL), payback_months`, which both dialects
    accept (`NULLS LAST` does not work -- SQLite lacks it).

**`250 passed, 0 failed`** after the fixes, confirmed by two independent full runs against
separate ops databases (16m53s and 16m41s) — the 226 pre-existing tests plus a new
`tests/test_qa_regressions.py` (24 tests, one per defect, each reproduced before it was
fixed). Two of them assert against non-Python files (a Dockerfile `CMD`, a TypeScript
query-string builder): there is no JS test runner here and both defects silently emptied a
headline feature, so a text assertion beats none.

**Verified for the first time this pass:** all four persona views rendering real data in a
real browser (item 3 of the NEXT ACTION list below), and the full F5 graceful-degradation
cycle — backend killed with the browser open, `TWIN OFFLINE` banner in 1s with the line map
and station tiles retained, backend restarted, banner cleared in 1s and `SSE LIVE` restored.
That was the only end-to-end acceptance criterion in the finals brief that needed no fix.

**Twelve further issues were found and deliberately NOT fixed** — §6 of
`reports/qa-audit-report.md` says why for each. The one that matters most:

> **The model that serves traffic is not in the registry.** `/api/predict/defect` scores
> with `artifacts/model_<plant>.npz`, built by `make train`; only `mlops/retrainer` ever
> calls `register()`. So the Ops view's "champion" has never scored a request, and the
> model that has, has no registry row, no drift attribution and no promotion record. This
> is the finding a judge could turn into a question — *"which model is actually running?"*
> — that the current answer does not survive. Fixing it is a design decision (should
> `build_artifact` register?), not a bug fix, so it is reported rather than patched.

**Resolved 2026-09-21 (production-readiness pass), and it was worse than the paragraph
above states.** Tracing the seam end to end found that a *promoted* model didn't survive a
restart either — `admin.py::_activate_scorer` correctly hot-swaps the live scorer AND
writes a real registry row on promotion, but `serve/main.py::lifespan` never read the
registry at startup, so a restart silently reverted to the committed artifact while
`model_registry` kept reporting the promoted challenger as champion. Fixed: startup now
asks `model_registry` for the current champion's `artifact_path` per plant and loads that,
falling back to the committed file only if no champion row exists or it is missing; the
committed artifact is also bootstrap-registered (real metrics, not placeholders) the first
time a plant is served with no prior row. ADR-041; `tests/test_model_registry_runtime.py`
(4 new tests, all against the real committed artifact, no mocks). **Named, not fixed:** a
promotion's in-memory hot-swap still reaches only the one pod that received the API call —
the documented 2-6 pod production topology needs either a post-promotion rolling restart
or a live cross-pod reconciliation signal, neither built. See ADR-041 alternative 2.

**Not verified this session:** `docker compose -f docker-compose.prod.yml up --build`
end-to-end. The Docker CLI is present in this sandbox but has no daemon behind it (no
`Docker Desktop.exe` process, no `com.docker.service`) — `docker compose up` fails
immediately with a named-pipe connection error, before any image even builds. Every
manifest and Dockerfile was reviewed by hand instead (catching bug #22 above); an actual
`up` run needs a machine with a working Docker daemon.

> *Postscript, 2026-09-16 (QA pass):* still true, and hand-review is demonstrably not
> equivalent. Bug #31 — `frontend/Dockerfile`'s `serve -s out` serving the landing page for
> all five persona routes — was found by **running that image's serve command against the
> built `out/`**, not by reading the line. The same `CMD` had been reviewed and passed
> twice. Treat every remaining "reviewed by hand" claim about the container stack as
> unverified rather than as evidence.

---

## Sessions after the finals close (2026-09-19 / 2026-09-20)

Two pieces of work sit on top of F1-F8: a **full frontend redesign** and a new **`genai/`
agentic triage tier**. **339 tests pass** (was 250).

### Frontend redesign (2026-09-19)

Tailwind v3 → **v4** (CSS-first `@theme`; `tailwind.config.ts` deleted), plus Radix
primitives, Motion, `next/font`, and a new `frontend/components/ui/` layer of 14 shared
primitives. All six routes and 13 components refactored onto it.

The largest single defect: **`tailwind.config.ts` named JetBrains Mono while nothing ever
fetched it**, so every machine rendered a different fallback. Now self-hosted via
`next/font`.

Measured after: 0 console errors and 0 failed requests on all six routes; 232 keyboard
stops all with a visible focus ring (the app previously had **zero** focus styles);
0 contrast failures; 0 horizontal overflow at 400px; **stream CLS 0 at 50×**; load CLS
0.043 (was 0.147).

### GenAI tier (2026-09-20) — ADR-037

`genai/` is a separate deployable, like `ingestion/` and `mlops/`. An agent investigates
through the READ-ONLY HTTP API and returns claims, each carrying the tool call and RFC
6901 pointer its number came from. `genai/verifier.py` — no model, no network, no clock —
re-checks them and **redacts or rejects** rather than flagging.
`genai/offline_triage.py` runs the same tools by fixed rules and passes the same gate, so
the feature works with no API key and doubles as an honest baseline.

**The import wall was NOT widened.** `genai` is absent from `ALLOWED`, so
`tests/test_serve_imports.py` already failed on any `import genai` in `serve/` and needed
no edit. Provider: Groq (`openai/gpt-oss-120b`), OpenAI-compatible, isolated to
`genai/client.py`.

### AI Operations Copilot (2026-09-20) — ADR-038, ADR-039

The agentic tier answered one question, once. This session turned it into a
**conversation**, widened what it can see, and added the deterministic evidence it
reasons over.

**Provider.** xAI Grok → **Groq `openai/gpt-oss-120b`**, same OpenAI wire protocol, no
second SDK (ADR-038 §1). `genai/capabilities.py` walks a five-rung degradation ladder when
the provider rejects `strict` / `json_schema` / `seed`, because a rejection is an HTTP 400
that used to fail the whole run. The settled rung is persisted and rendered.

**Conversation.** `triage_threads` + `thread_id`/`turn_index` on `triage_runs`.
**Citations never cross turns** — `call_id`s restart every run, so a replayed citation
could resolve against a different turn's transcript and be marked verified on a
coincidence. Only `carried_values` crosses, as a C3 prose exemption and nothing else,
server-derived and persisted for replay (ADR-039).

**Named actions.** Five intents (`ask`, `incident`, `handover`, `shift_intel`, `whatif`),
each with a deterministic plan in `genai/offline_triage.py` so every button works with no
credential. No NL intent classifier; station ids are always structured, never regexed.

**Progress.** Phases live on `ToolSpec.phase`; the router writes the first heartbeat
*before* spawning the worker. Polling, never token streaming — streaming would render
numbers before the verifier ran.

**Egress: 10 tools → 18** (ADR-038 §3). Admitted containment, alerts, alert triage, shift
summary, data quality, sensor dropouts, the evidence graph and the instrumentation plan.
Still excluded: `/api/units/{id}`, anything reading `checklists`, `/api/stream`,
`/api/audit/*`, `/api/admin/*`.

**New deterministic endpoints** (`serve/routers/intelligence.py`): `/api/alerts/triage`,
`/api/shift/summary`, `/api/system/data-quality`,
`/api/trace/evidence-graph/{unit_id}`. Computed in the backend on purpose — the verifier
can only check a claim against a tool result, so the tool result has to be trustworthy.

**Frontend.** New `/copilot` route plus a `CopilotDock` on all four persona views, a
`PhaseTrail`, an extracted `FindingView` shared with the ops console, an `AiTrustPanel`,
an `AlertTriageTable` on `/supervisor`, and an `EvidenceGraph` that encodes provenance in
colour + dash + texture and says `association, not cause` on its face.

### Bugs found while building the copilot

62. **`MetricsResponse` silently discarded six stored blobs.** `serve/main.py::metrics`
    splats all of `metrics_json` into the model and pydantic v2's `extra="ignore"` dropped
    everything undeclared — including `bottleneck_report` (forecast detail) and
    `soft_sensor_validation` (the masked-station R² ADR-031 rests on). Both computed,
    committed, cited in docs, and unreachable through any endpoint.
63. **`ingestion/data_quality.py` scored every weld reading invalid.**
    `VALID_RANGES["weld_current_a"]` was `(0, 2000)` while
    `offline/simulator/physics.py` generates ~8,200–8,900 A. All 8,610 weld readings in
    the artifact were being excluded from feature computation in the live path. Real spot
    welding is 8–15 kA; the bound was wrong by ~5×. Now 20 kA, with a test asserting the
    simulator's own output falls inside the ranges.
64. **The first `/api/alerts/triage` could not rank anything.** It triaged only
    `alert_fired = 1` rows, and that flag means `risk >= budget_threshold` by construction
    (0 of 141 rows violate it), so severity was a tautology and everything landed
    CRITICAL. Widened to the cost-optimal candidate set.
65. **The stored confidence interval carries no information.** Every distinct
    `calibrated_risk` maps to exactly one interval width across both plants — the interval
    is a deterministic function of the point estimate. Ranking on it was double-counting
    one number as two pieces of evidence. `LOW_CONFIDENCE` now keys on input quality;
    the interval is returned flagged `informative: false`.
66. **The evidence graph indicted all 30 stations.** It substring-matched signal names
    against factor names, so `torque_nm` matched that signal at every station. Features
    encode their station as an `s<N>_` prefix; parsing it collapsed a 70-node graph to 11.
67. **Triage keyed on the wrong station.** `current_station` is
    `MAX(genealogy.station_id)`, which for a finished unit is simply the last station on
    the line — identically for every row. Now keyed on the station the top-ranked factor
    names.
68. **An honest refusal was served as a blank panel.** The what-if plan correctly refused
    an uncovered station, but with no claims — and the verifier rejects a claimless
    finding, and a rejected run serves *nothing*. A refusal needs evidence too; it now
    cites the grid's own coverage.
69. **`CREATE TABLE IF NOT EXISTS` is a no-op against an existing table**, and the ops DB
    is a long-lived file in the system temp directory, not a rebuilt artifact. Every new
    column would have been missing at runtime on any machine that had started the server
    once. `_ensure_columns` now does an additive PRAGMA-then-ALTER pass — and the index
    over those columns had to move after it, because the first version died at startup
    with `no such column: thread_id`.
70. **Two `test_qa_regressions.py` tests passed exactly once per machine.** They inserted
    fixed `model_id` values into that same long-lived ops file and never deleted them, so
    every rerun failed on `UNIQUE constraint failed`. A test that only passes on a clean
    machine is a test that fails on the demo machine.
71. **A capability-cache leak between tests.** `remember()` writes a file under
    `PULSETWIN_JOBS_DIR`; clearing only the in-process memo let one test's degradation
    change the next test's starting rung, and wrote a degraded profile into the
    developer's real temp directory as a side effect of running the suite.

72. **The test suite tripped a production rate limit, and only when run whole.**
    `serve/middleware/rate_limit.py` keeps a sliding window per `role:ip`. Under
    TestClient every test is the same client IP and the whole suite runs in one process,
    so counters accumulate across FILES. Adding ~60 API-hitting tests pushed the suite
    past the 180/min leadership limit, and every test that happened to run after that
    point got a 429 whose body was then indexed as the success payload
    (`KeyError: 'current_model'`). Nine failures, all of them passing in isolation —
    the worst failure mode a test can have, because it looks like a regression in
    whichever file was unlucky. Fixed by resetting the window per test
    (`tests/conftest.py`, the repo's first) rather than by raising or disabling the
    limit: the limiter is real production behaviour and stays exactly as strict.
    Its counters moved to module scope to expose the reset hook — identical behaviour
    at `--workers 1`, and it avoids a test reaching into Starlette's middleware stack
    to find our own state.
### Measured against the live provider (2026-09-21)

The agent path talked to a real provider for the first time. Four facts came out of it
that no amount of stub testing would have produced, and all four changed code.

73. **Groq forbids `response_format` alongside `tools`, at every strictness.**
    `openai/gpt-oss-120b` answers L0, L1, L2 AND L3 with one identical 400 --
    `json mode cannot be combined with tool/function calling`, `param:
    response_format`. Only L4 works. The ladder converged correctly, but spent four
    rejected round trips learning one fact, and on a free tier those four count against
    the provider's own limit. `degrade()` now reads the error text and jumps straight to
    the first rung that omits the parameter. The rung ORDER still earns its place on an
    endpoint whose objection is the strictness rather than the parameter.

74. **The structured-output contract is carried entirely by the prompt on this
    provider**, because the schema cannot be sent at all. The system prompt had been
    written assuming `response_format` would pin the shape.

75. **A 400 can be the MODEL's fault, not the request's.** Groq validates tool syntax
    server-side and returns `code: tool_use_failed` with the text it could not parse.
    Ours read, verbatim:
    `{"name": "citaton", "arguments": The current bottleneck in **plant_a** is...` --
    the model invented a tool called `citaton` and wrote prose where the arguments JSON
    belongs, trying to emit its CITATION as a tool call. Degrading the request cannot fix
    that, so it was burning a ladder rung per occurrence and recording a misleading
    `capability_profile`. Now classified as `model_tool_call_invalid`, retried a bounded
    number of times without touching the ladder, and the prompt says in as many words
    that the final answer is an ordinary message and that no tool named `citation`
    exists.

76. **The free tier's binding limit is tokens, not requests.** 8,000 TPM against 1,000
    requests/day: one round trip of this agent costs ~3,200 tokens before any tool result
    (a 2,853-char system prompt plus ~10,000 chars of tool schemas, both re-sent every
    turn), so two or three iterations exhaust the minute. The loop retried once and slept
    a flat 5 seconds while the response header said the budget resets in 17.1s. It now
    reads `retry-after`, else `x-ratelimit-reset-tokens`, parses Groq's `17.145s` /
    `8m38.4s` duration format, waits that long up to a cap, and gives up early if the
    wait would exceed the run's wallclock budget rather than parking a detached worker.

### Bugs found in the two sessions before this one


**Frontend — found by code review plus a Chromium audit:**

46. **`cn()` silently stripped `text-numeric`.** tailwind-merge saw the `text-` prefix and
    filed it under text-colour, dropping it whenever another `text-*` followed. 71 call
    sites across 19 files lost their monospace face — inconsistently, which is worse than
    uniformly.
47. `RoleGate`'s `fallback ?? default` ignored an explicit `fallback={null}` (`??` does
    not short-circuit on null), wedging a lock card into a Panel header.
48. Radix `Select` renders its placeholder for `value=""`, so both audit-log filters
    showed blank triggers.
49. `DataTable`'s empty-rows early return dropped the footer — leadership's investment
    totals vanished on an empty plan.
50. Tooltips never opened on keyboard focus: most triggers are plain `<span>`s.
51. `motion`'s `layout` prop on up to 40 alert cards re-measured every box on each rAF
    flush — the exact cost the SSE buffering exists to avoid.
52. **`ink-faint` failed WCAG AA** at 3.66:1 on surface-2, and it carries every panel
    title, axis label and table header. Raised to `#88919f` (5.42–6.15:1).
53. Hydration mismatch from `Math.sin()` float precision in the chart skeleton.
54. The supervisor header **wrapped to two rows when the webfont swapped in**, dropping
    `MAIN` 32px. Load CLS 0.147 → 0.043.
55. The SSE cleanup called `setConnected(false)` on every speed change, so changing replay
    speed **flashed the full-width TWIN OFFLINE banner** — the degradation banner crying
    wolf mid-demo.

**Backend — found by running the spawned job, not by reading it:**

56. **`serve/db.py::open_ops_db()` opened SQLite with no WAL and no busy timeout.** That
    file is genuinely multi-process — the backend holds a long-lived handle while
    `mlops.retrainer` and `genai.triage` are spawned detached — so a child died on
    `database is locked` while its row sat at `queued` forever. `mlops.retrainer` has the
    identical pattern and the same latent flakiness; retraining is just rare enough that
    nobody hit it. Every standalone `python -m …` run had passed, because a standalone
    process contends with nothing.
57. Two SQLite connections **inside one process** deadlocked each other: a duplicate
    INSERT raised, and since `db.execute` commits AFTER the statement, the failed write
    left a transaction open that blocked the same process's other handle.
58. **`stream_index.byte_offset` is wrong on every Windows-built artifact.**
    `build_artifact` counted a 1-byte `\n` while text mode wrote `\r\n` — one byte of
    drift per line, compounding. Fixed at source with `newline=""`; `serve/main.py` probes
    around it for the already-committed artifact.
59. **`serve/main.py` seeked a TEXT-mode file to a byte offset.** Text handles only accept
    values from their own `tell()`, so the first `json.loads` got a fragment. Combined
    with #58, **`?from_ts=` had never worked for the life of the repo** — nothing called
    it until the replay pause/resume control did.

**Reported, deliberately NOT fixed:**

60. **`/api/stations/stats` ships `provenance: "inferred"` rows with no `ci_low`/`ci_high`**,
    violating contract #1 (CLAUDE.md §3). Found by the triage verifier rejecting its own
    deterministic path. `genai/offline_triage.py` now declines to make point claims about
    those rows rather than papering over it; the endpoint is unchanged.
61. **Every alert renders an identical risk (41% on plant A).** All 648 alerting units
    share `risk_raw` = 0.880117 to 16 significant figures. All 32 trees split on
    `paint_buffer_dwell_s` at the root; past the dwell threshold and under the checklist
    threshold no later split discriminates, so every such unit lands in the same leaf in
    every tree. Reproduced independently with the numpy scorer — not a rendering fault.
    The model identifies the at-risk set but **cannot rank within it**. Not fixed because
    retraining to spread the scores is tuning toward a nicer number, which CLAUDE.md §1
    forbids. The fix, if wanted, is presentational (band + CI) plus a MODEL_CARD entry.

### Also added

- **Replay transport on `/supervisor`**: Start / Stop / Restart. Pause closes the SSE
  connection and keeps the last line state; resume seeks with `?from_ts=` — which is what
  surfaced #58 and #59. A deliberate pause is not reported as an outage.
- `python -m genai.verify_cli --run-id X` re-runs the gate over a stored transcript and
  must reach a byte-identical verdict — the property that makes the audit claim real.

### Auth: real sessions + per-persona route isolation (2026-09-23) — ADR-048

The `/login` shipped in `847b594` checked a credential server-side and then returned a
role *string*, which the client echoed back in `X-PulseTwin-Role` forever. Two holes
followed from that, and both are now closed.

**Hole 1 — signing in was optional.** `lib/api.ts`'s `currentRole` defaulted to
`"leadership"`, so a visitor who never opened `/login` reached `/manager`, `/leadership`
and every leadership panel on `/overview`.

**Hole 2 — entry was a rank comparison.** `usePersonaAccess` used
`roleAtLeast(entryRole, minRole)`, mirroring the API hierarchy, so a leadership login
walked into `/manager` and `system` walked into all four. A hierarchy can say "leadership
may call a manager endpoint"; it cannot say "these are two different jobs."

**Built.**

- `serve/session_store.py` — opaque 256-bit tokens, 8h TTL, process-local, evicts rather
  than refusing when full. `POST /api/auth/login` mints, `GET /api/auth/session`
  validates, `POST /api/auth/logout` revokes (idempotently).
- `serve/middleware/auth.py` — `X-PulseTwin-Token` outranks the declared header: a role at
  or below the session's is honoured (that is `/ops`'s "Viewing as" drop), above it is
  ignored, and a dead token is a `401` rather than a fall-back. `get_user` takes the
  session's user, so a token-holder cannot relabel who acted in the audit trail.
  **Absent header ≠ `supervisor`** once a session exists — conflating those demoted every
  request that did not restate its own role, and cost four test failures to find.
- `frontend/lib/routeAccess.ts` — the ONE exact-match table of who may open what, read by
  both the route guard and the rail, so a lock icon can never sit on a working link.
  `/supervisor` and `/overview` are public; the rest require exactly their own role.
- `frontend/lib/usePersonaAccess.ts` — re-validates the session against the server, then
  redirects (`/login?next=`) or refuses (`AccessRestricted`). `setRole()` is now clamped
  to the session, so a persona pin can no longer be the thing that grants privilege.
- `AppShell` locks unreachable rail entries and shows a real sign-out; `/ops` and
  `/copilot` gained the guard they never had; the login form's inputs gained
  `htmlFor`/`id` (both were announced as unlabelled before).

**The rule bent, on purpose.** The session lives in `sessionStorage`, read/written only in
`lib/api.ts`. CLAUDE.md §7 bans browser storage in components; a sign-in is not derived
state, and with a static export typing `/manager` into the address bar is a reload, so
without persistence every hard navigation signed the user out. Reasoned through in
ADR-048.

**Measured in a browser against a live backend**, not inferred: signed out, `/supervisor`
and `/overview` render and the other four redirect carrying `next`; a manager signed in
from a `/copilot` bounce lands on `/copilot`, is refused on `/leadership` and `/ops`,
opens `/manager`, and survives a reload; sign-out returns to `/login` and `/manager`
bounces again; leadership is refused on `/manager`. The only 4xx on a signed-out page is
`/api/shift/summary` (manager-gated), already caught and degraded by `AppShell`.

**Tests.** `tests/test_auth_session.py`, 17 cases, including "a declared role cannot climb
above the session", "an expired token is 401 not a header fallback", and "no token behaves
exactly as before" — the last one pinning the header seam `docs/SECURITY.md` describes,
which this deliberately does NOT close. `tests/test_auth.py` (80) unchanged and passing;
`test_api`/`test_audit`/`test_triage_api`/`test_intelligence_api`/`test_qa_regressions`
(148) unchanged and passing. `npx tsc --noEmit` clean.

### Non-production code removed; backend always loads the key (2026-09-25) — ADR-054

Deleted `benchmark/` (external AI4I/Bosch harness + 3 tests + its dependency group and
nightly job) and the never-applied `infrastructure/kubernetes/` + `terraform/`; docs that
cited them as enforcement now name the real control or say plainly there is none in this
repo. `offline/benchmarks/` (ADR-050) is kept. The backend now loads `.env.local` itself
(compose `env_file`, `make dev-backend`) and logs its GenAI mode at startup; verified in the
container: `genai: agent mode`, and a live run verified 7/7 claims. Docker: the stale
publisher container/image removed; other projects' images left untouched. Demo parts
(MQTT publisher, demo docs, demo accounts, live simulation) deliberately KEPT for now.

### Production-only: SQLite demo runtime and retraining retired (2026-09-25) — ADR-052, ADR-053

The deployed site reported `database: sqlite` because nothing set `DATABASE_URL`; both
deploy paths were SQLite. Now every deployment sets `PULSETWIN_REQUIRE_POSTGRES=1` and fails
at startup without Postgres; `docker-compose.yml` and `serve/Dockerfile.render` are gone;
feedback lives in Postgres (migration 016); the cloud target is Neon + Render + Vercel
(`docs/PRODUCTION_ARCHITECTURE.md` §DEPLOY). Retraining was removed — static seed-42 data
makes a retrain reproduce the deployed model. **First end-to-end run of
`docker-compose.prod.yml` (2026-09-25): PASSED** — migrations 014-016 applied, 325,793 rows
loaded, `/api/health` = `postgresql`, feedback persisted across `restart backend`,
`/supervisor` and `/ops` loaded in Playwright with no page errors or 5xx, backend ~82 MB
RSS. Not yet done: the Neon/Render/Vercel accounts themselves (owner action).

### Proactive watch + agent-proposed actions (2026-09-24) — ADR-051

A friend's "agentic digital twin" boilerplate was assessed and not adopted as written (it
duplicated the twin and the agents with weaker versions, used mock actuators, and asked for
a stamping-die agent with no data behind it). Its two real ideas were built instead:
`serve/watch.py` detects starvation, paint-environment and tool-degradation risk on every
live-simulation event; a firing starts one of three new agents (`starvation`, `paint_env`,
`tool_wear`), each of which PROPOSES actions from a closed catalogue that the verifier
grades like numbers; a manager approves in the Proposals inbox (`/manager`, `/ops`); the
twin applies it; the watch confirms recovery (EFFECTIVE). Verified end to end on all three
scenarios in deterministic mode, and against OpenRouter (see ADR-051 for what the live model
got wrong and what the gate caught). Thresholds were profiled on the replay streams before
being trusted; the only normal-run firings are the designed Mode C torque deficits.

Also fixed this session: a triage worker that raised left its row at `running` forever —
it now records `worker_crashed: ...`, and `GET /api/triage/runs/{id}` reports a run with no
heartbeat for 15 minutes as `worker_stopped`. `openai` was missing from `.venv` entirely,
so the agent path had never run from this checkout's backend.

**Not done:** `artifacts/watch_rules_*.json` must be force-added when artifacts are next
committed (`artifacts/` is gitignored). The test suite still writes to the dev ops DB
(372 never-started `queued` rows found) — see the weakness below.

### Known test-isolation weakness

`tests/test_qa_regressions.py`'s model-registry tests read the **default temp ops DB** and
fail if an earlier manual run left rows in it. They pass against a clean
`PULSETWIN_OPS_DB`. Pre-existing; set that env var when running them after any hand-run of
`genai.triage` or `mlops.retrainer`.

---

## Status

**Phase 0 — Bootstrap: COMPLETE** (2026-08-31)

- [x] Spec audited — 31 findings, 6 blockers (`docs/AUDIT.md`)
- [x] 28 ADRs resolving every finding (`docs/DECISIONS.md`)
- [x] `CLAUDE.md` — session rules, import wall, three contracts
- [x] Git guardrails — push denied in `.claude/settings.json`; `includeCoAuthoredBy: false`
- [x] Plugins installed: frontend-design, feature-dev, pyright-lsp, typescript-lsp,
      playwright, claude-md-management, commit-commands
- [x] `docs/SPEC.md` — corrected canonical spec
- [x] `git init` + first commit

**Phase 1 — Configs: COMPLETE** (2026-08-31)

- [x] `configs/plant_a.yaml` — 30 stations, 4 manual, cycles 45–61s
- [x] `configs/plant_b.yaml` — 42 stations, 8 manual, TRUCK variant, cycles 45–70s
- [x] `configs/economics.yaml` — every figure sourced; discount rate + annualisation basis
- [x] `offline/config_schema.py` — Pydantic schemas with ADR-enforcing validators
- [x] `tests/test_configs.py` — **43 passing**

**Verified from config alone:**
- plant_a: S12 (62.1s) → **S19 (64.6s)** on EV-heavy days ✓
- plant_b: S16 (72.1s) → **S31 (74.9s)** on truck-heavy days ✓
- The bottleneck shift mechanism **generalises across both plants** — it is not tuned per plant.
- Cost-optimal threshold p\* = **0.0052** from config alone; the trained model measures
  **0.0065** (`metrics.cost_optimal_threshold`, and what the API serves). Alarm budget =
  6/shift either way, i.e. p ≥ 0.406. Irreconcilable, as ADR-004 predicts — two orders of
  magnitude apart whichever of the two p\* figures you use.

**Correction made this phase:** ADR-002 originally specified S19 base = 58s. That value makes
S19 the constraint on *normal* days too (62.5s vs S12's 62.1s), silently deleting the entire
Tuesday scenario. Corrected to **55s**. `check_bottleneck_shift_configured` now fails any
config whose constraint does not move with variant mix — the validator caught this on its
first run. `docs/SPEC.md` and ADR-002 both amended.

**Phases 2-8 — COMPLETE** (2026-08-31)

| Phase | What shipped | Evidence |
|---|---|---|
| 2 Simulator | Event-driven DES, correlated physics, competing-risks defects | 12/12 acceptance checks, both plants, 98s |
| 3 Featurise | 253 features, timestamp-based leakage guard | perturbation test passes |
| 4 Soft sensors | Quantile regressors + masked-station validation | `offline/ml/soft_sensor.py` |
| 5 Model | HistGB + Platt + dual thresholds + oracle ceiling | PR-AUC 0.256 [0.207-0.308] |
| 6 Artifacts | SQLite 37.5 MB + numpy models + stream JSONL | parity < 1e-6 |
| 7 Serve | FastAPI, import wall enforced, async SSE | 330ms under load |
| 8 Frontend | Supervisor view + landing, static export | control-room UI |
| 10 Docs | AUDIT, DECISIONS, SPEC, ARCHITECTURE, MODEL_CARD, ASSUMPTIONS, DEMO_SCRIPT, README | — |

**100 tests passing.**

### Headline measured results

Read straight out of the committed artifact (`artifacts/pulsetwin.db`, `metrics` and
`plant_meta`, built 2026-09-02) rather than transcribed from an older run — the two had
drifted apart, see the correction note below the table.

| | plant_a | plant_b |
|---|---|---|
| Target throughput | 55 UPH | 48 UPH |
| Achieved throughput | 53.7 UPH (97.7% of target) | 45.3 UPH (94.4%) |
| Defect rate (full run) | 2.99% | 2.82% |
| Defect rate (test split) | 2.52% | 2.35% |
| PR-AUC | 0.256 [0.207-0.308] | 0.052 [0.039-0.076] |
| Oracle ceiling | 0.481 (53% captured) | 0.075 (70% captured) |
| Recall @ operating threshold | 27.6% | 5.6% |
| Precision @ operating threshold | 35.4% | 7.9% |
| Alerts per shift (budget 6) | 8.5 | 6.0 |
| Bottleneck accuracy | 85.9% | 90.2% |
| Bottleneck shift | S12 -> S19 | S16 -> S31 |
| Fallback shadow precision / recall | 3.0% / 100% | 2.9% / 100% |

**Correction (2026-09-16):** this table previously read 87.9% / 79.1% bottleneck accuracy
and 53.8 / 45.4 UPH. Those were the figures from an earlier build; the artifact was
rebuilt on 2026-09-02 and `README.md` / `docs/MODEL_CARD.md` were updated while this file
and `docs/ASSUMPTIONS.md` were not. The values above now match the database. Plant B's
accuracy going *up* (79.1 -> 90.2) while plant A's went slightly down is not a fix
applied to the detector — neither was touched; it is the spread you get from re-running
a stochastic simulation, and it is why the number is quoted per-build rather than as a
fixed property of the method.

### Bugs found and fixed during the build

1. **Line deadlock** — a station breaking down while blocked never recovered; throughput
   collapsed 55 -> 6 UPH. `_on_repair_done` now retries the release.
2. **Manual stations were accidental bottlenecks** — cycle formula ignored their
   configured nominal.
3. **Mode C sign error** — poor lot quality raised torque while raising vibration,
   anti-correlating the interaction. Mode C fired 5 times in 27k units.
4. **Additive logistic could not work** — no single mode could clear the intercept alone.
   Replaced with competing risks; mode shares now track configured weights.
5. **Test split had no peak days** — 21-day horizon put test on Thu-Sun. Moved to 28 days
   of whole weeks.
6. **Circular bottleneck accuracy** — detector and ground truth used the same definition
   and reported 100%. Ground truth now uses a 2-hour window vs the detector's 15 minutes.
7. **Explanation additivity** — contributions telescope to `leaf - root`, so they sum to
   `raw_score - expected_value`, not `- baseline`. Also switched to sample-weighted node
   values (proper Saabas).
8. **PR-AUC band unreachable** — see AUDIT B34. Now measured against an oracle ceiling.

**Phase 9 + remaining Tier 2 — COMPLETE** (2026-09-01)

| Item | Evidence |
|---|---|
| Manager view | Constraint migration, league table, root-cause Pareto, what-if console |
| Leadership view | Value bridge, threshold trade-off, instrumentation planner, rollout, metrics panel |
| What-if grid (ADR-007) | 90 scenarios/plant, one 8h shift each, peak variant mix |
| Instrumentation planner | Ranked by measured value per dollar, with method stated |
| Bottleneck forecasting | Skill vs persistence +0.136 (A) / +0.205 (B) |
| Soft sensors + masked validation | The ambient vs tool-wear contrast, published both ways |
| Docker Compose + frontend Dockerfile | `make demo` |
| `BUSINESS_PROPOSAL.md` | 3-year case, risks, roadmap |
| `tests/test_api.py` | Every endpoint against real artifacts |

**Nothing from the original brief remains unbuilt.**

### Additional bugs found in this phase

9.  **What-if grid omitted the EV bottleneck.** Seeded from the single most frequent
    constraint (S12), so the grid covered S9-S14 and excluded S19 entirely -- the one
    station a manager would most want to tune. Now seeded from BOTH constraints.
10. **What-if window never reached steady state.** A 4-hour run reported 57.5 UPH (above
    the 55 target, impossible in steady state) and named S3 as the constraint, because
    buffers had not filled. Extended to one full 8-hour shift.
11. **What-if ran on the wrong variant mix.** The simulator starts on a Monday, so short
    runs used the NORMAL mix and S19 never bound -- making every change at S19 look
    worthless. The grid now forces the peak mix, which is the condition being tuned for.
12. **What-if buffer slider did nothing.** Every delta at S19 returned an identical
    52.707 UPH. The mutation was applied correctly -- it changed the station's OUTPUT
    buffer, which is irrelevant because a bottleneck is never blocked by what follows it.
    Now applied to the FEED buffer upstream, which shows a real optimum at +4 slots
    (52.7 -> 54.1 UPH) and a constraint migration to S12.
13. **Constraint detection was fill-biased.** Cumulative active time over a whole short
    window favours upstream stations, which have seen more units while the line fills --
    plant B reported S2 on a 42-station line. Now scored on the second half only.
14. **`achieved_uph` was wrong by a factor of 25, silently.** plant_meta reported
    **2.1 UPH** for a line running at 53.8. The computation divided the unit count -- by
    then trimmed to the replay window (ADR-032) -- by the FULL 28-day horizon. Nothing
    failed: the number flowed straight into `/api/economics/roi`, where
    `units_per_year = achieved_uph x 8 x 3 x 250`, and understated the entire business
    case. Found by eyeballing plant_meta after a rebuild, not by any test -- so a
    regression test now asserts achieved throughput lands within 80-105% of target.
15. **Soft sensor reported R2 = -0.30.** Not a bug in the end, but the diagnosis mattered:
    tool-wear signals are genuinely unrecoverable (neighbour correlation 0.06), while
    ambient signals reach R2 0.99. Added a paint station to the masked set so the contrast
    is measured, and reduced regressor capacity so an absent signal reports ~0 rather than
    a misleading negative.

---

## → NEXT ACTION

Everything in the Round 2 brief and everything in the finals brief (F1-F8) is built and
committed. What is left is **verification, not construction** — in priority order:

1. **Decide what to do about the registry not knowing the production model** (the quoted
   finding at the end of the QA section above). This is the only open item that is a
   *design* question rather than a verification one, and it is the one most likely to come
   back as a judge's follow-up.
2. **Run the production stack for real.** `docker compose -f docker-compose.prod.yml up
   --build` on a machine with a working Docker daemon. Still never executed. The QA pass
   found bug #31 — a container in which no persona view was reachable — only by
   reproducing the frontend image's serve command by hand, which is fair warning that an
   actual `up` will find more.
3. **Walk `docs/FINALS_DEMO_SCRIPT.md` end to end**, including the live sensor-dropout
   injection over MQTT, which needs that same stack running. Act 2's *rendering* is now
   verified (bug #33 fixed, banner confirmed in-browser), but the MQTT injection path that
   feeds it still has not run against a real broker. Note the pre-flight also needs
   `pip install -r ingestion/requirements.txt` on the host, which the script does not say.
4. ~~**Drive the four views in a browser.**~~ **Done 2026-09-16**, and again after the
   2026-09-19 redesign — six routes, 0 console errors, 0 failed requests.

6. **Run one live `genai.triage` against a real provider.** Set `GROQ_API_KEY` and run
   `python -m genai.triage --plant plant_a --question "..."`. Everything except one API
   call's wire format is already proven keyless — loop, tools, verifier, persistence,
   RBAC, degradation — but the agent path itself has never talked to a provider. Given
   this repo's history with untested paths, treat it as mandatory before demoing it.

7. **Decide what to do about every alert showing the same risk** (finding #61 above).
   It is a measured property of a saturated model, not a bug, but it reads as broken.
   The non-tuning fix is presentational.

8. **Rebuild the artifact when convenient** — `stream_index.byte_offset` is drifted in
   the committed one (#58). `serve/main.py` probes around it, so nothing is broken, but
   a rebuild makes the index correct rather than tolerated.
5. **Re-run `make generate && make train && make build-artifact`** if any headline number
   needs to change, then update `README.md`, `docs/MODEL_CARD.md`, `docs/STATE.md` and
   `docs/ASSUMPTIONS.md` **together**. Those four drifting apart is exactly what produced
   the correction note above. A rebuild also writes `trained_at` in UTC for the first time
   (bug #40) — not urgent, the existing value is stale demo state, not wrong code.

**Do not** retune the model to raise PR-AUC. The oracle framing is stronger than a
higher number, and ADR-020 makes the band advisory.

---

## Original next action (Phase 1, done)

**Tier 0, item 2: the simulator.** `offline/simulator/`.

Build in this order, because each stage is testable against the one before:

1. `line_model.py` — **event-driven** scheduler (ADR-005), never a 1-second tick loop.
   States `idle | working | blocked | starved | down`; finite output buffers; exponential
   breakdowns from MTBF with gamma repair; tool-wear drift 0.1–0.5%/hr resetting after
   repair. Persist state as **intervals** `(station_id, state, start_ts, end_ts)`.
2. `physics.py` — temporally autocorrelated signals. Mean-reverting random walks for tool
   wear, thermal inertia `T(t) = α·T(t-1) + (1-α)·(T_ambient + heat)`, EWMA vibration that
   spikes on blocking→release bursts. Manual stations emit **NaN + a checklist record**.
3. `operators.py` — skill pools; the S15 rush fires on the **unblock transition** (ADR-026).
4. `causal_engine.py` — the ADR-025 propensity formula. `paint_batch_quality` and
   `incoming_lot_quality` are latent and must never reach the feature matrix.
5. `inspection.py` — S30 only (ADR-014). 92% detection, 0.2% false reject. `true_defect`
   stored separately and never shown to the model.
6. `generate.py` — CLI, writes telemetry / genealogy / checklists / inspections.

Read `docs/SPEC.md` §Phase 2 for the acceptance checks. The one to watch: **achieved UPH
within 10% of 55** — that is the check that proves ADR-002 actually worked.

Config access is already done — use `offline.config_schema.load_plant_config`. Do not
re-parse YAML anywhere else, and do not hardcode a station ID outside the config.

---

## The ladder

### TIER 0 — no demo exists without these (~10h)

| # | Item | Est | Done | Notes |
|---|---|---|---|---|
| 1 | `configs/` — plant_a, plant_b, economics + Pydantic validation | 1.5h | ✅ | 43 tests. Both plants' bottleneck shift verified |
| 2 | Simulator | 3.5h | ✅ | 12/12 acceptance checks both plants |
| 3 | Featurise + leakage test | 1.5h | ✅ | perturbation test, not a name scan |
| 4 | Defect model + thresholds | 1.5h | ✅ | dual thresholds + oracle ceiling |
| 5 | Artifacts + numpy export | 1.5h | ✅ | 37.5 MB, parity < 1e-6 |
| 6 | FastAPI core | 1.5h | ✅ | import wall enforced, async SSE verified |
| 7 | Supervisor view | 3h | ✅ | line map, alert feed, containment |
| 8 | README + compliance table | 0.5h | ✅ | all 7 rubric bullets mapped |

*(This table previously carried a second, unticked copy of rows 2-8 — a stale duplicate
from before Tier 0 closed. Removed 2026-09-16; the ✅ rows above are authoritative.)*

**If Tier 0 is not closed at T-14h, stop and cut Tier 1 down to containment only.**
— never triggered; Tier 0 closed with time left.

### TIER 1 — best rubric value per hour (~6h)

| # | Item | Est | Done | Rubric |
|---|---|---|---|---|
| 9 | Soft sensors + **masked-station validation** | 1.5h | ✅ | Bullet 1. Ambient R² 0.96-1.00, tool-wear R² ≈ 0 — both published (ADR-030) |
| 10 | Containment endpoint + `ContainmentPanel` | 1.5h | ✅ | Bullet 4 (delayed defect surfacing) |
| 11 | Bottleneck detection + `BottleneckTimeline` + Manager view | 2h | ✅ | Bullets 2, 5 |
| 12 | ROI endpoint + Leadership KPI cards + value bridge | 1h | ✅ | Bullet 5 |

### TIER 2 — cut in this order, first to last (~6h)

Nothing on this tier was actually cut.

| # | Item | Cut order | Done | Rubric cost if cut |
|---|---|---|---|---|
| 13 | Instrumentation planner | 6th (keep if possible) | ✅ | Bullet 3 weakens — it is the differentiator |
| 14 | Reduced what-if grid (ADR-007) | 5th | ✅ | The "twin" moment weakens |
| 15 | Plant B full artifacts | 4th | ✅ | Bullet 6. Full pipeline runs for plant B, not just config loading |
| 16 | Sensitivity toggle | 3rd | ✅ | Minor |
| 17 | Rollout timeline | 2nd | ✅ | Minor — it is in the business proposal anyway |
| 18 | Docker Compose | **1st to cut** | ✅ | None at the time. The finals upgrade later made it load-bearing (`docker-compose.prod.yml` is the F1/F2 runtime), so the thing that was first to cut became the thing the production story runs on |

---

## Rubric coverage tracker

The seven "Real-World Complexities" are the grading surface. Keep every row non-empty.

| # | Complexity | Answered by | Tier | Status |
|---|---|---|---|---|
| 1 | Mixed legacy / modern instrumentation | Soft sensors + INFERRED provenance badge; masked-station validation publishes where it works and where it does not | 0+1 | ✅ |
| 2 | Multi-causal, intermittent defects | 3 competing-risk defect modes + exact decision-path explanations | 0+1 | ✅ |
| 3 | Cannot modify live systems | Read-only architecture + instrumentation planner; finals adds the one-way OT/IT bridge (`network-policy.yaml`) | 1+2 | ✅ |
| 4 | Delayed defect surfacing | Containment tracing + measured lead time | 1 | ✅ |
| 5 | Different stakeholders | **4**-persona dashboard — supervisor / manager / leadership, plus the finals Ops view for the team that runs the twin itself | 0+1+F6 | ✅ |
| 6 | Multi-site scaling | Config-driven + Plant B swap; finals adds per-plant Postgres and the 1/5/20-plant cost model | 0+2+F7 | ✅ |
| 7 | False alarms erode trust | Dual thresholds + alarm budget + feedback; finals adds drift detection so a degrading model is visible before the floor stops trusting it | 0+F3 | ✅ |

**Bullet 7 is fully inside Tier 0** and is the one the ADR-004 dual-threshold story makes
genuinely distinctive. Lead with it.

The finals upgrade answers a different question set (§1 of `pulsetwin_final_prompt.md`'s
finals section — "what happens when a sensor dies," "how do you know when the model is
wrong," "who can see what"). That mapping lives in `docs/FINALS_DEMO_SCRIPT.md` and
`docs/PRODUCTION_ARCHITECTURE.md`, not in this seven-row table, which grades Round 2.

---

## Risk register

| Risk | Trigger | Mitigation |
|---|---|---|
| Simulator overruns 3.5h | Event scheduler debugging | Hard cap at 5h. Fall back to a coarser 5-second tick — worse fidelity, still ships |
| PR-AUC lands outside 0.75–0.88 | Reported at Tier 0 item 4 | **Do not tune toward the band.** Report the measured value with its CI (ADR-020). An honest 0.71 beats a tuned 0.85 |
| SSE dead in deployment | Only visible after deploy | ADR-009 headers from the first commit. Test through a proxy, not just localhost |
| Frontend eats the remaining clock | Tier 0 item 7 exceeds 3h | Mock with the `design` skill first. Ship Supervisor only; Manager and Leadership become static-data views |
| Both plants' pipelines overrun | Tier 2 item 15 | `plant_b.yaml` validating + loading proves config-driven. Full artifacts optional |

---

## Decision log — this session

- Original prompt kept unmodified as the historical brief; `docs/SPEC.md` is canonical.
- No GitHub push, ever. No commit trailers. Enforced, not merely documented.
- Artifacts committed once at the end, not per phase (ADR-027).
- 21 simulated days rather than 14 (ADR-020) — cheap under event-driven simulation, and it
  meaningfully tightens the PR-AUC confidence interval.

---

## Ledger

Append one line per phase as it closes. Include the wall-clock time — the ladder depends on
knowing whether we are ahead or behind.

```
2026-08-31  Phase 0  Bootstrap complete. Audit + ADRs + CLAUDE.md + guardrails + plugins.
2026-08-31  Ph2-8   Simulator -> API -> Supervisor UI. 100 tests pass.
                     PR-AUC 0.256 (53% of a measured 0.481 oracle ceiling).
                     8 real bugs found and fixed; all logged above.
2026-08-31  Phase 1  Configs complete. plant_a (30 stn) + plant_b (42 stn) + economics.
                     43 tests pass. Bottleneck shift verified on BOTH plants.
                     Caught + fixed: S19 58s->55s (58s deleted the Tuesday scenario).
2026-09-16  Finals   F1-F8 closed out. Audited a stalled prior session (mostly-built
                     backend/infra, but 4 red tests, dead Prometheus deps, and
                     frontend/app/ops/page.tsx missing entirely). Fixed 4 pre-existing
                     test failures + 3 introduced while wiring Prometheus metrics; built
                     the Ops view (3 missing components + the page); wired
                     OfflineBanner/SensorDropoutAlert/RoleGate/AuditToast/EscalationChain,
                     which existed but were never rendered on /supervisor and were absent
                     from /manager, /leadership; wrote terraform/main.tf +
                     monitoring configs + all 8 finals docs (none existed). 226/226 tests
                     pass. Docker daemon unavailable in this sandbox -- compose stack
                     reviewed by hand, not run end to end (see bugs 16-24 above).
2026-09-16  Docs     Audited every .md against the code and the committed artifact rather
                     than against each other. 6 findings (25-30 above): four docs
                     disagreeing on headline metrics, a SECURITY.md RBAC table claiming
                     gates that do not exist, an operator-privacy primitive described as
                     sha256/salted when it is neither, retrofit costs estimated when the
                     artifact had measured ones, a soft-sensor caveat the artifact itself
                     records but the docs omitted, and an alarm-budget overshoot nothing
                     explained. No code changed; every headline number now names the
                     table it came from.
2026-09-16  QA       End-to-end validation pass: ran the system instead of reading it.
                     Live backend probed across every endpoint x every role; all six
                     routes driven in real Chromium with network+console captured; MLOps
                     lifecycle exercised through the API; ingestion modules executed
                     directly. 15 defects found (bugs 31-45), all fixed and verified;
                     12 more reported and deliberately not fixed (reports/
                     qa-audit-report.md SS6). The 226-test suite was green throughout and
                     caught none of them -- every defect sits in a SEAM between two
                     components, and the suite tests components. 4 of the 14 silently
                     disabled a feature the finals demo depends on. Added
                     tests/test_qa_regressions.py (24 tests, one per defect, each
                     reproduced before it was fixed) -> 250 passed. First confirmed
                     browser rendering of all four persona views, and first end-to-end
                     run of the F5 offline/reconnect cycle.
2026-09-19  UI       Frontend redesign: Tailwind v3->v4 (CSS-first @theme, config
                     deleted), Radix, Motion, next/font, and a components/ui/ layer of
                     14 primitives. 10 defects fixed (46-55) -- incl. a font that was
                     named but never loaded, and a cn() bug that stripped monospace from
                     71 call sites. Verified in Chromium across six routes: 0 console
                     errors, 0 contrast failures, 232 keyboard stops all with a focus
                     ring (was zero focus styles), stream CLS 0 at 50x.
2026-09-20  GenAI    genai/ agentic triage tier (ADR-037): tool-use loop over the
                     read-only API, with a deterministic verifier that redacts or
                     rejects rather than flags. Import wall NOT widened -- genai was
                     already outside ALLOWED, so test_serve_imports.py needed no edit.
                     Provider xAI Grok, isolated to genai/client.py; the no-LLM
                     offline_triage path passes the same gate, so the feature tests
                     keyless. Added Start/Stop/Restart replay transport, which surfaced
                     that ?from_ts= had never worked (bugs 58-59). Found 4 real bugs
                     (56-59), reported 2 more (60-61) without fixing. 339 tests pass.
2026-09-21  GenAI    Provider moved to OpenRouter nvidia/nemotron-3-ultra-550b-a55b:free
                     (ADR-040) and the agent path ran against a LIVE provider for the
                     first time. Every defect below was invisible to the suite because
                     each lives in the seam between this repo and a real endpoint.
                     Bug 73: credential precedence -- KEY_ENVS takes the first name SET
                     and GROQ_API_KEY precedes OPENROUTER_API_KEY, so an env holding
                     both sent the Groq key to OpenRouter -> bare provider_status_401.
                     Bug 74: the capability ladder degrades on 400/422 and OpenRouter
                     rejects response_format+tools with HTTP 200 instead -- zero tool
                     calls, unparseable finding, on L0, which is where every new model
                     starts. Added capabilities.suppresses_tool_calls (soft probe, first
                     round trip only, needs BOTH no-tool-call AND non-JSON output; the
                     broader rule was wrong and two existing tests caught it).
                     Bug 75: the output schema only ever reached the model via
                     response_format, so at L4 -- the rung the ladder lands on for BOTH
                     providers -- the model invented a `citations` key and a correct
                     6-tool investigation was discarded by the parser. Schema is now
                     written into prompts.SYSTEM as well.
                     Bug 76: store.py billed every run at Groq's gpt-oss-120b rate card,
                     so a FREE model cost $0.015/run with a cost_source naming a provider
                     never called -- contract #2 broken by the tier that enforces it.
                     _cost() now prefers the provider's own usage.cost.
                     Bug 77: finding_raw was NULLed on a parse failure, so "invented a
                     schema" and "cut off mid-sentence" were indistinguishable from the
                     database. Raw text now kept (capped, never served).
                     After the fixes, cold-cache live runs: ask 4/5 claims verified (the
                     5th correctly redacted, claim 85.9 vs result 0.8586 -- a
                     ratio-vs-percent slip the verifier caught), incident 10/10, 6 and 11
                     tool calls, L4, $0.00, ~70s. Deterministic path 26/26 across all
                     five intents. All four decision-intelligence endpoints 200. Five
                     routes rendered in Chromium with 0 console errors (one expected
                     403 on the leadership-gated model health). 518 tests pass.
                     NOT a defect: `:free` caps the account at 50 requests/day and one
                     run spends 7-12, so the last three intents could only be exercised
                     via the deterministic path once the quota went.
2026-09-22  Live     Added an explicitly labelled Live Synthetic Simulation alongside
                     recorded replay (ADR-042): server-owned run/clock, deterministic
                     seed and eight scenarios, shared event/current-state/alert tables,
                     start-pause-resume-stop APIs, SSE join by run ID, time-scoped unit
                     and containment reads, durable acknowledgement/actions, synthetic
                     inspection outcomes and validation metrics. Frontend API port is
                     consistently 7860; Compose now persists local ops state and the
                     Postgres stack loads artifacts before backend/ingestion and includes
                     the frontend. TypeScript and Python compilation pass; direct SQLite
                     runs produced 20/20 events with an injected alert and 200/200 events
                     with six inspection outcomes. Docker verification was completed once
                     the daemon became available; MQTT now writes the shared run store.
2026-09-22  Finals   Added offline/live feature parity and prediction metadata;
                     production-context containment explanations; the complete alert
                     lifecycle; response, containment, validation, and sourced economics
                     metrics; decision history; and Scenario Lab controls. The full
                     Postgres/Redis/MQTT/API/frontend path passed its smoke check.
2026-09-22  Closeout Completed the remaining live-demo seams: full-vector parity for both
                     plants, time-scoped operator-group containment, config-loaded action
                     thresholds, registered fallback scorer, user/owner/due assignment,
                     refresh-safe alert status and UI close action, active/correctness
                     metrics, and full-stack readiness. 551 backend tests passed with the
                     two blocking TestClient SSE cases excluded; frontend production build
                     and rebuilt Docker MQTT smoke passed. No in-app browser was connected,
                     so browser interaction could not be re-run in this environment.
```
2026-09-22  Orchestr Multi-agent orchestration for the `incident` intent (ADR-045),
                     prompted by mentor feedback asking for genuine multi-agent
                     orchestration rather than one broadly-scoped agent. `incident` now
                     runs Coordinator (deterministic) -> three bounded specialist loops
                     (reliability, production, risk), each `run_loop` against a disjoint
                     tool subset with its own iteration/tool-call/wallclock cap -> one
                     synthesis call (no tools) that reconciles their evidence into the
                     existing Finding shape. genai/verifier.py needed ZERO changes --
                     proven, not just asserted, by a test that runs the real verifier
                     against a merged three-specialist transcript. The one new mechanism:
                     run_loop takes initial_seq/max_iterations/max_tool_calls/
                     wallclock_cap_s, all defaulting to today's constants, so the
                     single-agent path and its whole test suite are byte-identical with no
                     call-site edits (all 14 pre-existing loop tests pass unmodified).
                     New: genai/orchestrator.py, migrations/014_genai_orchestration.sql +
                     serve/db.py mirror (triage_tool_calls.agent, triage_runs.orchestrated/
                     agents_used), frontend AgentTrail.tsx wired into CopilotThread.tsx.
                     24 new tests across loop, orchestrator, and persistence
                     layers, all passing; tsc clean. Kill switch
                     (PULSETWIN_GENAI_DISABLE_ORCHESTRATION) and a specialist-count lever
                     (PULSETWIN_GENAI_MAX_SPECIALISTS) guard the OpenRouter :free-tier
                     50/day request budget (ADR-040) -- worst case 19 requests per
                     orchestrated run, typical ~9. NOT YET DONE: a live-provider smoke run
                     (budget-aware, manual, deferred to avoid burning the day's free-tier
                     quota) and the docs/FINALS_DEMO_SCRIPT.md Act 2B addition.
2026-09-22  Knowledge Added engineering-knowledge retrieval (ADR-046), the one genuine
                     gap surfaced when comparing this build against a competing
                     Qdrant/sentence-transformers RAG prompt for the same hackathon.
                     Deterministic lexical TF-IDF over a 10-doc/26-chunk synthetic SOP
                     and fault-code corpus, precomputed offline
                     (offline/build_knowledge_index.py) into artifacts/knowledge_index.npz
                     + knowledge_corpus.json, served by pure numpy/stdlib
                     (serve/knowledge_index.py, GET /api/knowledge/search) -- no
                     embedding model, no vector DB service, no new dependency, works with
                     no LLM credential. A real precision bug was caught by hand-checking
                     retrieval before writing tests: "weld current anomaly" first ranked
                     the wrong document because the corpus wrote the signal as the single
                     compound token weld_current_a while the query used separate English
                     words; fixed by indexing both the compound identifier and its
                     underscore-split parts, plus the doc title/section heading alongside
                     each chunk's body. New tool search_engineering_knowledge joins the
                     18-tool registry, available to the single-agent path and all three
                     `incident` specialists; each specialist's max_tool_calls bumped by 1
                     so the optional lookup never displaces domain evidence. New
                     genai/verifier.py check: a KnowledgeCitation's quoted_text must be a
                     real substring of the transcript's recorded chunk text, and every
                     failure code is fatal (rejects the run), same posture as unsourced
                     currency or operator identity -- tracked in a verdict pool kept
                     strictly separate from numeric claims so it can never inflate
                     claims_verified past claims_total. 28 new tests total: 9 in
                     tests/test_genai_verifier.py (40 total in that file), 13 in
                     tests/test_knowledge_index.py, 5 in tests/test_knowledge_api.py, 1 in
                     tests/test_genai_orchestrator.py, all passing; full
                     genai+triage+serve-imports+intelligence sweep (344 tests) green.
                     Deterministic (no-LLM) incident path re-run end to end
                     to confirm the schema addition doesn't affect offline parity.
                     NOT YET DONE: a live-provider run exercising the model actually
                     calling search_engineering_knowledge and citing a result (deferred,
                     same budget-conscious reasoning as the ADR-045 entry above); the
                     ACT 2B demo-script beat does not yet mention it.

2026-09-23  Design   Rebuilt the frontend in BMW's corporate-automotive language, light
                     (ADR-047), from two references that disagreed: dashboard mockups
                     carrying a new information architecture, and a written BMW design
                     system that is light where the mockups were dark. Took structure
                     from one and language from the other; the palette conflict was
                     resolved in BMW's favour on the user's explicit call, which is why
                     CLAUDE.md §7 is rewritten rather than left contradicting the code.
                     White canvas, BMW blue #1c69d4 as the single action colour, 0px
                     corners everywhere, no card shadow, 700/300 weights with 500 absent.
                     The dark surface survives as ONE band (#1a2129: sidebar rail, hero
                     and CTA bands, tooltip, chart tip) with its own -on-dark signal set.
                     The swap was cheap because zero hex literals and zero bg-black/
                     text-white existed outside the token layer: app/globals.css plus
                     lib/theme.ts carried ~9,000 lines of view code with them, and the
                     --radius-* names were kept (all now 0) so no call site needed an
                     edit. Three contracts untouched -- only the signal VALUES moved, and
                     hatch-inferred went 28% -> 42% alpha because an amber hatch tuned for
                     #111419 nearly vanishes on white, which would have been a broken
                     provenance contract rather than a styling nit. AppShell rebuilt as a
                     dark rail + light top bar WITHOUT a prop-contract change, so all four
                     persona pages mount it unedited; its two new affordances are real,
                     not decorative -- a notification tray derived from /api/shift/summary
                     (the badge counts conditions the API actually reported) and a search
                     box with exactly two destinations that exist (^U\d{4,8}$ -> that unit
                     on /supervisor via #unit=, anything else -> a DRAFT on /copilot via
                     #q= that the composer seeds but does not send). New /overview page:
                     not gated by usePersonaAccess, degrades panel by panel naming the
                     role each blocked panel needs, and bands its station heatmap
                     RELATIVELY (top decile / next fifth of the plant's own measured
                     distribution) after an absolute uph threshold painted 24 of 30
                     stations red -- unsensored stations take no band and are excluded
                     from the ranked loss table, since /api/stations/stats reports them
                     with no interval. Verified by driving all 8 routes in Chromium and
                     by reading getComputedStyle on probe elements (260px rail clearance,
                     80px section rhythm, 0px radius, no shadow, weight 700 surviving on
                     text-2xs, on-dark tokens resolving); a deterministic copilot
                     `incident` run rendered 5/5 claims VERIFIED in the new theme.
                     BUG FOUND AND FIXED, worth the §8 entry: the base rules were
                     initially UNLAYERED, and unlayered CSS beats Tailwind v4's
                     @layer utilities at any specificity -- so h2{color:ink} rendered
                     every heading in every dark band invisible and .text-2xs{weight:400}
                     overrode font-bold on every badge, panel title and table header.
                     Also fixed: bg-surface-2/50-style translucent insets (invisible at
                     50% over white) and an audit toast anchored on top of the new rail.
                     `next build` then run for real (dev stopped first per §8, and
                     restarted after): compiles clean, all 9 routes prerender and export,
                     and the exported bundle was re-driven in Chromium at 1500px and at
                     390px. tsc --noEmit is clean; no pytest test references the frontend
                     theme. SECOND BUG, found only by testing the export and worth its own
                     §8 line: serving out/ with `python -m http.server` forces /overview
                     .html into the address bar, so usePathname() returns "/overview.html"
                     while the build prerendered "/overview" -- the active nav item
                     differs and React throws hydration error #418 on every page whose
                     shell prerenders. Production never sees it (frontend/Dockerfile runs
                     `serve out`, which resolves clean URLs), so the defect was ENTIRELY
                     in the test harness; re-tested behind a clean-URL shim, all 8 routes
                     are clean. Worth knowing before someone "fixes" the shell. Also note
                     for anyone bisecting: a stale backend has been listening on :7860
                     since before this session with no intelligence router mounted, and it
                     404s /api/shift/summary. The live backend this frontend targets is
                     :9100, which is what frontend/public/config.json already says.
                     SUPERSEDED 2026-09-23: that :9100 move was a workaround for the stale
                     process, not a decision, and it became its OWN bug -- a second stale
                     backend (pre-session-store, no /api/auth/session) then squatted :9100
                     and every manager/leadership login bounced straight back to /login,
                     because restoreSession() reads a 404 as a dead session. The stale
                     processes are gone and both config.json files are back to :7860,
                     which is what the other ~40 references say (Makefile, both compose
                     files, serve/Dockerfile, k8s, prometheus, ingestion, genai/triage.py,
                     scripts/smoke_demo.py, lib/api.ts's own fallback, and the "start the
                     backend on :7860" string the landing page shows the user). Run the
                     dev backend on 7860 and nothing disagrees.
