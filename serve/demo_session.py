"""Demo sessions: a clean, repeatable start for a live presentation (LOOP criteria A, T, U).

Two things make a fresh demo honest rather than staged:

1. **Where the recorded shift starts.** Every run replays the plant's recorded stream, and
   that stream has real episodes in it -- the designed Mode C torque deficit at S3 trips
   the tool-wear watch 8 simulated minutes in (configs/plant_a.yaml `watch:`). A demo that
   always starts at line 0 therefore always opens on that incident. `quiet_starts` replays
   the stream through a cold `LineWatch` and returns the points where the line then runs
   for `QUIET_S` without a single firing. A demo run starts at one of them, and each new
   session takes the NEXT one, so a replay after a reset shows different vehicles and
   timestamps. Nothing is suppressed: the watch still sees every event of the run.
2. **Reset closes, it never deletes.** `reset` stops the plant's run, closes its open
   incidents, expires pending approvals and starts a fresh normal run. Every row stays
   (audit, case memory, outcome history), and it only runs when `PULSETWIN_DEMO_MODE` is on
   -- a production backend refuses it, and a normal restart never touches history.

Import wall: stdlib only.
"""
from __future__ import annotations

import functools
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from serve import commander, db
from serve import incident_evidence as E
from serve.watch import LineWatch, load_rules

DEMO_ENV = "PULSETWIN_DEMO_MODE"
# The demo's horizon in SIMULATED time: healthy lead-in, the scenario, the investigation's
# 600 s telemetry wait, approval and the outcome window. Measured on the Docker stack, a
# full pass of scenario A spans under 3 simulated hours at speed 60.
QUIET_S = 3 * 3600.0
# Candidate start points 2 simulated hours apart (~110 vehicles at line rate), so two
# consecutive sessions show different vehicles, not the same ones 10 minutes later.
STEP_S = 7200.0
MIN_LINE_WALL_S = 1800.0  # a healthy demo line must keep moving for 30 minutes
# A run counts as a session someone SAW once it produced this many events (5-13 s of line
# at the healthy pacing). A double-clicked Reset's first run showed 8-25 events in ~1 s;
# counting it made the next session return to the one before (independent verifier).
SEEN_EVENTS = 100


def enabled() -> bool:
    return os.environ.get(DEMO_ENV, "").strip().lower() in ("1", "true", "yes", "on")


# One computation per cache, not one per concurrent caller: an unlocked lru_cache let a
# request arriving during the startup warm-up redo ~15 s of stream replay in parallel.
_CACHE_LOCK = threading.Lock()


def quiet_starts(artifacts: str, plant_id: str) -> tuple[int, ...]:
    """Stream line indices after which a cold watch stays silent for QUIET_S. Cached: the
    stream and the rules are baked artifacts that do not change while the process runs."""
    with _CACHE_LOCK:
        return _quiet_starts(artifacts, plant_id)


@functools.lru_cache(maxsize=4)
def _quiet_starts(artifacts: str, plant_id: str) -> tuple[int, ...]:
    rules = load_rules(Path(artifacts), plant_id)
    path = Path(artifacts) / f"stream_{plant_id}.jsonl"
    if rules is None or not path.exists():
        return (0,)
    events: list[tuple[float, dict[str, Any]]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            events.append((datetime.fromisoformat(e["ts"]).timestamp(), e))
    if not events:
        return (0,)
    starts: list[int] = []
    i, t_end = 0, events[-1][0]
    next_candidate = events[0][0]
    while i < len(events) and events[i][0] + QUIET_S <= t_end:
        if events[i][0] >= next_candidate:
            next_candidate = events[i][0] + STEP_S
            watch, j, quiet = LineWatch(rules), i, True
            while j < len(events) and events[j][0] <= events[i][0] + QUIET_S:
                if watch.observe(events[j][1]):
                    quiet = False
                    break
                j += 1
            # At least QUIET_S after the previous start: a session uses at most QUIET_S
            # of the shift, so two sessions can never show the same vehicle.
            if quiet and (not starts or events[i][0] - events[starts[-1]][0] >= QUIET_S):
                starts.append(i)
        i += 1
    return tuple(starts) or (0,)


def _line_times(artifacts: str, plant_id: str) -> tuple[float, ...]:
    """Epoch seconds of every stream line (cached; floats only, ~1 MB)."""
    with _CACHE_LOCK:
        return _line_times_cached(artifacts, plant_id)


@functools.lru_cache(maxsize=4)
def _line_times_cached(artifacts: str, plant_id: str) -> tuple[float, ...]:
    path = Path(artifacts) / f"stream_{plant_id}.jsonl"
    if not path.exists():
        return ()
    with path.open("r", encoding="utf-8") as f:
        return tuple(datetime.fromisoformat(json.loads(line)["ts"]).timestamp() for line in f)


def warm(artifacts: str, plant_id: str) -> None:
    """Everything a reset or a preset reads, computed once at startup. The preset's first
    call after a restart took 2.4 s parsing the stream baseline (E.baseline, cached per
    process) -- the only slow demo call in the backend's own latency histogram."""
    quiet_starts(artifacts, plant_id)
    _line_times(artifacts, plant_id)
    E.baseline(artifacts, plant_id)


def _audited_start(ops: Any, run_id: str) -> int | None:
    """The stream line a demo run started at, from its own audit record."""
    row = db.query_one(ops, "SELECT payload FROM audit_log WHERE resource_id=? AND action IN "
                            "('demo_reset','demo_scenario_started') ORDER BY id DESC LIMIT 1",
                       (run_id,))
    payload = (row or {}).get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = None
    value = (payload or {}).get("start_line") if isinstance(payload, dict) else None
    return int(value) if value is not None else None


def session_start(ops: Any, artifacts: Path, plant_id: str) -> int:
    """The start point for a NEW session: a different one from the last run anyone SAW.

    Keyed on what was shown, not on a counter. Two counters failed independent
    verification: per run (a session is two runs), then per reset -- a reset whose run
    showed nothing (event_count 0, reset again at once) still flipped the parity, and the
    next session replayed the previous visible session's vehicles, 47/47."""
    starts = quiet_starts(str(artifacts), plant_id)
    for r in db.query_all(ops, "SELECT run_id FROM simulation_runs WHERE plant_id=? AND "
                               "event_count >= ? ORDER BY started_at DESC LIMIT 20",
                          (plant_id, SEEN_EVENTS)):
        last = _audited_start(ops, r["run_id"])
        if last is not None:
            return next((x for x in starts if x != last), starts[0])
    return starts[0]


def next_start(ops: Any, live: Any, artifacts: Path, plant_id: str) -> int:
    """Where a demo PRESET starts: the current session's start point, read from the latest
    reset's audit record -- so it holds even after the healthy run has ended (reading the
    live run's control did not, and a preset then jumped sessions)."""
    row = db.query_one(ops, "SELECT resource_id FROM audit_log WHERE action='demo_reset' "
                            "ORDER BY id DESC LIMIT 1")
    start = _audited_start(ops, row["resource_id"]) if row else None
    return start if start is not None else session_start(ops, artifacts, plant_id)


def healthy_speed(artifacts: Path, plant_id: str, start_line: int) -> float:
    """Pace the reset's healthy line so the remaining recorded shift lasts at least
    MIN_LINE_WALL_S of presentation. At speed 60 the late start point ran out of stream in
    ~7.5 minutes and the line went dead on screen (independent verifier)."""
    times = _line_times(str(artifacts), plant_id)
    if not times or start_line >= len(times):
        return 60.0
    remaining = times[-1] - times[start_line]
    return max(5.0, min(60.0, remaining / MIN_LINE_WALL_S))


def reset(ops: Any, live: Any, artifacts: Path, plant_id: str, role: str) -> dict[str, Any]:
    """Close the current session and start a fresh, healthy one. Deletes nothing."""
    active = live.active(plant_id)
    stopped = None
    if active:
        live.stop(active["run_id"])
        stopped = active["run_id"]
    now = datetime.now().astimezone().isoformat()
    open_rows = db.query_all(ops, "SELECT incident_id FROM incidents WHERE plant_id=? AND status "
                                  "NOT IN ('closed','resolved')", (plant_id,))
    for r in open_rows:
        db.execute(ops, "UPDATE recommendations SET status='expired', updated_at=? WHERE "
                        "incident_id=? AND status IN ('proposed','withheld')",
                   (now, r["incident_id"]))
        # Under the commander's own per-investigation lock: advance() re-reads the status
        # inside it and skips a failed investigation, so an in-flight step cannot write
        # 'running' back over this (race noted by the loop's verifier).
        for inv in db.query_all(ops, "SELECT investigation_id FROM investigations WHERE "
                                     "incident_id=? AND status NOT IN ('completed','failed')",
                                (r["incident_id"],)):
            with commander._lock(inv["investigation_id"]):
                db.execute(ops, "UPDATE investigations SET status='failed', finished_at=? WHERE "
                                "investigation_id=? AND status NOT IN ('completed','failed')",
                           (now, inv["investigation_id"]))
        db.execute(ops, "UPDATE incidents SET status='closed', updated_at=? WHERE incident_id=?",
                   (now, r["incident_id"]))
    # The closed session's twin experiments still hold the concurrency slots
    # (serve/routers/experiments.py: max_concurrent); left running they refused the NEW
    # session's twin test with 429 -- measured on the first replay. Same semantics as the
    # cancel endpoint: a queued one is settled now, a running worker stops between seeds.
    db.execute(ops, "UPDATE simulation_experiments SET status='cancelled', cancel_requested=1, "
                    "finished_at=?, updated_at=? WHERE plant_id=? AND status='queued'",
               (now, now, plant_id))
    db.execute(ops, "UPDATE simulation_experiments SET cancel_requested=1, updated_at=? "
                    "WHERE plant_id=? AND status='running'", (now, plant_id))
    first = session_start(ops, artifacts, plant_id)
    speed = healthy_speed(artifacts, plant_id, first)
    run = live.start(plant_id, 42, "normal", speed, 0.0, None, None, 40000,
                     commander=True, start_line=first)
    db.execute(ops, "INSERT INTO audit_log (user_role, action, resource_type, resource_id, payload) "
                    "VALUES (?,?,?,?,?)",
               (role, "demo_reset", "simulation_run", run["run_id"],
                json.dumps({"stopped_run": stopped, "closed_incidents": len(open_rows),
                            "start_line": first, "speed": speed})))
    return {"run_id": run["run_id"], "stopped_run": stopped, "start_line": first, "speed": speed,
            "closed_incidents": [r["incident_id"] for r in open_rows]}
