FROM python:3.11-slim
WORKDIR /app

# Only the import-wall allowlist (CLAUDE.md SS2 / ADR-033). No pandas, no sklearn, no
# shap -- the ensemble ships as flat numpy arrays and is walked by serve/scorer.py.
COPY serve/requirements.txt .
COPY serve/requirements-jobs.txt .
RUN pip install --no-cache-dir -r requirements.txt -r requirements-jobs.txt

COPY serve/ ./serve/
COPY artifacts/ ./artifacts/
# The `migrate` service (docker-compose.prod.yml) reuses this image with its CMD
# overridden to `python -m migrations.migrate` -- migrations/migrate.py's own docstring
# says so. Without this, that service would build fine and crash at runtime with
# ModuleNotFoundError, since nothing else in this Dockerfile puts migrations/ on the image.
COPY migrations/ ./migrations/
# Admin and triage routes launch these as detached modules. They are never imported by
# the request-serving process, preserving the API import wall while making the routes
# executable in the same production image.
COPY genai/ ./genai/
COPY mlops/ ./mlops/
COPY offline/ ./offline/
COPY configs/ ./configs/
COPY feature_contract.py ./feature_contract.py

EXPOSE 7860
CMD ["uvicorn", "serve.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
