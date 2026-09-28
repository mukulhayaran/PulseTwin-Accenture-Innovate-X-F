# Copilot panel

## What this panel is

The Copilot is a multi-turn, read-only investigation workspace. A user can ask about
production, defects, bottlenecks, data quality, and supported simulations in plain
language.

It is designed around one rule: **an answer is not shown until its numbers are checked.**
The AI is used to investigate and explain; deterministic API data and a separate verifier
remain the source of truth.

The full page pins itself to the **Manager** role, which is the minimum role allowed to
start a run.

## Page header

- **Plant picker:** changes the investigation to Plant A or Plant B.
- **New conversation:** clears the active thread.
- **Read-only badge:** reminds the user that the agent cannot control equipment or deploy
  a model.
- **Connection status:** the backend is checked every ten seconds.

Switching plants begins a new conversation. A thread cannot carry verified Plant A facts
into a Plant B answer.

## Starting an investigation

### Free-text question

The user can type up to 500 characters and press Enter or **Ask**.

Suggested questions are generated from the current bottleneck, for example:

- Why is S19 showing elevated defect risk?
- What is causing the current production bottleneck?
- Which vehicles are potentially affected?
- What changed during the last 30 minutes?
- Which stations need investigation?

The station number comes from live plant data rather than a hardcoded example.

### Named actions

Four buttons send a clear action key to the backend:

| Action | What it does |
|---|---|
| Investigate incident | Checks what changed, rules out sensor/infrastructure problems, then traces affected units. |
| Run what-if for the current constraint | Confirms the station is supported, then runs the precomputed simulation and compares baseline with projected results. If the station is outside the grid, it explains the limitation instead. |
| Shift intelligence | Builds a manager-focused ranking of production loss, risk, and constraint behavior. It attempts to qualify the ranking with model health; on the Manager-only full page, that Leadership-level part is reported as permission-blocked. |
| Shift handover | Produces a concise next-operator handover: what changed, what remains open, what to watch, and what was already tried. |

These buttons send structured intent values, not hidden canned prompts. Every action also
has a deterministic plan, so it still works when no external AI credential is configured.

## What happens while it works

The operator's question appears immediately. The page then polls the run once per second
and shows safe progress labels such as:

1. Starting investigation.
2. Checking production state.
3. Reading station performance.
4. Examining risk signals.
5. Tracing affected units.
6. Running simulation.
7. Costing the options.
8. Composing the finding.
9. Verifying every number.

In Agent mode, tool-call chips can appear as calls start and finish, along with the
investigation round and elapsed time. The Deterministic path normally completes its fixed
sequence before the browser sees most or all of the finished ledger.

The model's draft answer and private reasoning are not streamed. Otherwise a made-up
number could briefly appear and only disappear after verification. Only progress and tool
status are live; the prose arrives after the check.

## How to read the final answer

### Verdict strip

Possible states are:

- **Verified:** all served claims passed.
- **Verified with redactions:** unsupported claims or sentences were removed, while the
  remaining answer stayed within the allowed failure limit.
- **Rejected:** too much of the answer failed or a serious data contract was broken; no
  finding is served.
- **Failed:** the run itself could not complete.

The strip can also show checked claims, runtime, model cost, provider capability
degradation, and how many previously verified values were repeated from older turns.

### Answer sections

- **Summary:** plain-language result.
- **Evidence:** numeric claims with the exact tool-call ID and JSON location that support
  them.
- **Measured/Inferred:** inferred claims are visibly marked and should include a
  confidence interval.
- **Source:** shown for economic claims and other sourced values.
- **Recommendation:** a human-readable next step, never an automatic factory action.
- **Could not determine:** facts blocked by missing data or the caller's permissions.
- **Why not something else?:** expandable alternatives that were considered.
- **Removal note:** number of prose sentences removed because they carried an unsupported
  figure.

Example: a claim might cite call ID `call_0004` and pointer
`/data/0/lost_uph`, show that the value was measured, and recommend inspecting the
upstream buffer. The citation identifies the exact API result used in that turn. It is
displayed as text rather than as a clickable evidence viewer; full tool results remain
available for the audit/verification path.

## The verification rules

For every numeric claim, the verifier checks:

1. The cited tool call really happened in this turn.
2. The JSON pointer reaches a real value.
3. The claimed value matches that value within the rounding implied by the displayed
   number.
4. Provenance agrees with the source.
5. An inferred value includes its confidence range.
6. A dollar amount includes a named source.
7. Claim text/source plus summary and recommendation do not expose individual operator
   identity.
8. A number in summary or recommendation is supported by a verified claim, a value in
   the current tool results, a real station ID, or a previously verified carried value.

If no claim can be verified, more than roughly 30% of ordinary claims fail, or a serious
contract is violated, the whole result is rejected. The raw draft may remain available
for internal audit/debugging, but the browser only receives the post-verification finding.

Current privacy limitation: the prompt forbids operator identity everywhere, but the
deterministic verifier does not scan the **Could not determine** and **Why not something
else?** lists for identity text. Those fields should therefore not be described as having
the same enforced privacy check as claims, summary, and recommendation.

## What information the Copilot can use

The closed registry contains 18 tools, grouped into these capabilities:

- Current and historical bottlenecks.
- Station performance and station configuration.
- Model metrics and model health.
- Recent alerts and deterministic alert triage.
- Unit containment and the association-based evidence chain.
- Shift summary, data quality, and active sensor dropouts.
- Supported what-if stations and the what-if simulation.
- ROI and instrumentation plan where the role permits them.

Seventeen tools are reads. The only POST calls the what-if simulator; it does not change
the plant. There is no tool for acknowledging an alert, editing a configuration, stopping
the line, promoting a model, or retraining.

The repository contains an `EvidenceGraph` visual component, but no current page renders
it. The agent can use the evidence-graph data in an investigation; users should not expect
a visible graph in this tab yet.

## Agent mode and deterministic mode

### Agent mode

When a provider credential exists, an OpenAI-compatible model chooses from the approved
tools and builds a structured finding. If a provider rejects advanced request features,
the client can step down through capability levels. That degraded level is shown, while
the role cap, tool allowlist, citations, and verifier stay unchanged.

### Deterministic mode

Without a credential, fixed rules choose the tools and produce a finding. This is a
supported operating mode, not an error. It goes through the same verifier as the AI path.

If a run has already started in Agent mode and its provider later fails, that turn becomes
failed; it is not silently restarted in Deterministic mode.

## Role behavior

An agent can never act above the user who started it. At Manager role it can read
production, system, what-if, and shift-summary data, but Leadership-only model health,
ROI, and instrumentation endpoints return permission errors. A correct answer places
those missing facts in **Could not determine** rather than estimating them.

Because the full Copilot page always sets Manager role, its AI Trust panel normally says
**requires leadership** for champion model, model age, drift, and fallback. Those facts
can be available to a conversation started from a Leadership/System persona dock, where
the caller has the higher role.

## AI Trust panel

The right-side trust panel shows:

- Agent or Deterministic mode.
- Model ID and age, if the role may read them.
- Drift and fallback state, if permitted.
- Plant data-quality score.
- Data-quality components that cannot be measured.
- Most recent completed turn's verdict.
- Verified claims out of total.
- Measured versus inferred claim count.
- Number of tool calls.
- Run time stamp and audit/run ID.
- Reminder that every recommendation needs human action.

The trust panel updates after a turn completes in the current browser session. Loading an
older conversation does not currently populate it from that thread's latest saved turn.
A near-perfect Data Quality score may simply mean the clean committed simulator artifact
contains no injected faults; it does not prove that a live MQTT feed is healthy. The
panel's tooltip carries that qualification when the backend provides it.

## Conversation history

- The right sidebar lists the 12 most recent threads for the selected plant.
- Each entry shows the title and number of turns.
- Clicking one reloads its saved turns, findings, and verdicts, including rejected or
  failed turns where present.
- The title comes from the user's first question and is shortened, not written by the AI.
- Only one turn can run at a time in a thread.
- **New conversation** starts with an empty thread.

The thread ID lives in the URL hash, such as `/copilot#t=abc123`. This allows reload and
sharing without browser local storage.

Earlier citations cannot be reused as new evidence because call IDs restart each turn.
Previously verified values may be repeated in prose, but a new numeric claim needs fresh
evidence from the current turn.

To keep long conversations bounded, a new turn receives at most four earlier verified
turns and up to eight claim texts from each. This prevents the prompt and cost from
growing without limit.

There is currently no rename or delete function. Reloaded historical turns keep their
answer and verdict, but the detailed tool-call chips are not returned with thread history.

## Floating dock on other panels

The compact dock is intended for a captured Manager, Leadership, or System role, and a
thread can be opened with **Open full page**. A captured Supervisor role sees an access
explanation because triage requires Manager or above. On Operations, changing **Viewing
as** after the dock mounts may leave its visible permission message stale, although the
server still enforces the newly selected role.

## Connectivity and current edge states

- Missing provider credentials select Deterministic mode.
- Start or polling failures appear below the conversation.
- Free-text questions shorter than four trimmed characters are rejected by the backend.
- The what-if action is disabled if the current bottleneck could not be loaded.
- If a saved thread no longer exists, the page returns to a new conversation.
- Failure to load the sidebar currently looks like "No conversations yet."
- If mode availability cannot be loaded, the empty-state wording currently falls back to
  the Deterministic explanation even though the true mode is unknown.
- On this page, **Retry now** updates the displayed last-known time; the normal health
  poll performs the actual reconnection check.
- Conversation turn counts in the sidebar may remain stale until the list reloads.
- Opening a thread with an already-running turn starts polling it, but the composer can
  still look available. A second send is correctly rejected by the backend.
- Historical turns do not return their tool ledger. The phase component can nevertheless
  draw all phases as completed for a finished turn, so that display is not a recovered
  historical execution trace.

## Main code behind this panel

- Full page: `frontend/app/copilot/page.tsx`
- Conversation state: `frontend/components/copilot/CopilotThread.tsx`
- Questions and named actions: `frontend/components/copilot/CopilotComposer.tsx`
- Result renderer: `frontend/components/copilot/FindingView.tsx`
- Progress display: `frontend/components/copilot/PhaseTrail.tsx`
- Trust panel: `frontend/components/shared/AiTrustPanel.tsx`
- API/run management: `serve/routers/triage.py`
- Agent loop and tools: `genai/loop.py` and `genai/tools.py`
- Deterministic fallback: `genai/offline_triage.py`
- Verification: `genai/verifier.py`
