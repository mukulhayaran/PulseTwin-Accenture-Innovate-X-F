---
name: pulsetwin-loop
description: Run or resume the PulseTwin engineering loop (LOOP.md) -- read the goal and state, pick the highest-priority failing acceptance criterion, implement it as the maker, verify it with the independent verifier subagent, update the ledger, and continue until every required criterion passes or a genuine blocker is reached. Use when asked to "run the loop", "continue the loop", "resume", or to finish PulseTwin for the Grand Finale.
---

# PulseTwin engineering loop controller

The loop's contract is `LOOP.md`. This skill is the procedure.

## 1. Resume (never restart from zero)
Read `LOOP.md`, `docs/ENGINEERING_LOOP_STATUS.md`, `.loop/state.json`,
`.loop/findings.json`, the last 20 lines of `.loop/history.jsonl`, `git status --short`
and `git log --oneline -5`. Establish:
- the last verified criterion;
- the current unfinished objective (`state.json.current`);
- the last failure and its attempt count.

## 2. Establish the stack
Make sure the Docker stack is up (`docker compose -f docker-compose.prod.yml ps`) and the
backend is healthy (`curl -s localhost:7860/api/health`). If it is down, run `docker compose
-f docker-compose.prod.yml up -d`. A stack that cannot come up is a blocker (LOOP.md §Blocked).

## 3. Select ONE objective
Take the failing or unknown criteria from `.loop/goal.json` / `state.json` and rank them by
LOOP.md §Priority. Pick exactly one. Write it into `state.json.current` with the expected
observable result BEFORE editing code.

## 4. Maker
Implement it yourself following `.claude/agents/maker.md`, or delegate to the `maker`
subagent. Verify narrowly first, then at runtime.

## 5. Independent verifier
Launch the `verifier` subagent with: the goal, the criterion id and text, the changed
files, the maker's hand-off block, and the commands available. Do not paste your
conclusions as facts; let it re-derive them.
- **PASS** → go to 6.
- **FAIL** → record the failure in `state.json.current.failures`: signature, command, raw
  output (trimmed), hypothesis, fix tried. Diagnose; do not retry the same fix. After 3
  identical failures change strategy; after 5 attempts on one task stop and write
  `docs/LOOP_BLOCKER.md`.
- **INCONCLUSIVE** → make the check observable (improve the verification script), then
  re-verify.

## 6. Record
- Set the criterion to PASS in `state.json` with its evidence and the verifier's verdict.
- Append one line to `.loop/history.jsonl`.
- Rewrite `docs/ENGINEERING_LOOP_STATUS.md` compactly: objective, result, files, verification,
  remaining problems, next task. Carry no raw logs forward.
- If the user allows commits, make one coherent local commit (plain message, no trailers,
  NEVER push).

## 7. Continue or stop
Go back to 3 while a required criterion is not PASS. Stop successfully only when every
required criterion is PASS AND `scripts/verify.py --full`, `scripts/verify_demo_flow.py
--replay` and `scripts/verify_ui_flow.py` all exit 0. Then write
`docs/LOOP_COMPLETION_REPORT.md`. After 3 consecutive unproductive iterations, write
`docs/LOOP_BLOCKER.md` instead of continuing.
