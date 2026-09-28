"""The Grand Finale story as a judge sees it, in real browsers (LOOP.md). Needs Python
Playwright + Chromium and a running demo stack (PULSETWIN_DEMO_MODE=1):

    python scripts/verify_ui_flow.py [--ui http://localhost:3000] [--api http://localhost:7860]

Two pages stay open for the whole run and are NEVER reloaded: a supervisor and a manager.
Everything asserted is what those pages render after the backend moves. Every wait is a
bounded poll on the rendered DOM. Results go to .loop/verification_ui.json and one line to
.loop/history.jsonl; exit 0 only when every check passed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
LOOP = ROOT / ".loop"
checks: list[dict] = []


def check(cid: str, criterion: str, ok, evidence) -> bool:
    status = "PASS" if ok else ("SKIPPED" if ok is None else "FAIL")
    checks.append({"check": cid, "criterion": criterion, "status": status, "evidence": evidence})
    print(f"[{status:7}] {criterion:2} {cid}: {json.dumps(evidence, default=str)[:260]}", flush=True)
    return bool(ok)


def login(api: str, user: str, pw: str) -> dict:
    req = urllib.request.Request(api + "/api/auth/login", method="POST",
                                 data=json.dumps({"username": user, "password": pw}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def api_post(api: str, path: str, token: str, body: dict) -> dict:
    req = urllib.request.Request(api + path, method="POST", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-PulseTwin-Token": token})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


T0 = time.time()
TEARDOWN = {"at": None}   # set when the harness starts closing pages


def _err(errors: list, text: str) -> None:
    """Every error is kept with its time; errors logged after TEARDOWN are the harness
    aborting in-flight fetches (a Next.js prefetch cut off by browser.close), not the
    product -- they are reported separately, never silently dropped."""
    errors.append({"t": round(time.time() - T0, 1), "teardown": TEARDOWN["at"] is not None,
                   "text": text})


def page_as(browser, ui: str, sess: dict, errors: list, path: str) -> Page:
    # The session is seeded BEFORE any page script runs, so the first navigation is the
    # page under test. Seeding it by visiting /overview first and then navigating away
    # aborted /overview's in-flight Next.js prefetches, which log console errors at t~3 s
    # -- a harness artifact that intermittently failed the error check.
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
    ctx.add_init_script("sessionStorage.setItem('pulsetwin.session', " + json.dumps(json.dumps(
        {k: sess[k] for k in ("role", "token", "user", "expires_at")})) + ");")
    page = ctx.new_page()
    page.on("console", lambda m: m.type == "error" and _err(errors, f"{path}: {m.text[:160]}"))
    page.on("pageerror", lambda e: _err(errors, f"{path}: {str(e)[:160]}"))
    page.on("response", lambda r: (r.status >= 500 or r.status == 429) and _err(errors, f"{r.status} {r.url}"))
    page.goto(ui + path, wait_until="domcontentloaded")
    return page


def poll(fn, timeout: float, every: float = 2.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            v = fn()
        except Exception:  # noqa: BLE001 -- a DOM mid-render is not a result
            v = None
        if v:
            return v, round(time.time() - t0)
        time.sleep(every)
    return None, round(time.time() - t0)


def stages(page: Page) -> dict[str, str]:
    """{label: mark} from the AI progress track, as rendered."""
    track = page.get_by_label("AI progress")
    if not track.count():
        return {}
    out = {}
    for li in track.first.locator("li").all():
        text = li.inner_text().split("\n")
        if len(text) >= 2:
            out[text[1].strip().title()] = text[0].strip()
    return out


def main() -> int:
    # A Windows console is cp1252: printing a build's '○' or a stage mark crashed the
    # gate itself, not the thing it was checking.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--ui", default="http://localhost:3000")
    ap.add_argument("--api", default="http://localhost:7860")
    ap.add_argument("--timeout", type=float, default=420)
    args = ap.parse_args()
    errors: list[str] = []
    t0 = time.time()
    sup_s, mgr_s = login(args.api, "supervisor", "sup2026"), login(args.api, "manager", "mgr2026")
    try:
        api_post(args.api, "/api/demo/reset", sup_s["token"], {"plant_id": "plant_a"})
    except Exception as exc:  # noqa: BLE001
        check("reset", "T", False, repr(exc)[:200])
        return finish(t0, errors)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        sup = page_as(browser, args.ui, sup_s, errors, "/supervisor")
        mgr = page_as(browser, args.ui, mgr_s, errors, "/manager")

        # A + J: both pages calm after a reset.
        ok, secs = poll(lambda: "No incidents in the current run" in sup.inner_text("body"), 30)
        check("supervisor-clean-start", "A", ok, {"after_s": secs})
        def strip() -> str:
            loc = sup.get_by_label("Line status")
            return " ".join(loc.first.inner_text().split()) if loc.count() else ""
        ok, secs = poll(lambda: "Healthy" in strip() and "Monitoring" in strip(), 30)
        check("supervisor-status-healthy", "A", ok, {"strip": strip(), "after_s": secs})
        ok, secs = poll(lambda: "No approvals required" in mgr.inner_text("body"), 30)
        check("manager-empty", "J", ok, {"after_s": secs})

        # B: the vehicle list moves without a reload.
        def rows():
            return [r.inner_text().replace("\n", " ")[:60] for r in sup.locator("li button[aria-expanded]").all()]
        first, _ = poll(lambda: rows(), 40)
        later, secs = poll(lambda: (lambda r: r if r and r != first else None)(rows()), 30)
        check("vehicles-live", "B", bool(first) and bool(later), {"after_s": secs, "rows": len(first or [])})

        # Start scenario A from the floor's own control.
        sup.select_option("select[aria-label='Demo preset']", "bottleneck")

        # G: the stage track appears and MOVES, from backend state, with no reload.
        seen: list[dict] = []

        def track_moves():
            s = stages(sup)
            if s and (not seen or s != seen[-1]):
                seen.append(s)
            return len(seen) >= 3 and seen[-1].get("Recommend") == "✓"
        ok, secs = poll(track_moves, args.timeout)
        check("ai-stages", "G", ok, {"after_s": secs, "snapshots": seen[-3:]})

        # K + L: the manager page (never reloaded) gains exactly this decision.
        ok, secs = poll(lambda: mgr.locator("article").count() > 0, 90)
        check("manager-card-appears", "K", ok, {"after_s": secs})
        s_ok, _ = poll(lambda: "Awaiting manager decision" in strip() and "Degraded" in strip()
                       and "ATTENTION Nothing" not in strip().upper().replace("NOTHING", "Nothing"), 30)
        check("supervisor-status-escalated", "G", s_ok, {"strip": strip()})
        if ok:
            card = mgr.locator("article").first
            text = card.inner_text()
            labels = ["PROBLEM", "RECOMMENDATION", "EXPECTED IMPACT", "RISK", "WHY YOUR APPROVAL IS REQUIRED"]
            primary = [card.get_by_role("button", name=n, exact=True) for n in ("Approve", "Explain")]
            secondary = [card.get_by_role("button", name=n, exact=True)
                         for n in ("Modify", "Reject", "Request more evidence")]
            check("manager-card-layout", "L",
                  all(l in text.upper() for l in labels) and all(b.count() == 1 for b in primary + secondary),
                  {"labels": [l for l in labels if l in text.upper()],
                   "primary": [b.count() for b in primary], "secondary": [b.count() for b in secondary]})
            check("twin-result-on-card", "I", "Not simulated" not in text and "+0.00" not in text,
                  {"expected_impact": next((l for l in text.split("\n") if "blocked" in l or "throughput" in l), None)})

            # H: the Explain drawer tells the whole story.
            card.get_by_role("button", name="Explain", exact=True).click()
            drawer_ok, _ = poll(lambda: mgr.get_by_role("dialog").count() == 1 and
                                "RECOMMENDATION" in mgr.get_by_role("dialog").inner_text().upper(), 20, 1)
            dtext = mgr.get_by_role("dialog").inner_text().upper() if drawer_ok else ""
            sections = ["OBSERVATION", "WHAT THE AI INVESTIGATED", "POSSIBLE EXPLANATIONS", "RULED OUT",
                        "EVIDENCE THAT MATTERED", "DIGITAL TWIN EXPERIMENT", "RECOMMENDATION",
                        "WHY YOUR APPROVAL IS REQUIRED"]
            check("explain-drawer", "H", drawer_ok and all(s in dtext for s in sections),
                  {"missing": [s for s in sections if s not in dtext]})
            mgr.keyboard.press("Escape")

            # M + O: approve in the UI; the gateway reference comes back.
            card.get_by_role("button", name="Approve", exact=True).click()
            ref, secs = poll(lambda: next((t for t in [mgr.get_by_role("status").first.inner_text()]
                                           if "reference" in t), None), 30, 1)
            check("approve-in-ui", "O", bool(ref), {"status": ref, "after_s": secs})

            # N: the supervisor page, never reloaded, shows the approval and then verification.
            ok, secs = poll(lambda: stages(sup).get("Approve") == "✓", 60)
            check("supervisor-sees-approval", "N", ok, {"after_s": secs, "stages": stages(sup)})
            ok, secs = poll(lambda: stages(sup).get("Verify") == "✓", args.timeout)
            check("supervisor-sees-verification", "R", ok, {"after_s": secs, "stages": stages(sup)})
        else:
            for c, k in (("manager-card-layout", "L"), ("explain-drawer", "H"), ("approve-in-ui", "O"),
                         ("supervisor-sees-approval", "N")):
                check(c, k, None, "no card")
        TEARDOWN["at"] = round(time.time() - T0, 1)
        sup.screenshot(path=str(LOOP / "ui_supervisor.png"), full_page=False)
        mgr.screenshot(path=str(LOOP / "ui_manager.png"), full_page=False)
        browser.close()
    live = [e for e in errors if not e["teardown"]]
    check("no-console-or-429-errors", "W", not live,
          {"during_flow": live[:6], "after_teardown_began": [e for e in errors if e["teardown"]][:4],
           "teardown_at_s": TEARDOWN["at"]})
    return finish(t0, errors)


def finish(t0: float, errors: list[str]) -> int:
    ok = bool(checks) and all(c["status"] == "PASS" for c in checks)
    out = {"tool": "scripts/verify_ui_flow.py", "at": datetime.now(timezone.utc).isoformat(),
           "seconds": round(time.time() - t0), "result": "PASS" if ok else "FAIL", "checks": checks}
    LOOP.mkdir(exist_ok=True)
    (LOOP / "verification_ui.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    with (LOOP / "history.jsonl").open("a", encoding="utf-8") as h:
        h.write(json.dumps({"at": out["at"], "event": "ui_flow", "result": out["result"],
                            "failed": [c["check"] for c in checks if c["status"] != "PASS"]}) + "\n")
    print(f"\nRESULT {out['result']} in {out['seconds']} s -- "
          f"{sum(c['status'] == 'PASS' for c in checks)}/{len(checks)} checks passed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
