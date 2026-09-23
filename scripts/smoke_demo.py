"""Dependency-free smoke check for the demo API."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request


# Match the browser's documented endpoint. On Windows+WSL, 127.0.0.1 can retain a
# stale mirrored listener even while localhost correctly resolves to Docker's IPv6 bind.
BASE = os.environ.get("PULSETWIN_API_BASE", "http://localhost:7860")


def request(path: str, body: dict | None = None) -> dict | list:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method="POST" if body is not None else "GET",
        headers={"Content-Type": "application/json", "X-PulseTwin-Role": "supervisor"},
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    try:
        health = request("/api/health")
        ready = request("/api/ready")
        stations = request("/api/config/stations?plant_id=plant_a")
        if health["status"] != "ok" or ready["status"] != "ready" or not stations["stations"]:
            raise RuntimeError("health/readiness/config check failed")
        active = request("/api/simulations/active?plant_id=plant_a")
        if active:
            if active.get("producer") == "mqtt":
                before = int(active.get("event_count") or 0)
                deadline = time.time() + 8
                current = active
                while time.time() < deadline:
                    time.sleep(0.5)
                    current = request(f"/api/simulations/{active['run_id']}")
                    if int(current.get("event_count") or 0) > before:
                        break
                if current["status"] != "running" or int(current.get("event_count") or 0) <= before:
                    raise RuntimeError(f"external MQTT run is not advancing: {current}")
                if os.environ.get("PULSETWIN_REQUIRE_FULL_DEMO") == "1":
                    complete = request("/api/demo/readiness")
                    if complete.get("status") != "ready":
                        raise RuntimeError(f"complete demo is not ready: {complete}")
                print(f"PulseTwin MQTT smoke check passed: {active['run_id']}")
                return 0
            request(f"/api/simulations/{active['run_id']}/stop", {})
        run = request("/api/simulations", {
            "plant_id": "plant_a", "seed": 42, "scenario": "normal",
            "speed": 500, "max_events": 10,
        })
        deadline = time.time() + 10
        while time.time() < deadline:
            current = request(f"/api/simulations/{run['run_id']}")
            if current["status"] in ("completed", "failed"):
                if current["status"] != "completed" or current["event_count"] < 10:
                    raise RuntimeError(f"synthetic run failed: {current}")
                print(f"PulseTwin smoke check passed: {run['run_id']}")
                return 0
            time.sleep(0.1)
        raise RuntimeError("synthetic run did not complete within 10 seconds")
    except (urllib.error.URLError, KeyError, RuntimeError) as exc:
        print(f"PulseTwin smoke check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
