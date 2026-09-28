# Engineering Loop Status

Rewritten after every iteration (LOOP.md). Machine state: `.loop/state.json`. Findings: `.loop/findings.json`.

# Current Goal
Make PulseTwin Grand-Finale ready. The criteria are A–W in `.loop/goal.json`; the stop condition is in `LOOP.md` §Stop.

# Current Overall Status
Iteration 5. Every criterion has maker evidence as PASS. Two things remain open:
- the last runtime confirmations below;
- independent re-derivation for the criteria listed under "Currently Broken".

# Verified Working
Evidence is from the Docker/Postgres stack, 2026-09-28.

**UI flow** (`scripts/verify_ui_flow.py`, run 7): 14/14 PASS, in two pages that are never reloaded.
- **A.** After a reset the page is clean. The status strip reads "Line Healthy · AI Monitoring".
- **J.** The manager page starts empty.
- **B.** The vehicle list moves live.
- **G.** The stage track moves forward only. The strip changes to "Degraded at S19 · Awaiting manager decision".
- **K.** The card appears without a reload.
- **L.** The card leads with 5 primary facts, Approve and Explain.
- **I.** The card shows the twin result: −21.27 points of blocking.
- **H.** The Explain drawer shows all 8 sections.
- **O.** Approve in the UI returns CMMS-WO-7966696.
- **N.** The supervisor sees the approval within 1 s.
- **R.** Verify shows done after 122 s.
- **W.** No console errors and no 429s.

**API flow** (`scripts/verify_demo_flow.py --replay`, run 4): 65/65 PASS, including:
- a second fresh replay with vehicle-id overlap 0;
- two informative twin tests: −20.6 and −21.3 points, `improved`;
- the slowest call at 1.36 s.

**Quality gates**
- `pytest tests/`: 878 passed in 31.6 min.
- `tsc`: PASS.
- `next build`: PASS.

**Telemetry realism (C)**, lag-1 autocorrelation on the stream:
- temperature 0.99, vibration 0.89, humidity 0.59, torque 0.42;
- the i.i.d. reference is 0.04.

**Independent verifier** (read-only subagent):
- **PASS:** A (4 resets at different points), D, G, I, O, U; T except vehicle ids; J (API parts).
- **FAIL on M:** leadership could reject and request investigation. This is now fixed: those two need L3, leadership gets 403 (live and in tests), and an anonymous reset gets 401.

# Currently Broken
Nothing is known to be broken. The following are **unconfirmed**:
- **API replay run 7** was stopped by the OS for low memory after 11/11 checks passed. It is the clean re-run of run 6, whose only miss was a 5.2 s demo start measured under concurrent test load (idle: 0.3–0.9 s).
- **"Needs attention" strip fix.** The cell now names stations with open incidents; before, it said "Nothing" while S19 was degraded. It passes `tsc` but is not rebuilt into Docker or checked in the UI.
- **Independent re-derivation still pending** for B C E F H K L N P Q R S V W, plus a re-check of M.

# Current Task
Close out the stop condition:
1. Rebuild the stack.
2. Run `verify_ui_flow.py`, then `verify_demo_flow.py --replay`, sequentially and with nothing else running.
3. Run the independent verifier on the pending criteria.
4. Write `docs/LOOP_COMPLETION_REPORT.md`.

# Next Candidate Tasks
1. **F10 (product decision).** The supervisor banner prints the injected scenario name before the AI detects it.
2. Run the UX pass again on the supervisor page with the status strip.

# Blockers
None in the code. The machine ran out of memory with the Docker stack, the pytest suite and Chromium running together. Run the heavy checks **one at a time**.

# Verification Evidence
- `.loop/verification.json` — last API flow.
- `.loop/verification_ui.json` — last UI flow.
- `.loop/ui_supervisor.png` and `.loop/ui_manager.png` — screenshots.
- `.loop/history.jsonl` — run history.

# Last Completed Iteration
**Iteration 5 — RBAC and the supervisor's first screen.**
- **Fixes:**
  - `serve/routers/incidents.py::_require_decision_authority`: reject and request-investigation need authority L3.
  - Demo reset needs a real sign-in.
  - Reset takes the commander's per-investigation lock.
  - `LineStatusStrip` added on /supervisor.
  - Stage track made monotonic.
  - Supervisor detail keeps polling until the incident is settled.
  - Explain drawer gains the Observation section.
- **Verified by:**
  - `tests/test_demo_reset.py` and `tests/test_incident_stages.py` (13 passed);
  - commander + reset tests: 41 passed;
  - live RBAC probe;
  - `verify_ui_flow` 14/14.
