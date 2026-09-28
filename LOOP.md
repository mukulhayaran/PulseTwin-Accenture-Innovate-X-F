# PulseTwin engineering loop

A bounded maker/checker loop that moves PulseTwin from an observable failing state to an
independently verified passing state. The controller procedure is the
`pulsetwin-loop` skill (`.claude/skills/pulsetwin-loop/SKILL.md`); the roles are the
`maker` and `verifier` subagents (`.claude/agents/`).

## Goal
PulseTwin must function as a polished, dynamic, production-oriented AI-driven
manufacturing digital twin suitable for a live Grand Finale presentation. That means:
- a clean Supervisor experience and a minimal Manager approval experience;
- a dynamically evolving agentic AI investigation;
- explainable digital-twin reasoning;
- human-governed actions and closed-loop outcome validation;
- a repeatable fresh demo runtime.

The loop does **not** stop because code compiles, a page loads, unit tests pass, a
component exists or an endpoint returns 200. It stops only when the acceptance criteria
(A–W in `.loop/goal.json`) are verified end to end.

## Files
| File | Role | Committed |
|---|---|---|
| `.loop/goal.json` | Stable acceptance criteria A–W and how each is verified | yes |
| `.loop/state.json` | Per-criterion status + evidence, current objective, failures | no (runtime) |
| `.loop/findings.json` | Audit findings, ranked | no (runtime) |
| `.loop/verification.json` | Last `verify_demo_flow` / `verify_ui_flow` result | no (runtime) |
| `.loop/history.jsonl` | One line per iteration / verification run | no (runtime) |
| `docs/ENGINEERING_LOOP_STATUS.md` | Human-readable ledger, rewritten each iteration | yes |

## Commands
| Command | What it proves | Cost |
|---|---|---|
| `.venv/Scripts/python scripts/verify.py` | serve import wall, configs, QA regressions, commander tests, `tsc` | ~3 min |
| `.venv/Scripts/python scripts/verify.py --full` | the whole pytest suite + `tsc` + `next build` | ~25 min |
| `python scripts/verify_demo_flow.py [--replay]` | the Grand Finale flow through the API of the running stack: reset → clean → vehicles move → auto-detect → investigation → twin → queue → RBAC → approve → gateway → recovery → resolved → memory (→ reset → second run) | ~10 min per run |
| `python scripts/verify_ui_flow.py` | the same story as a judge sees it in the browser (Playwright): stages, live updates, manager card, cross-panel update | ~10 min |
| `docker compose -f docker-compose.prod.yml up --build -d backend frontend` | redeploy after a change (runtime checks run against Docker/Postgres) | ~2 min |

`make` is not installed; these are the raw commands. Demo users: supervisor/sup2026,
manager/mgr2026, leadership/lead2026.

## Iteration
1. **Inspect.** Which exact criterion is failing? Get evidence from a command, not by reading code.
2. **Objective.** Write the criterion and the expected observable result into `state.json.current`.
3. **Make.** Make the smallest coherent change in the existing architecture.
4. **Verify locally.** Run the narrowest test first, then `scripts/verify.py`.
5. **Verify at runtime.** Rebuild the stack and run the criterion's runtime check.
6. **Check independently.** The `verifier` subagent is read-only and adversarial, and returns PASS / FAIL / INCONCLUSIVE with evidence.
7. **Record.**
   - PASS: update `state.json`, `history.jsonl` and the status ledger, then choose the next objective.
   - FAIL: record the failure signature, command, raw failure, hypothesis and fix tried. Diagnose; never repeat an identical fix.

## Priority (pick exactly one objective per iteration)
1. Blocks the complete demo flow.
2. Correctness.
3. The agentic-AI story.
4. Supervisor/Manager UX.
5. Dynamic behaviour.
6. Explainability.
7. Security or data isolation.
8. Test reliability.
9. Performance.
10. Cosmetics.

No CSS polish while an end-to-end flow is broken.

## Budgets
- Per task: at most 5 implementation attempts and 3 identical-failure retries. Hitting either means changing strategy.
- Loop: at most 3 consecutive unproductive iterations. An iteration is unproductive if it changed code but produced no newly verified criterion and no new diagnostic information. After 3, write `docs/LOOP_BLOCKER.md`.

## Evidence rule
A criterion is PASS only with observable evidence: command output, an API response, a
persisted row, or a Playwright observation. "The code looks correct" is not evidence, and
neither is an LLM's answer. Arbitrary sleeps are never verification; every wait is a
bounded poll on a real condition.

A gate that fails intermittently is itself a defect, whichever side is at fault. Diagnose
it with evidence (timestamps, histograms), then fix whichever side is wrong:
- **The product.** Example: the N+1 incident list, finding F16.
- **The harness.** Examples: the /overview navigation hop that aborted prefetches, and the
  B check passing on an empty snapshot (F18).
- **The statistic.** API responsiveness is judged by p95 < 1 s over every flow call, plus
  no single call over 3 s (F15). A lone slow poll delays one refresh; it does not freeze
  the screen.

Record each such change in `.loop/findings.json`; never loosen a gate silently.

## Stop
**Success** requires all of the following:
- every required criterion is PASS;
- `scripts/verify.py --full` passes;
- `scripts/verify_demo_flow.py --replay` passes (end to end, plus a second fresh replay);
- `scripts/verify_ui_flow.py` passes.

Then write `docs/LOOP_COMPLETION_REPORT.md`, covering the final architecture, verified
features, commands, non-blocking limitations, demo instructions and the fallback procedure.

**Blocked** means a missing secret, a missing external service, an environment
incompatibility, a corrupted dependency or a required credential. In that case write
`docs/LOOP_BLOCKER.md`, covering the exact blocker, evidence, what still works, the
smallest human action needed and the command to resume.

## Never
- Push, merge, or add commit trailers.
- Delete production data. Reset is demo-mode only and never deletes rows (criterion U).
- Retry blindly, or rewrite working code.
- Count an LLM output as verification.
- Fake browser behaviour, or use frontend timers as progress.
- Work on two unrelated objectives at once.

## Resuming after an interruption
Run the `pulsetwin-loop` skill. It reads this file, the ledger, `.loop/state.json`, the
last history lines, and recent git state, then continues the recorded
`state.json.current` objective instead of re-auditing from zero.
