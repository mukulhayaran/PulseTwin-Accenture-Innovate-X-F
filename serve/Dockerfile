FROM python:3.11-slim
WORKDIR /app

# Only the five runtime dependencies. No pandas, no sklearn, no shap -- the ensemble
# ships as flat numpy arrays and is walked by serve/scorer.py.
COPY serve/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY serve/ ./serve/
COPY artifacts/ ./artifacts/

EXPOSE 7860
CMD ["uvicorn", "serve.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
