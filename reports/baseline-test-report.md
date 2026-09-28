# Baseline Test Report — before any QA fixes

Captured 2026-09-16, on the repository as committed at `c218bae`, before a single line was
changed. This is the "what did the project claim about itself" record that the end-to-end
QA pass was measured against.

## Environment

| | |
|---|---|
| OS | Windows 11 (win32) |
| Python | 3.13.2 |
| Node | v24.19.0 / npm 11.17.0 |
| Docker CLI | 29.6.2 — **no daemon reachable** (`npipe:////./pipe/dockerDesktopLinuxEngine` not present) |
| PostgreSQL | a server listens on `127.0.0.1:5432`, credentials unknown — not the project's |
| Backend runtime | SQLite fallback (`DATABASE_URL` unset) |

**Installed vs pinned.** `serve/requirements.txt` pins `fastapi==0.115.0`,
`pydantic==2.9.0`; the environment actually runs fastapi 0.139.0, pydantic 2.13.4,
starlette 1.6.0. Every result below — baseline and post-fix — is therefore against
*unpinned* versions, not the ones the container would install. See finding L-7.

`paho-mqtt` and `redis` were **not installed** at baseline, so three of the six
`ingestion/` modules could not even be imported (`mqtt_publisher`, `mqtt_subscriber`,
`feature_buffer`). They were installed during the QA pass specifically to exercise them.

## Command

```
OMP_NUM_THREADS=1 python -m pytest tests/ -v --tb=short
```

## Result

```
226 passed, 55 warnings in 1027.65s (0:17:07)
```

| Total | Passed | Failed | Errors | Skipped |
|---|---|---|---|---|
| 226 | 226 | 0 | 0 | 0 |

### By module

| Module | Tests |
|---|---|
| `test_configs.py` | 43 |
| `test_api.py` | 40 |
| `test_serve_imports.py` | 39 |
| `test_simulator.py` | 26 |
| `test_auth.py` | 19 |
| `test_ingestion.py` | 13 |
| `test_scorer_parity.py` | 10 |
| `test_no_leakage.py` | 8 |
| `test_drift_detection.py` | 8 |
| `test_graceful_degradation.py` | 6 |
| `test_audit.py` | 6 |
| `test_sensor_dropout.py` | 4 |
| `test_retraining.py` | 4 |

### Other baseline checks

| Check | Command | Result |
|---|---|---|
| Frontend types | `npx tsc --noEmit -p .` | clean, exit 0 |
| Frontend build | `npm run build` | clean, 7 routes exported statically |
| Backend boot | `uvicorn serve.main:app` | starts, `/api/health` 200, 2 plants, 72 stations |
| Prometheus | `GET /metrics` | 43 `pulsetwin_*` series present |

## Reading of the baseline

The suite is green and it is not a weak suite — it trains real models rather than mocking
them, which is why it takes 17 minutes. It covers each component well.

It nonetheless caught **none** of the 14 defects the QA pass went on to find, and the
reason is structural rather than a matter of thoroughness: every one of those defects
lives in a **seam between two components**, and the suite tests components.

- `mlops/drift_detector.py` is tested (8 tests) and correct. `serve/routers/model_health.py`
  reads what it writes. **Nothing tested the pair**, so the endpoint discarding 19 of 20
  rows was invisible.
- `serve/middleware/auth.py` is tested (19 tests) and correct. `/supervisor` is type-checked
  and builds. **Nothing checked that the page's pinned role can reach the endpoints it
  calls**, so a permanent 403 on every poll was invisible.
- `ingestion/data_quality.py` is tested (13 tests) and correct in isolation.
  **`mqtt_publisher`, `mqtt_subscriber` and `feature_buffer` had no tests at all** — their
  dependencies were not even installed — so the publisher and subscriber disagreeing about
  which clock a timestamp is on was invisible.
- `frontend/Dockerfile` has no test of any kind, and the container had never been run, so
  a `CMD` that serves the landing page for all five persona routes was invisible.

Nothing here is an argument that the existing tests are bad. It is an argument that
"226 passed" and "the system works end to end" were never the same claim, and only the
second one was checked by running the thing.

See `reports/qa-audit-report.md` for what the end-to-end pass found, and
`tests/test_qa_regressions.py` for the 20 tests that now cover these seams.
