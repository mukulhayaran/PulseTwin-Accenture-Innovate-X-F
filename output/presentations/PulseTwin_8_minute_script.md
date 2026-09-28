# PulseTwin 8-minute presentation script

## Run of show

| Time | Speaker | Slides | Purpose |
|---|---|---|---|
| 0:00-2:10 | Mukul | 1-3 | Stakes, hypothetical failure and the visibility gap |
| 2:10-5:50 | Rishabh | 4-9 | Present the business solution, agentic workflow and Accenture opportunity |
| 5:50-7:45 | Prashant | 10 and live application | Demonstrate the vehicle-to-decision flow |
| 7:45-8:00 | Team | 11 | Close |

## Mukul handoff into your section

> So the problem is not simply that factories have no data. The problem is that the evidence remains separated until it is too late to act. Rishabh will now continue this same vehicle journey and show how PulseTwin turns those disconnected signals into an early, explainable decision.

## Your 3-4 minute script

### Slide 4 - From plant problem to business solution | 2:10-2:40

Let us continue Mukul's hypothetical case. The vehicle's torque pattern is drifting, a manual check finished unusually quickly and a queue is forming. Alone, these signals may look minor. Together, they could become a quality escape, an unplanned stoppage or a costly recall.

PulseTwin connects this fragmented evidence while the business can still act. It brings vehicle quality, production flow and operational risk into one decision system, helping the plant protect safety, throughput and customer trust at the same time.

### Slide 5 - One operational picture | 2:40-3:05

The Supervisor panel gives the shift team a live view of the plant: where a vehicle is, which station needs attention and why the system raised a concern.

Instead of comparing separate reports after the event, teams share the same picture during production. This reduces investigation time and focuses attention where delay or quality loss is most likely.

### Slide 6 - Earlier and explainable decisions | 3:05-3:35

For every at-risk vehicle, PulseTwin shows a risk estimate and the business evidence behind it. For the wider line, it identifies the current constraint and shows how it is affecting throughput.

Each role gets the right decision view. A supervisor can decide whether a vehicle needs containment. A manager can identify lost capacity and compare responses. Leadership can connect performance with quality cost, delivery risk and investment priorities.

### Slide 7 - Agentic AI as a digital operations team | 3:35-4:35

This is where the solution becomes agentic. When an alert or operating condition appears, PulseTwin can start an investigation instead of simply adding another notification to a dashboard.

Its specialist agents work like a coordinated digital operations team. They examine reliability, production flow and vehicle risk, then combine the evidence into one finding rather than sending users across different systems.

Before recommending action, the agents test alternatives in the digital twin, such as a buffer change, maintenance window, cycle-time adjustment or environmental control. The system explains which option appears beneficial, uncertain or unsuitable.

This makes the AI accountable. It shows the evidence, records the investigation and sends a verified proposal to the responsible person. The person remains in control of the decision.

### Slide 8 - From recommendation to measurable value | 4:35-5:20

The agents support several business moments. Starvation, paint environment and tool degradation agents monitor recurring risks. What-if compares improvement choices. Shift Intelligence explains performance loss, while Handover carries open actions into the next shift.

After approval, an action is applied only to the simulated twin. PulseTwin then compares the expected result with what actually happened in that environment. This creates a reusable record of which actions worked, failed or remained uncertain. Over time, teams can make faster decisions based on their own operational experience.

Across the panels, Supervisor manages the immediate response, Manager reviews investigations and outcomes, Ops monitors system health, and Leadership sees business impact.

### Slide 9 - Accenture opportunity and demo handoff | 5:20-5:50

For Accenture, PulseTwin can become a reusable industrial AI offering. Accenture can adapt it for automotive, manufacturing, energy or logistics clients, integrate it with their systems and provide ongoing model, simulation and operations services.

That creates value through transformation projects, platform implementation, industry-specific agent packages and managed services. Clients gain lower quality cost, reduced downtime and faster decisions, while Accenture gains a scalable solution that can grow from one production line to multiple plants.

Prashant will now demonstrate this journey live, from an operational signal to an agent investigation, tested recommendation and human decision.

## Agentic AI implemented in the application

Use this section for questions after the presentation. It describes the implemented application rather than the shorter spoken story.

### Shared agent workflow

1. An alert, watch rule or user request creates an investigation job.
2. The job gathers current evidence through role-controlled tools.
3. It forms bounded hypotheses and, where applicable, runs a fresh simulation experiment.
4. The experiment runs baseline and intervention scenarios on an isolated copy of the live twin with matching seeds.
5. A deterministic comparison reports the paired change, uncertainty interval and a verdict of **improved**, **no improvement** or **too uncertain**.
6. The synthesis separates measured facts, inferred relationships and simulated projections.
7. A verifier checks supported claims before the finding becomes a proposal.
8. A person approves or rejects the proposal.
9. An approved action is applied only to the simulated twin. The system monitors the result and compares expected performance with the observed outcome.
10. The structured outcome becomes operational memory that later investigations can retrieve.

The visible lifecycle can move through **investigating, forming hypotheses, simulating, comparing, verifying, awaiting approval, applied, monitoring** and **outcome evaluated**. It can also finish as rejected, no action warranted, verification failed or run failed.

### Current agents and their responsibilities

| Agent | Trigger | What it investigates or produces |
|---|---|---|
| Incident Agent | On demand or a new risk alert | Coordinates Reliability, Production and Risk specialists, runs one fresh experiment, verifies the evidence and creates a combined finding. |
| What-if Agent | On demand | Compares more than one supported intervention using fresh paired simulations at the live constraint. The precomputed grid is used only as a labelled fallback. |
| Shift Intelligence Agent | On demand | Explains the largest sources of loss and links experiments, decisions and expected-versus-observed outcomes. |
| Handover Agent | On demand | Summarises changes, applied and pending actions, outcomes that are still maturing, and the conditions the next shift should watch. |
| Starvation Agent | Automatically when its watch fires | Separates local station failure from missing upstream feed, then tests feed-buffer, repair and cycle-time options. It proposes a change only when the simulated improvement is clear. |
| Paint Environment Agent | Automatically when its watch fires | Checks humidity evidence and paint guidance, then simulates the expected quality effect of a supported HVAC intervention. Unsupported actions are clearly labelled. |
| Tool Degradation Agent | Automatically when its watch fires | Checks sensor health before diagnosing wear, then compares maintenance now, a planned stop and load shifting. It does not claim remaining useful life because no RUL model is implemented. |

The conversational Copilot and these agents have different roles. Copilot answers a user's question in a conversation. An agent is a persisted, auditable job with a goal, tool calls, lifecycle, evidence and an outcome.

### What the experiment evidence means

- **Fresh simulation** means the current twin is copied and evaluated without changing the live simulation.
- **Paired comparison** means baseline and intervention use the same seeds, which reduces random differences between them.
- The comparison can include throughput, work in progress, blocked time, starved time, downtime, expected defects, alerts and the bottleneck.
- The UI shows baseline values, intervention changes, a 95 percent interval, seed count, runtime and simulator version.
- Simulation results are labelled **inferred projections**. Results observed after an approved action in the simulated twin are recorded separately as outcomes.

### Outcome evaluation and operational memory

After an approved action, a deterministic monitoring window compares pre-action and post-action events. The outcome is classified as **successful, partially successful, no material effect, harmful, inconclusive** or **cancelled**. The system also records projection error between the simulation expectation and the observed result.

Later agents can retrieve outcomes and structurally similar incidents using plant, station, incident type and intervention. This is practical operational memory; it does not retrain the language model or allow the system to modify itself.

### Panel mapping

| Panel | Agentic role in the story |
|---|---|
| Supervisor | Shows the live line, vehicle risk, evidence, containment and immediate alert response. |
| Manager | Hosts on-demand agents, investigation lifecycle, experiment comparisons, proposal approval and the outcome ledger. |
| Ops | Shows sensor, ingestion and model health together with the audit trail. |
| Leadership | Connects model and operational performance to business value and investment decisions. |

### Accurate claims for the presentation

- Say that the LLM selects tools and interprets evidence, while deterministic services calculate simulation results, verdicts and monitored outcomes.
- Say the incident specialists execute in sequence. Do not describe them as parallel agents.
- Say the proposal affects the simulated twin after human approval. Do not claim that PulseTwin controls real factory equipment.
- Say the system reuses structured outcomes. Do not claim autonomous model retraining or self-learning weights.
- Say a simulated result is an inferred projection. Keep it separate from a measured post-action outcome.
- If an intervention is unsupported, the application labels the limitation rather than inventing a result.

## Prashant's demo checklist

1. Open the Supervisor panel and start or resume Plant A.
2. Show vehicle movement, station states and the current constraint.
3. Open an at-risk vehicle and show prediction time, calibrated risk and contributing factors.
4. Show containment or related in-flight vehicles.
5. Open the Manager view and show an agent lifecycle plus one fresh experiment comparison.
6. Open the Proposal Inbox and show the human approval boundary.
7. If a completed action is available, show expected-versus-observed performance in the Outcome Ledger.
8. Show the run mode honestly as AI or Deterministic.
9. End by confirming that approved actions affect only the simulated twin.

Recovery line if the live stream is slow:

> The live stream is still advancing, so I will use this completed vehicle to show the same persisted prediction, explanation and inspection outcome.

## Closing line

> PulseTwin helps teams find risk earlier, understand why it matters and make a verified decision before the problem leaves the plant. Thank you.

## Delivery notes

- Treat the accident scenario as hypothetical. Do not claim it caused the Honda recall referenced in the deck.
- Say calibrated defect risk, rather than certainty of failure.
- Alert correctness means precision. Recall means the share of actual defects detected.
- If the agent badge says Deterministic, do not describe that run as an LLM agent run.
- Do not say PulseTwin controls factory equipment. It advises people and applies approved changes only to the simulated twin.
- Operational memory means stored incidents and measured outcomes; it does not mean autonomous retraining.
