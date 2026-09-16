FROM python:3.11-slim
WORKDIR /app

# ingestion/ is a separate deployable from serve/ -- it never imports serve/'s code, only
# writes into the same PostgreSQL tables. See ingestion/mqtt_subscriber.py.
COPY ingestion/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ingestion/ ./ingestion/
COPY configs/ ./configs/
COPY artifacts/ ./artifacts/

CMD ["python", "-m", "ingestion.mqtt_subscriber"]
