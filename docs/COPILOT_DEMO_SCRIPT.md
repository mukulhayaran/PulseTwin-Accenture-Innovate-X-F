# AI Copilot Demo Script — 6 Minutes

For the AI/Agentic-AI round. `docs/FINALS_DEMO_SCRIPT.md` is the production-readiness
walkthrough; this one spends its whole budget on the AI story and assumes the judges have
not seen either.

The arc, and it is the whole point:

> **PREDICT → DETECT → INVESTIGATE → EXPLAIN → SIMULATE → RECOMMEND → HUMAN DECIDES**

---

## Pre-flight

```bash
# 1. The backend. SQLite path is fine for this demo -- no Docker needed.
make dev-backend                    # uvicorn on :7860

# 2. The frontend.
make dev-frontend                   # next dev on :3000

# 3. OPTIONAL, and the only line that changes what the judges see:
export GROQ_API_KEY=gsk_...         # agent mode. Without it, deterministic mode.
```

**Check `/api/triage/availability` before you start.** It reports `mode: "agent"` or
`mode: "deterministic"`, and the copilot header badges whichever is live. Both paths work;
they differ in what you claim on stage. Say which one is running — the badge is on screen
and a judge will read it.

> **Rehearse once.** As of 2026-09-20 the agent path has never talked to a real provider
> on this machine, so if you are demoing with a key, run one question end to end before
> the judges sit down. The deterministic path is fully tested and is the safe default.

---

## ACT 1 — "It already predicts" (60s)

- `/supervisor`, plant A, speed 10×. Let the line run for twenty seconds.
- An alert fires. Open the unit: plain-language root causes, confidence interval,
  containment sweep listing every other in-flight vehicle sharing its fingerprint.
- **Line:** *"That is the predictive half, and it was built first. Everything after this
  is the AI layer explaining it — it never replaces it."*

## ACT 2 — "It triages without being asked" (45s)

- Scroll to **Alert triage** on the same page.
- **Line:** *"These are not the alerts that fired. They are every vehicle the cost model
  says is worth inspecting — about 1,400 of them — ranked into CRITICAL, INVESTIGATE,
  MONITOR and LOW_CONFIDENCE by fixed rules, not by a language model."*
- Hover a class badge: the written reasons appear.
- **The honest bit, and do not skip it.** Point at the amber diagnostics note:
  *"On this plant the model's calibration has plateaued — every fired alert shares one
  risk value, so severity cannot rank them. The system says so and ranks on lead time and
  peer count instead. We publish that rather than tuning it away."*
- **Line:** *"LOW_CONFIDENCE never hides a row. It means the inputs behind the score are
  reconstructed, not that the vehicle is fine."*

## ACT 3 — "Ask it" (90s) — **the centre of the demo**

- Open **/copilot** (or the dock, bottom right of any view).
- Click the suggested question **"What is causing the current production bottleneck?"**
- **Watch the phase trail fill:** *Starting investigation → Checking production state →
  Reading station performance → Examining risk signals → Composing the finding →
  Verifying every number.*
- **Line:** *"That trail is tool activity, not the model's thinking. We never show chain
  of thought, and there is a test asserting the model's own text can never reach that
  panel."*
- When it lands, read the verdict strip aloud: **`verified · 8/8 claims verified`**.
- Expand one claim. Point at `call_0003/data/current_station`.
- **Line, and this is the sentence that matters:** *"Every number is a citation into the
  exact tool result the model was shown. A deterministic verifier re-checks all of them
  with no model in the loop. If a number cannot be traced, the claim is **removed** — not
  flagged. If too many fail, the whole answer is discarded and you see nothing."*

## ACT 4 — "Investigate this" (60s)

- Click **Investigate incident**.
- **Line:** *"Same agent, a different plan: infrastructure before behaviour. A constraint
  that looks like it moved is sometimes a sensor that went dark, so it checks dropouts and
  data quality before it blames the line."*
- Point at **Could not determine**. *"It reports what it could not read instead of
  estimating it. A manager running this cannot see leadership's economics, so the agent
  says so rather than guessing a number."*

## ACT 5 — "What if?" (60s)

- Click **Run what-if for S<n>** (the button takes the station from the live constraint —
  nothing is typed, and nothing is parsed out of prose).
- Baseline vs projected throughput, both cited to the simulator.
- **Then break it deliberately.** Ask: *"What happens if Station 19 becomes unavailable?"*
- **Line:** *"The grid covers buffer and cycle-time changes at seeded stations. Taking a
  station offline is not in it — so it says the simulation is unavailable and refuses to
  approximate it with a scenario that is in the grid. An AI that quietly substitutes a
  different question is worse than one that says no."*

## ACT 6 — "Hand it over" (45s)

- Click **Shift handover**.
- Four sections: what changed, the open constraint, what the next shift must watch, what
  was already ruled out.
- **Line:** *"Every figure in that handover is cited. The next operator can ask it
  follow-up questions in the same thread — and if they do, citations do not carry across
  turns. The agent re-reads the line rather than repeating a number from twenty minutes
  ago as if it were current."*

## ACT 7 — "Why you can deploy this" (40s)

- Open the **AI trust** panel (right-hand column of `/copilot`).
- Model version, drift status, fallback status, data quality, measured vs inferred counts,
  evidence count, audit id.
- **Closing line:** *"The agent is read-only. Its entire vocabulary is eighteen functions
  and the one that is not a GET posts to a simulator. It cannot promote a model, change a
  config or touch the line. Every run is audited with the role that started it and the
  role its tools acted as — and a manager cannot start a run that reads more than the
  manager can. The AI supports the operator. It does not control the factory."*

---

## If something breaks

| Symptom | What to say, and what to do |
|---|---|
| Badge reads `Deterministic` | Say so: *"No provider key is loaded, so a rule-based path is answering through the same tools and the same verifier."* Everything in Acts 3–6 still works. |
| A run comes back `rejected` | **This is the feature.** *"The verifier could not trace every number, so it discarded the whole answer rather than showing you a plausible one."* Show the evidence ledger below it. |
| Badge reads `degraded L2` | *"The provider rejected part of our request format, so it stepped down one rung at a time. The finding still passed the identical verifier, and we show you that it degraded rather than hiding it."* |
| A turn 409s | Only one turn runs per conversation at a time. Wait for the trail to finish, or hit **New conversation**. |
| Backend unreachable | The offline banner appears with the last known state retained. Restart `make dev-backend`. |

## The three claims to leave them with

1. **The model never produces a number.** It orchestrates tools and explains their
   results; every figure is re-checked by a deterministic verifier against the exact bytes
   the model was shown, and unverifiable claims are removed rather than flagged.
2. **The egress surface is an allowlist, and widening it edits a test.** Eighteen paths,
   none mutating, no per-unit journeys, no operator data, no audit log.
3. **It works with the model switched off.** Every named action has a rule-based plan that
   passes the same gate — which is also the honest baseline for asking whether the
   language model added anything at all.
