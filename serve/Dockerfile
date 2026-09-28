FROM python:3.11-slim
WORKDIR /app

# Only the import-wall allowlist (CLAUDE.md SS2 / ADR-033). No pandas, no sklearn, no
# shap -- the ensemble ships as flat numpy arrays and is walked by serve/scorer.py.
COPY serve/requirements.txt .
COPY serve/requirements-jobs.txt .
RUN pip install --no-cache-dir -r requirements.txt -r requirements-jobs.txt

COPY serve/ ./serve/
# What the API reads at runtime: models, fallbacks, replay streams, the knowledge index and
# watch rules. NOT pulsetwin.db (39 MB): it is the SQLite build artifact the loader puts
# into Postgres, and a deployment must never open it (ADR-052, PULSETWIN_REQUIRE_POSTGRES).
COPY artifacts/*.npz artifacts/*.jsonl artifacts/*.json ./artifacts/
# The `migrate` service (docker-compose.prod.yml) reuses this image with its CMD
# overridden to `python -m migrations.migrate` -- migrations/migrate.py's own docstring
# says so. Without this, that service would build fine and crash at runtime with
# ModuleNotFoundError, since nothing else in this Dockerfile puts migrations/ on the image.
COPY migrations/ ./migrations/
# The triage route launches genai/ as a detached module; the request-serving process never
# imports it, so the API import wall holds. configs/ is read as text by serve/ (plant
# YAMLs exist-checked by triage.py, alert_policies.yaml by serve/policy.py). No mlops/:
# retraining is gone (ADR-053).
COPY genai/ ./genai/
COPY configs/ ./configs/
# The experiments route launches `python -m offline.sim_experiment` the same way (ADR-064):
# a detached job forking the physical twin. The API process still never imports offline/.
COPY offline/ ./offline/
COPY feature_contract.py ./

EXPOSE 7860
# Shell form so the platform's $PORT is honoured (Render injects it); 7860 locally.
CMD uvicorn serve.main:app --host 0.0.0.0 --port ${PORT:-7860} --workers 1
