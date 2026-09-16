"""Prometheus metrics (finals brief Phase F5). One module so every router updates the
SAME Counter/Gauge/Histogram objects instead of each defining its own -- prometheus_client
raises on a duplicate metric name registered twice, which is exactly what happens if two
modules each do `Counter("pulsetwin_predictions_total", ...)`.

These update calls are bookkeeping (an increment, a gauge set), not computation -- the
same ADR-033 category as the psycopg/redis import-wall exceptions. Nothing here computes
drift, lag, or coverage; it exposes numbers `mlops/` and `ingestion/` already computed and
serve/ already reads for its JSON responses.

`Instrumentator().instrument(app).expose(app)` (wired in serve/main.py) adds the standard
HTTP request-count/latency metrics automatically; the metrics below are the
finals-brief-specific ones the generic instrumentator cannot know about.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

predictions_total = Counter(
    "pulsetwin_predictions_total", "Defect-risk predictions served", ["plant_id"]
)
prediction_latency_seconds = Histogram(
    "pulsetwin_prediction_latency_seconds", "Time to score one unit", ["plant_id"]
)
alerts_fired_total = Counter(
    "pulsetwin_alerts_fired_total", "Alerts fired during stream replay", ["plant_id", "shift"]
)
drift_psi = Gauge(
    "pulsetwin_drift_psi", "Latest measured PSI per feature", ["plant_id", "feature"]
)
sensor_dropouts_active = Gauge(
    "pulsetwin_sensor_dropouts_active", "Sensors currently in dropout", ["plant_id"]
)
ingestion_lag_seconds = Gauge(
    "pulsetwin_ingestion_lag_seconds", "Most recent ingestion lag sample", ["plant_id"]
)
active_connections = Gauge(
    "pulsetwin_active_connections", "Open SSE stream connections"
)
