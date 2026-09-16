"""Consumes the Unified Namespace, applies real data-quality handling, and writes
validated readings to PostgreSQL. Runs as its own container (ingestion/Dockerfile) --
everything it shares with serve/ is the wire format of the tables it writes into, never
code.

    python -m ingestion.mqtt_subscriber

Responsibilities (finals brief, Phase F2):
- deduplicate messages (QoS-1 "at least once" legitimately resends)
- reorder late arrivals within a 60s window before writing (mqtt_publisher.py injects
  arrivals delayed 5-30s on purpose)
- detect sensor dropouts within ~10s of the gap forming and log the transition
- validate values against physically plausible ranges; corrupt readings are excluded from
  what gets written, not silently accepted
- feed the real-time feature buffer; once a unit exits the prediction-cutoff station, score
  it and log the prediction

Single-threaded consumer: paho's network loop only ever calls `_on_message`, which does the
minimum work (decode + queue.put) and returns; every stateful operation (dedup, reordering,
DB writes) happens on the main thread pulling off that queue, so there is no locking to get
wrong.
"""

from __future__ import annotations

import hashlib
import heapq
import itertools
import json
import os
import queue
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import psycopg

from ingestion.config_lite import prediction_cutoff_station
from ingestion.data_quality import DataQualityTracker, is_valid_reading
from ingestion.feature_buffer import FeatureBuffer
from ingestion.unified_namespace import parse_topic

REORDER_WINDOW_S = 60.0
DEDUP_TTL_S = 120.0
DROPOUT_CHECK_INTERVAL_S = 10.0
SIG_COLS = ("torque_nm", "vibration_mm_s", "temperature_c", "weld_current_a",
            "humidity_pct", "film_thickness_um")

# Nominal cycle time isn't in the telemetry payload -- keyed by station at startup from
# the same lightweight config read used for the topic zone map.
_STATION_NOMINAL: dict[tuple[str, int], float] = {}


def _connect_pg(database_url: str, retries: int = 15, delay_s: float = 2.0) -> psycopg.Connection:
    last_err = None
    for attempt in range(retries):
        try:
            con = psycopg.connect(database_url, autocommit=True)
            print("[subscriber] connected to postgres")
            return con
        except psycopg.OperationalError as e:
            last_err = e
            print(f"[subscriber] postgres not ready (attempt {attempt + 1}/{retries}): {e}")
            time.sleep(delay_s)
    raise RuntimeError("could not connect to postgres") from last_err


class Deduplicator:
    def __init__(self, ttl_s: float = DEDUP_TTL_S):
        self.ttl_s = ttl_s
        self._seen: dict[str, float] = {}

    def is_duplicate(self, topic: str, payload: bytes, now: float) -> bool:
        if len(self._seen) > 20000:
            self._evict(now)
        key = hashlib.sha256(topic.encode("utf-8") + payload).hexdigest()
        if key in self._seen:
            return True
        self._seen[key] = now
        return False

    def _evict(self, now: float) -> None:
        expired = [k for k, t in self._seen.items() if now - t > self.ttl_s]
        for k in expired:
            del self._seen[k]


class Ingestor:
    def __init__(self, database_url: str, redis_url: str, plant_ids: list[str]):
        self.con = _connect_pg(database_url)
        self.dedup = Deduplicator()
        self.tracker = DataQualityTracker()
        self.sensor_status: dict[tuple[str, int, str], str] = {}
        self.reorder_heap: list[tuple[float, int, dict]] = []
        self._seq = itertools.count()
        self.buffers = {
            pid: FeatureBuffer(redis_url, prediction_cutoff_station(pid)) for pid in plant_ids
        }
        self.n_processed = 0
        self.n_deduped = 0
        self.n_invalid = 0
        self._last_dropout_check = 0.0

    # ------------------------------------------------------------------- ingest path

    def handle_message(self, topic: str, payload: bytes, arrival_ts: float) -> None:
        if self.dedup.is_duplicate(topic, payload, arrival_ts):
            self.n_deduped += 1
            return
        parsed = parse_topic(topic)
        if parsed is None:
            return
        try:
            body = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            return

        if parsed.kind == "telemetry":
            self._queue_telemetry(parsed, body, arrival_ts)
        elif parsed.kind == "state":
            self._on_state(parsed, body, arrival_ts)

    def _queue_telemetry(self, parsed, body: dict, arrival_ts: float) -> None:
        event_ts = datetime.fromisoformat(body["ts"]).timestamp()
        record = {
            "plant_id": parsed.plant_id, "station_id": parsed.station_id,
            "unit_id": body.get("unit_id"), "event_ts_iso": body["ts"],
            "event_ts": event_ts, "arrival_ts": arrival_ts,
            "signals": {k: body[k] for k in SIG_COLS if k in body},
        }
        heapq.heappush(self.reorder_heap, (event_ts, next(self._seq), record))

    def _on_state(self, parsed, body: dict, arrival_ts: float) -> None:
        unit_id = body.get("unit_id")
        if unit_id is None:
            return
        buf = self.buffers.get(parsed.plant_id)
        if buf is None:
            return
        result = buf.maybe_score(parsed.plant_id, str(unit_id), parsed.station_id)
        if result is not None:
            self._log_prediction(parsed.plant_id, result)

    def flush_ready(self, now: float) -> int:
        """Write every buffered reading whose event is older than the reorder window."""
        n = 0
        while self.reorder_heap and self.reorder_heap[0][0] <= now - REORDER_WINDOW_S:
            _, _, record = heapq.heappop(self.reorder_heap)
            self._write_reading(record)
            n += 1
        return n

    def _write_reading(self, record: dict) -> None:
        plant_id, station_id = record["plant_id"], record["station_id"]
        nominal = _STATION_NOMINAL.get((plant_id, station_id), 60.0)
        buf = self.buffers.get(plant_id)

        for signal, value in record["signals"].items():
            self.tracker.record(plant_id, station_id, signal, value,
                                 record["event_ts"], record["arrival_ts"], nominal)
            valid = is_valid_reading(signal, value)
            if not valid:
                self.n_invalid += 1
            self._transition_status(plant_id, station_id, signal,
                                     "healthy" if valid else "degraded", None)
            if not valid:
                continue
            self.con.execute(
                """INSERT INTO station_readings
                       (unit_id, plant_id, station_id, signal, value, provenance)
                   VALUES (%s,%s,%s,%s,%s,'measured')
                   ON CONFLICT (unit_id, plant_id, station_id, signal)
                   DO UPDATE SET value = EXCLUDED.value, provenance = EXCLUDED.provenance""",
                (record["unit_id"], plant_id, station_id, signal, value),
            )
            if buf is not None:
                buf.add_reading(plant_id, str(record["unit_id"]), station_id, signal, value)
            self.n_processed += 1

    def check_dropouts(self, now: float) -> None:
        if now - self._last_dropout_check < DROPOUT_CHECK_INTERVAL_S:
            return
        self._last_dropout_check = now
        for d in self.tracker.check_dropouts(now):
            self._transition_status(d["plant_id"], d["station_id"], d["signal"], "dropout",
                                     d["dropout_duration_s"])

    def _transition_status(self, plant_id: str, station_id: int, signal: str, status: str,
                            dropout_duration_s: float | None) -> None:
        key = (plant_id, station_id, signal)
        if self.sensor_status.get(key) == status:
            return
        self.sensor_status[key] = status
        self.con.execute(
            """INSERT INTO sensor_health
                   (station_id, sensor_type, status, last_reading_at, dropout_duration_s,
                    plant_id)
               VALUES (%s,%s,%s,NOW(),%s,%s)""",
            (station_id, signal, status, dropout_duration_s, plant_id),
        )
        print(f"[subscriber] sensor_health {plant_id}/S{station_id}/{signal} -> {status}")

    def _log_prediction(self, plant_id: str, result: dict) -> None:
        self.con.execute(
            """INSERT INTO model_predictions_log (model_id, unit_id, prediction)
               VALUES (%s,%s,%s)""",
            (f"{plant_id}_live", result.get("unit_id"), result.get("probability")),
        )
        print(f"[subscriber] real-time prediction {plant_id}/{result.get('unit_id')}: "
              f"{result.get('probability'):.3f} "
              f"(from {result.get('n_features_observed')} observed signals)")


def _load_nominal_cycles(plant_ids: list[str]) -> None:
    from ingestion.config_lite import load_stations
    for pid in plant_ids:
        for s in load_stations(pid):
            _STATION_NOMINAL[(pid, int(s["id"]))] = float(s.get("nominal_cycle_s", 60.0))


def run(broker: str, port: int, database_url: str, redis_url: str,
        plant_ids: list[str]) -> None:
    _load_nominal_cycles(plant_ids)
    ing = Ingestor(database_url, redis_url, plant_ids)
    inbox: queue.Queue = queue.Queue()

    def on_connect(client, _userdata, _flags, reason_code, _properties=None):
        print(f"[subscriber] connected to mqtt broker ({reason_code})")
        for pid in plant_ids:
            client.subscribe(f"pulsetwin/{pid}/#", qos=1)

    def on_message(_client, _userdata, msg):
        inbox.put((msg.topic, msg.payload, time.time()))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pulsetwin-subscriber")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(broker, port, keepalive=30)
    client.loop_start()

    print("[subscriber] running")
    last_report = time.time()
    try:
        while True:
            now = time.time()
            drained = 0
            try:
                while drained < 500:
                    topic, payload, arrival_ts = inbox.get(timeout=0.2)
                    ing.handle_message(topic, payload, arrival_ts)
                    drained += 1
            except queue.Empty:
                pass
            ing.flush_ready(time.time())
            ing.check_dropouts(time.time())
            if now - last_report > 30:
                last_report = now
                dq = ing.tracker.overall_score()
                print(f"[subscriber] processed={ing.n_processed} deduped={ing.n_deduped} "
                      f"invalid={ing.n_invalid} data_quality={dq} "
                      f"buffered_for_reorder={len(ing.reorder_heap)}")
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
        ing.con.close()


def main() -> int:
    broker = os.environ.get("MQTT_BROKER", "localhost")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    database_url = os.environ.get("DATABASE_URL")
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    plant_ids = os.environ.get("PULSETWIN_PLANTS", "plant_a,plant_b").split(",")
    if not database_url:
        raise SystemExit("DATABASE_URL is required for ingestion.mqtt_subscriber")
    run(broker, port, database_url, redis_url, [p.strip() for p in plant_ids if p.strip()])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
