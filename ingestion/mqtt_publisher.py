"""Simulates the OPC-UA -> MQTT edge bridge. Replaces the JSONL file as serve/'s data
source: the same committed `artifacts/stream_{plant_id}.jsonl` (built once, offline, by
offline/build_artifact.py) is replayed here as real MQTT messages onto the Unified
Namespace (ingestion/unified_namespace.py) instead of being read straight off disk.

    python -m ingestion.mqtt_publisher --plant-id plant_a --speed 10

Injects the data-quality problems a real plant floor produces, so ingestion/
mqtt_subscriber.py has something real to catch:

- sensor dropout: a (station, signal) goes dark for 30-120s
- late arrival: 2-5% of messages delayed 5-30s, arriving out of order
- duplicates: ~1% of messages resent (the honest QoS-1-"at least once" behaviour)
- corrupt values: ~0.1% replaced with NaN or an out-of-range sentinel (-999)

Background faults are seeded (42, CLAUDE.md determinism) for a reproducible schedule. The
`control/dropout` topic additionally lets the finals demo force a specific sensor dark on
cue ("It breaks gracefully", docs/FINALS_DEMO_SCRIPT.md) independent of that schedule.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt

from ingestion.config_lite import station_zones
from ingestion.unified_namespace import (
    checklist_topic,
    control_dropout_topic,
    simulation_topic,
    state_topic,
    telemetry_topic,
    unit_entered_topic,
)

ROOT = Path(__file__).resolve().parents[1]
SIG_COLS = ("torque_nm", "vibration_mm_s", "temperature_c", "weld_current_a",
            "humidity_pct", "film_thickness_um")

SEED = 42
HEARTBEAT_S = 10.0  # wall seconds between edge heartbeats
DROPOUT_RATE_PER_EVENT = 0.0004        # background chance any (station, signal) starts a dropout
DROPOUT_DURATION_RANGE = (30.0, 120.0)
LATE_ARRIVAL_PCT = 0.035
LATE_DELAY_RANGE = (5.0, 30.0)
DUPLICATE_PCT = 0.01
CORRUPT_PCT = 0.001


class FaultInjector:
    """Background stochastic faults, plus a live override from the control topic."""

    def __init__(self, seed: int = SEED):
        self.rng = random.Random(seed)
        self._dropouts: dict[tuple[int, str], float] = {}   # (station, signal) -> ends at (sim time)

    def force_dropout(self, station_id: int, signal: str, duration_s: float, now: float) -> None:
        self._dropouts[(station_id, signal)] = now + duration_s

    def maybe_start_dropout(self, station_id: int, signal: str, now: float) -> None:
        if (station_id, signal) in self._dropouts and self._dropouts[(station_id, signal)] > now:
            return
        if self.rng.random() < DROPOUT_RATE_PER_EVENT:
            dur = self.rng.uniform(*DROPOUT_DURATION_RANGE)
            self._dropouts[(station_id, signal)] = now + dur
            print(f"[publisher] FAULT dropout station={station_id} signal={signal} "
                  f"duration={dur:.0f}s")

    def in_dropout(self, station_id: int, signal: str, now: float) -> bool:
        end = self._dropouts.get((station_id, signal))
        return end is not None and now < end

    def roll_late(self) -> float:
        """Returns extra delay in seconds, 0 if this message is on time."""
        if self.rng.random() < LATE_ARRIVAL_PCT:
            return self.rng.uniform(*LATE_DELAY_RANGE)
        return 0.0

    def roll_duplicate(self) -> bool:
        return self.rng.random() < DUPLICATE_PCT

    def roll_corrupt(self, value: float) -> float:
        if self.rng.random() < CORRUPT_PCT:
            return float("nan") if self.rng.random() < 0.5 else -999.0
        return value


def _publish_event(client: mqtt.Client, plant_id: str, run_id: str,
                    zones: dict[int, str], rec: dict,
                    faults: FaultInjector, wall_now: float, inject_faults: bool) -> int:
    sid = int(rec["station_id"])
    zone = zones.get(sid, "unknown")
    n_published = 0

    payload = {}
    for sig in SIG_COLS:
        if sig not in rec:
            continue
        v = rec[sig]
        if inject_faults:
            faults.maybe_start_dropout(sid, sig, wall_now)
            if faults.in_dropout(sid, sig, wall_now):
                continue
            v = faults.roll_corrupt(v)
        payload[sig] = v

    if payload:
        body = json.dumps({
            # Two clocks, deliberately, exactly as OPC-UA carries both a SourceTimestamp
            # and a ServerTimestamp:
            #   ts          -- the event's own (simulated) time, months in the past because
            #                  this replays a committed artifact. Ordering key.
            #   emitted_at  -- wall clock at publish. Freshness key.
            # The subscriber must not measure arrival lag against `ts`: doing so made every
            # reading look ~231 days late, pinning the timeliness component of every
            # station's data-quality score at 0.0 (see ingestion/data_quality.py).
            "ts": rec["ts"], "emitted_at": wall_now, "run_id": run_id,
            "source": "live_synthetic", "transport": "mqtt",
            "unit_id": rec["unit_id"], "station_id": sid,
            "variant": rec.get("variant"), "lot_id": rec.get("lot_id"),
            "shift_id": rec.get("shift_id"), "elapsed_s": rec.get("elapsed_s"),
            "provenance": rec.get("provenance", {}), **payload,
        })
        client.publish(telemetry_topic(plant_id, zone, sid), body, qos=1)
        n_published += 1
        if inject_faults and faults.roll_duplicate():
            client.publish(telemetry_topic(plant_id, zone, sid), body, qos=1)

    state_body = json.dumps({
        "ts": rec["ts"], "unit_id": rec["unit_id"], "station_id": sid,
        "run_id": run_id, "source": "live_synthetic", "transport": "mqtt",
        "variant": rec.get("variant"), "lot_id": rec.get("lot_id"),
        "shift_id": rec.get("shift_id"), "exit_ts": rec.get("exit_ts"),
        "elapsed_s": rec.get("elapsed_s"),
        "exit_elapsed_s": rec.get(
            "exit_elapsed_s",
            (float(rec.get("elapsed_s") or 0) + float(rec.get("cycle_time_s") or 0)),
        ),
        "blocked_before_s": rec.get("blocked_before_s"),
        "buffer_wait_s": rec.get("buffer_wait_s"),
        "ambient_temperature_c": rec.get("ambient_temperature_c"),
        "ambient_humidity_pct": rec.get("ambient_humidity_pct"),
        "station_state": rec.get("station_state"), "cycle_time_s": rec.get("cycle_time_s"),
        "calibrated_risk": rec.get("calibrated_risk"), "alert": rec.get("alert", 0),
    })
    client.publish(state_topic(plant_id, zone, sid), state_body, qos=1)
    n_published += 1

    if "checklist_time_s" in rec:
        ck_body = json.dumps({
            "ts": rec["ts"], "unit_id": rec["unit_id"], "station_id": sid,
            "run_id": run_id, "source": "live_synthetic", "transport": "mqtt",
            "checklist_time_s": rec["checklist_time_s"],
            "checklist_pass": rec["checklist_pass"], "notes_code": rec["notes_code"],
            "operator_bucket": rec.get("operator_bucket"),
        })
        client.publish(checklist_topic(plant_id, zone, sid), ck_body, qos=1)
        n_published += 1

    client.publish(
        unit_entered_topic(plant_id, str(rec["unit_id"])),
        json.dumps({"ts": rec["ts"], "station_id": sid, "run_id": run_id,
                    "source": "live_synthetic", "transport": "mqtt"}), qos=0,
    )
    return n_published


def run(plant_id: str, broker: str, port: int, speed: float, loop: bool,
        inject_faults: bool, run_id: str | None = None) -> None:
    stream_path = ROOT / "artifacts" / f"stream_{plant_id}.jsonl"
    if not stream_path.exists():
        raise SystemExit(f"no stream artifact for {plant_id}: {stream_path}")

    zones = station_zones(plant_id)
    run_id = run_id or f"MQTT-{plant_id}-{uuid.uuid4().hex[:10]}"
    faults = FaultInjector()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"pulsetwin-publisher-{plant_id}")

    def on_control(_client, _userdata, msg):
        try:
            cmd = json.loads(msg.payload.decode("utf-8"))
            faults.force_dropout(int(cmd["station_id"]), str(cmd["signal"]),
                                  float(cmd.get("duration_s", 120.0)), time.time())
            print(f"[publisher] CONTROL dropout forced: {cmd}")
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            print(f"[publisher] bad control message: {e}")

    client.on_message = on_control
    client.connect(broker, port, keepalive=30)
    client.subscribe(control_dropout_topic(plant_id))
    client.loop_start()

    lifecycle_topic = simulation_topic(plant_id)
    lifecycle = {
        "run_id": run_id, "plant_id": plant_id, "status": "running",
        "source": "live_synthetic", "producer": "mqtt", "transport": "mqtt",
        "scenario": "normal", "seed": SEED, "speed": speed,
        "started_at": time.time(),
    }
    client.publish(lifecycle_topic, json.dumps(lifecycle), qos=1, retain=True)
    print(f"[publisher] run {run_id} started")
    # The compose dependency means the subscriber process has started, not necessarily
    # that its MQTT subscription is active. The lifecycle marker is retained, but state
    # messages are not; this short grace period avoids losing the first dashboard frame.
    time.sleep(1.0)

    n_events = 0
    last_heartbeat = time.time()
    while True:
        prev_ts = None
        with stream_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                if prev_ts is not None:
                    from datetime import datetime
                    delta = (datetime.fromisoformat(rec["ts"]) - datetime.fromisoformat(prev_ts)).total_seconds()
                    if delta > 0:
                        time.sleep(min(delta / speed, 1.0))
                prev_ts = rec["ts"]

                extra_delay = faults.roll_late() if inject_faults else 0.0
                if extra_delay:
                    time.sleep(min(extra_delay / speed, 2.0))

                n_events += _publish_event(client, plant_id, run_id, zones, rec, faults, time.time(),
                                            inject_faults)
                if time.time() - last_heartbeat >= HEARTBEAT_S:
                    # The data clock, not wall time: serve ticks the watch's silence
                    # detector on it (ADR-059). A real edge sends its own clock.
                    client.publish(lifecycle_topic, json.dumps(
                        {"run_id": run_id, "plant_id": plant_id, "status": "running",
                         "heartbeat": True, "ts": rec["ts"]}), qos=0)
                    last_heartbeat = time.time()
                if n_events % 500 == 0:
                    print(f"[publisher] {n_events} messages published")
        if not loop:
            break
    lifecycle.update({"status": "completed", "ended_at": time.time()})
    client.publish(lifecycle_topic, json.dumps(lifecycle), qos=1, retain=True)
    time.sleep(0.1)
    client.loop_stop()
    client.disconnect()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plant-id", required=True)
    ap.add_argument("--broker", default=os.environ.get("MQTT_BROKER", "localhost"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    ap.add_argument("--speed", type=float, default=float(os.environ.get("PUBLISH_SPEED", "10")))
    ap.add_argument("--loop", action="store_true", default=True)
    ap.add_argument("--no-loop", dest="loop", action="store_false")
    ap.add_argument("--no-inject-faults", dest="inject_faults", action="store_false", default=True)
    ap.add_argument("--run-id", help="stable run id shown by the dashboard")
    args = ap.parse_args()
    run(args.plant_id, args.broker, args.port, args.speed, args.loop,
        args.inject_faults, args.run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
