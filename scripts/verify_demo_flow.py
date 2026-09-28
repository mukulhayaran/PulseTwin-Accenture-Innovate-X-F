"""Grand Finale flow check against a RUNNING stack (LOOP.md). Stdlib only; every wait is
bounded; nothing here trusts the UI or an LLM -- each check reads the API's persisted state.

    python scripts/verify_demo_flow.py [--api http://localhost:7860] [--speed 60]
                                       [--replay] [--timeout 480]

Each check maps to an acceptance criterion in .loop/goal.json. The result goes to
.loop/verification.json (overwritten) and one line to .loop/history.jsonl. Exit code 0 only
when every check ran and passed; a check that could not run (an earlier step failed) is
SKIPPED, which also fails the run.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOOP = ROOT / ".loop"
USERS = {"supervisor": "sup2026", "manager": "mgr2026", "leadership": "lead2026"}
PLANT = "plant_a"


class Api:
    def __init__(self, base: str):
        self.base, self.tokens = base.rstrip("/"), {}
        self.slowest: tuple[float, str] = (0.0, "")   # the demo UI polls these same calls
        self.durations: list[float] = []

    def call(self, method: str, path: str, role: str | None = None, body=None):
        headers = {"Content-Type": "application/json"}
        if role:
            headers["X-PulseTwin-Token"] = self.tokens[role]
        data = json.dumps(body).encode() if body is not None else (b"{}" if method == "POST" else None)
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"null")
            except ValueError:
                return e.code, None
        except (TimeoutError, urllib.error.URLError) as e:
            return 0, repr(e)          # recorded as slow below; a poll retries
        finally:
            took = time.time() - t0
            self.durations.append(took)
            if took > self.slowest[0]:
                self.slowest = (round(took, 2), f"{method} {path}")

    def get(self, path, role="manager"):
        code, body = self.call("GET", path, role)
        if code != 200:
            raise RuntimeError(f"GET {path} -> {code} {str(body)[:200]}")
        return body

    def login(self):
        for u, pw in USERS.items():
            code, body = self.call("POST", "/api/auth/login", None, {"username": u, "password": pw})
            if code != 200:
                raise RuntimeError(f"login {u} -> {code}")
            self.tokens[u] = body["token"]


class Report:
    def __init__(self):
        self.checks: list[dict] = []

    def add(self, cid: str, criterion: str, ok: bool | None, evidence):
        status = "PASS" if ok else ("SKIPPED" if ok is None else "FAIL")
        self.checks.append({"check": cid, "criterion": criterion, "status": status,
                            "evidence": evidence})
        print(f"[{status:7}] {criterion:2} {cid}: {json.dumps(evidence, default=str)[:300]}",
              flush=True)
        return ok


def wait(fn, timeout: float, every: float = 3.0):
    """Poll fn() until it returns truthy or the deadline passes. Returns (value, seconds)."""
    t0 = time.time()
    while True:
        try:
            v = fn()
        except RuntimeError:
            v = None
        if v:
            return v, round(time.time() - t0)
        if time.time() - t0 > timeout:
            return None, round(time.time() - t0)
        time.sleep(every)


def clean_state(api: Api, rep: Report, tag: str) -> dict | None:
    """Criterion A: the dashboard a judge sees right after a (re)start."""
    run = api.get(f"/api/simulations/active?plant_id={PLANT}", "supervisor")
    run = (run or {}).get("run") if isinstance(run, dict) and "run" in run else run
    incs = api.get(f"/api/incidents?plant_id={PLANT}&scope=current", "supervisor")["incidents"]
    open_incs = [i["incident_id"] for i in incs if i["status"] not in ("closed", "resolved")]
    pending = api.get(f"/api/decisions/pending?plant_id={PLANT}", "manager")["decisions"]
    rep.add(f"{tag}:no-open-incidents", "A", not open_incs, {"open": open_incs[:5]})
    rep.add(f"{tag}:no-pending-approvals", "A", not pending,
            {"pending": [d["recommendation_id"] for d in pending][:5]})
    return run


def vehicles_progress(api: Api, rep: Report, run_id: str, tag: str) -> set[str]:
    """Criterion B: vehicles are produced by the runtime and move between two samples."""
    def snap():
        v = api.get(f"/api/vehicles?plant_id={PLANT}&run_id={run_id}&limit=100", "supervisor")
        return {x["unit_id"]: x["current_station"] for x in v["vehicles"]} or None
    a, _ = wait(lambda: (lambda x: x if x and len(x) >= 5 else None)(snap()), 60)
    if not a:
        rep.add(f"{tag}:vehicles-appear", "B", False, "fewer than 5 vehicles within 60 s")
        return set()
    time.sleep(8)
    b = snap() or {}
    moved = sum(1 for u in a if u in b and b[u] != a[u])
    # A vehicle that was already on the line moved station (not just "new rows appeared"
    # after an empty first look -- the verifier caught that loophole).
    rep.add(f"{tag}:vehicles-move", "B", moved > 0,
            {"first": len(a), "second": len(b), "moved": moved, "new": len(set(b) - set(a))})
    return set(a) | set(b)


def session_vehicles(api: Api, run_id: str | None) -> set[str]:
    """Every unit id a run has produced so far (paged)."""
    if not run_id:
        return set()
    out: set[str] = set()
    for offset in range(0, 1000, 100):
        v = api.get(f"/api/vehicles?plant_id={PLANT}&run_id={run_id}&limit=100&offset={offset}",
                    "supervisor")["vehicles"]
        out |= {x["unit_id"] for x in v}
        if len(v) < 100:
            break
    return out


def run_flow(api: Api, rep: Report, speed: float, timeout: float, tag: str) -> dict:
    """One Grand Finale pass. Returns facts for the replay comparison."""
    facts: dict = {}
    code, started = api.call("POST", "/api/demo/scenarios/bottleneck/start", "supervisor",
                             {"plant_id": PLANT, "speed": speed})
    if not rep.add(f"{tag}:demo-start", "A", code == 200, {"code": code, "body": str(started)[:200]}):
        return facts
    run_id = facts["run_id"] = started["run_id"]
    time.sleep(3)
    clean_state(api, rep, f"{tag}:after-start")
    facts["units"] = vehicles_progress(api, rep, run_id, tag)
    sim = api.get(f"/api/simulations/{run_id}", "supervisor")
    facts["first_ts"] = sim.get("simulation_time")

    # D: the line runs healthy before the scenario's problem develops -- no incident in
    # the first stretch of the run, at any station.
    early_incs = [i for i in api.get(f"/api/incidents?plant_id={PLANT}&scope=current",
                                     "supervisor")["incidents"] if i["sim_run_id"] == run_id]
    rep.add(f"{tag}:healthy-before-scenario", "D", not early_incs,
            [(i["incident_id"], i["station_id"], i["title"][:60]) for i in early_incs])

    # E: the SCENARIO's problem, detected by the runtime, not by a person.
    station = started.get("station_id")

    def incident():
        rows = api.get(f"/api/incidents?plant_id={PLANT}&scope=current", "supervisor")["incidents"]
        return next((i for i in rows if i["sim_run_id"] == run_id and i["source"] == "watch"
                     and i["station_id"] == station), None)
    inc, secs = wait(incident, timeout)
    if not rep.add(f"{tag}:auto-detected", "E", bool(inc), {"after_s": secs,
                   "incident": inc and inc["incident_id"], "source": inc and inc["source"]}):
        return facts
    iid = facts["incident_id"] = inc["incident_id"]

    # K + J: the manager queue must NOT hold it while the investigation is unfinished.
    early = api.get(f"/api/decisions/pending?plant_id={PLANT}", "manager")["decisions"]
    d0 = api.get(f"/api/incidents/{iid}", "manager")
    inv0 = d0.get("investigation") or {}
    premature = [d for d in early if d["incident_id"] == iid] and inv0.get("status") != "completed"
    rep.add(f"{tag}:queue-empty-while-investigating", "J", not premature,
            {"investigation_status": inv0.get("status")})

    # F: automatic investigation with dynamic specialists and competing hypotheses.
    def decided():
        d = api.get(f"/api/incidents/{iid}", "manager")
        inv = d.get("investigation") or {}
        return d if inv.get("status") in ("completed", "failed") and d.get("recommendation") else None
    det, secs = wait(decided, timeout)
    if not rep.add(f"{tag}:investigation-completes", "F", bool(det), {"after_s": secs}):
        return facts
    st = det["investigation"]["state"]
    specialists = [s.get("specialist") or s.get("agent") for s in det["investigation"]["steps"]]
    rep.add(f"{tag}:dynamic-specialists", "F", len(set(filter(None, specialists))) >= 2,
            {"steps": specialists})
    hyps = st.get("hypotheses") or []
    rep.add(f"{tag}:competing-hypotheses", "F", len(hyps) >= 2 and
            any(h["status"] in ("rejected", "weakened") for h in hyps),
            [(h["title"], round(h["confidence"], 2), h["status"]) for h in hyps[:4]])

    # I: the twin compared scenarios and the backend computed a non-trivial result.
    exps = st.get("experiments") or []
    arms = [(e.get("label"), (e.get("objective_delta") or {}).get("mean"),
             e.get("baseline_uph"), e.get("verdict")) for e in exps]
    rep.add(f"{tag}:twin-experiment-ran", "I", bool(exps) and all(e.get("baseline_uph") for e in exps),
            arms)
    rep.add(f"{tag}:twin-result-informative", "I",
            any(abs((e.get("objective_delta") or {}).get("mean") or 0) > 1e-6 for e in exps),
            {"deltas": [a[1] for a in arms], "verdicts": [a[3] for a in arms]})

    # H: the plain-words explanation exists and is generated from the record.
    titles = [s["title"] for s in det.get("explanation") or []]
    rep.add(f"{tag}:explanation", "H", {"What happened", "What it checked", "What it tested",
                                        "What it concluded", "What it decided"} <= set(titles), titles)

    # K: the approval request reaches the queue, governor-gated.
    def queued():
        rows = api.get(f"/api/decisions/pending?plant_id={PLANT}", "manager")["decisions"]
        return next((d for d in rows if d["incident_id"] == iid), None)
    card, secs = wait(queued, 60)
    if not rep.add(f"{tag}:decision-queued", "K", bool(card),
                   {"after_s": secs, "rec": card and card["recommendation_id"],
                    "approval": card and card["approval"]["reason"]}):
        return facts
    rec_id = facts["rec_id"] = card["recommendation_id"]
    lead = api.get(f"/api/decisions/pending?plant_id={PLANT}", "leadership")["decisions"]
    rep.add(f"{tag}:queue-role-scoped", "J", not any(d["incident_id"] == iid for d in lead),
            {"leadership_sees": len(lead)})
    ev = api.get(f"/api/decisions/{rec_id}/evidence", "manager")
    rep.add(f"{tag}:evidence-drawer", "H", bool(ev.get("points")) and bool(ev.get("conclusion")),
            {"points": len(ev.get("points") or []), "twin_tests": len(ev.get("twin_tests") or [])})

    # M: a supervisor cannot approve; the manager's approval is persisted and audited.
    code, _ = api.call("POST", f"/api/recommendations/{rec_id}/approve", "supervisor",
                       {"option_id": card["action"]["option_id"]})
    rep.add(f"{tag}:supervisor-cannot-approve", "M", code == 403, {"code": code})
    code, ap = api.call("POST", f"/api/recommendations/{rec_id}/approve", "manager",
                        {"option_id": card["action"]["option_id"], "note": "loop verification"})
    if not rep.add(f"{tag}:manager-approves", "M", code == 200 and ap.get("action"),
                   {"code": code, "body": str(ap)[:200]}):
        return facts
    action_id = ap["action"]["action_id"]
    code, ex = api.call("POST", f"/api/actions/{action_id}/execute", "manager")
    ref = (ex or {}).get("action", {}).get("external_reference")
    rep.add(f"{tag}:gateway-executes", "O", code == 200 and bool(ref), {"code": code, "ref": ref})
    code2, ex2 = api.call("POST", f"/api/actions/{action_id}/execute", "manager")
    rep.add(f"{tag}:gateway-idempotent", "O",
            code2 == 200 and (ex2 or {}).get("action", {}).get("external_reference") == ref,
            {"code": code2})
    audit = api.get("/api/audit/log?limit=200", "leadership")
    rows = [r for r in audit.get("entries") or audit.get("rows") or audit.get("log") or []
            if rec_id in json.dumps(r, default=str) or action_id in json.dumps(r, default=str)]
    rep.add(f"{tag}:audited", "M", len(rows) >= 2, {"rows": [r.get("action") for r in rows]})
    hist = api.get(f"/api/decisions/history?plant_id={PLANT}", "manager")["history"]
    rep.add(f"{tag}:in-history", "R", any(h["recommendation_id"] == rec_id for h in hist), {})
    pending = api.get(f"/api/decisions/pending?plant_id={PLANT}", "manager")["decisions"]
    rep.add(f"{tag}:queue-cleared", "R", not any(d["recommendation_id"] == rec_id for d in pending), {})

    # P + Q + R: gradual recovery, predicted vs actual, then closure.
    seen_observing = {"v": False}

    def evaluated():
        d = api.get(f"/api/incidents/{iid}", "manager")
        o = next((a.get("outcome") for a in d["actions"] if a["action_id"] == action_id), None)
        if o and o["status"] == "observing":
            seen_observing["v"] = True
        return (d, o) if o and o["status"] != "observing" else None
    got, secs = wait(evaluated, timeout)
    rep.add(f"{tag}:recovery-observed-not-instant", "P", seen_observing["v"] and bool(got),
            {"after_s": secs, "saw_observing": seen_observing["v"]})
    if got:
        d, o = got
        rep.add(f"{tag}:predicted-vs-actual", "Q", o.get("baseline_value") is not None and
                o.get("actual_value") is not None and "prediction_error" in o,
                {k: o.get(k) for k in ("metric", "baseline_value", "actual_value",
                                       "expected_change", "prediction_error", "status")})
        improved = o["status"] in ("successful", "partially_successful")
        rep.add(f"{tag}:incident-resolved", "R", d["incident"]["status"] == "resolved" and improved,
                {"incident": d["incident"]["status"], "outcome": o["status"]})
        case = d.get("case") or {}
        rep.add(f"{tag}:case-memory", "S", bool(case.get("actual_outcome")) and
                case.get("human_decision") == "approved",
                {"case": case.get("case_id"), "root_cause": case.get("root_cause")})
    else:
        for c, k in (("predicted-vs-actual", "Q"), ("incident-resolved", "R"), ("case-memory", "S")):
            rep.add(f"{tag}:{c}", k, None, "outcome never evaluated")
    return facts


def main() -> int:
    # A Windows console is cp1252: printing a build's '○' or a stage mark crashed the
    # gate itself, not the thing it was checking.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:7860")
    ap.add_argument("--speed", type=float, default=60)
    ap.add_argument("--timeout", type=float, default=480, help="per wait, seconds")
    ap.add_argument("--replay", action="store_true", help="reset and run the flow a second time")
    args = ap.parse_args()
    api, rep = Api(args.api), Report()
    t0 = time.time()
    try:
        code, health = api.call("GET", "/api/health")
        if not rep.add("server-reachable", "W", code == 200, {"code": code}):
            raise SystemExit(finish(rep, t0))
        api.login()

        # T/A: an explicit reset returns the demo to a clean healthy line.
        code, body = api.call("POST", "/api/demo/reset", "supervisor", {"plant_id": PLANT})
        rep.add("reset-endpoint", "T", code == 200, {"code": code, "body": str(body)[:200]})
        reset1 = (body or {}).get("run_id") if code == 200 else None
        clean_state(api, rep, "fresh")
        seen1 = session_vehicles(api, reset1)
        first = run_flow(api, rep, args.speed, args.timeout, "run1")

        if args.replay:
            code, body = api.call("POST", "/api/demo/reset", "supervisor", {"plant_id": PLANT})
            rep.add("replay:reset", "T", code == 200, {"code": code})
            reset2 = (body or {}).get("run_id") if code == 200 else None
            clean_state(api, rep, "replay:fresh")
            seen2 = session_vehicles(api, reset2)
            second = run_flow(api, rep, args.speed, args.timeout, "run2")
            rep.add("replay:new-session", "T", bool(second.get("run_id")) and
                    second.get("run_id") != first.get("run_id"),
                    {"run1": first.get("run_id"), "run2": second.get("run_id")})
            # Everything each session put on screen: its reset run AND its scenario run.
            all1 = (seen1 | session_vehicles(api, reset1) | first.get("units", set())
                    | session_vehicles(api, first.get("run_id")))
            all2 = (seen2 | session_vehicles(api, reset2) | second.get("units", set())
                    | session_vehicles(api, second.get("run_id")))
            rep.add("replay:new-vehicle-ids", "T", bool(all2) and not (all1 & all2),
                    {"session1": len(all1), "session2": len(all2), "overlap": len(all1 & all2)})
            rep.add("replay:new-incident", "T", bool(second.get("incident_id")) and
                    second.get("incident_id") != first.get("incident_id"), {})
        # The browser polls these same endpoints asynchronously: one slow response delays one
        # refresh, it does not freeze the screen. What a judge perceives is the typical
        # latency (p95 < 1 s); a real stall is the ceiling (any call > 3 s -- tightened from 10 s
        # on the independent verifier's advice: it still catches the old 5.37 s spike). The first
        # version failed a whole run on a single 5.4 s call -- a flaky gate, not a finding
        # (LOOP finding F15).
        d = sorted(api.durations)
        p95 = round(d[int(0.95 * (len(d) - 1))], 2) if d else None
        rep.add("api-responsive", "W", bool(d) and p95 < 1.0 and api.slowest[0] < 3.0,
                {"calls": len(d), "p95_s": p95, "slowest_s": api.slowest[0], "call": api.slowest[1]})
    except Exception as exc:  # noqa: BLE001 -- a crash is itself a result
        rep.add("harness-error", "W", False, repr(exc)[:300])
    return finish(rep, t0)


def finish(rep: Report, t0: float) -> int:
    ok = bool(rep.checks) and all(c["status"] == "PASS" for c in rep.checks)
    out = {"tool": "scripts/verify_demo_flow.py", "at": datetime.now(timezone.utc).isoformat(),
           "seconds": round(time.time() - t0), "result": "PASS" if ok else "FAIL",
           "checks": rep.checks}
    LOOP.mkdir(exist_ok=True)
    (LOOP / "verification.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    with (LOOP / "history.jsonl").open("a", encoding="utf-8") as h:
        h.write(json.dumps({"at": out["at"], "event": "demo_flow", "result": out["result"],
                            "failed": [c["check"] for c in rep.checks if c["status"] != "PASS"]})
                + "\n")
    print(f"\nRESULT {out['result']} in {out['seconds']} s -- "
          f"{sum(c['status'] == 'PASS' for c in rep.checks)}/{len(rep.checks)} checks passed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
