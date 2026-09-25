# Does the proactive watch beat simple threshold alerting?

> Synthetic twin data throughout, plant_a, seeds 42–44, git `f0aa70c`. These are not
> claims about a real line (`docs/CLAIMS.md`). Every number comes from
> `artifacts/benchmarks/agent_eval_plant_a.json`. To regenerate:
> `OMP_NUM_THREADS=1 python -m offline.benchmarks.agent_eval --seeds 3` (≈6 min).

## Short answer

**Partly, and not where it matters most.** The watch's alerts are far more trustworthy
than threshold alarms: 110× fewer false alarms, and detections that are not chance. But
**neither the watch nor the threshold baseline catches the faults that cost real
throughput.** Deadlock-level buffer congestion loses 78–91% of output and is invisible to
every event-driven alerter, because a stopped line emits no events.

## Method

Faults are injected into the **physical twin** (`offline/live_twin`). They are not
replayed from the committed stream, because that is the stream the watch thresholds were
profiled on, and grading the watch there would be grading it on its own training data.
Each seed warms one engine and forks it per scenario: a clean run, and 9 faults. The
faults are torque at both Mode C stations, zone humidity, and a breakdown and a congestion
at three stations picked by quartile from the config. Each run has 2 h clean, then the
onset, then a 3 h horizon, so 27 fault runs in all. Every station event goes, identically, to:

| System | What it is |
|---|---|
| **watch** | `serve/watch.py`: 8-event window, latch, 1 h per-key cooldown |
| **threshold** | the same thresholds on every single event, every crossing an alarm |
| **deadband** | threshold + the watch's cooldown, which isolates what the window buys |

Two controls that a naive eval would skip:
- **Placebo recall.** The same alerter is scored against the fault on the *same-seed clean
  run*, where there is no fault. Detection at or below this rate is chance.
- **Impact.** Units lost vs the same-seed clean fork. A fault costing under 5% of output is
  counted as minor, and recall is also reported on impactful faults only.

## Results

| | watch | threshold | deadband |
|---|---|---|---|
| Faults detected (of 27) | 8 | 10 | 10 |
| **Placebo recall**, "detections" on a clean line | **7%** | 41% | 41% |
| **Impactful faults caught** (loss ≥ 5%) | **0 / 8** | 2 / 8 | 2 / 8 |
| Median detection delay | 17.7 min | 9.9 min | 24.7 min |
| **False alarms per 8 h shift** | **3.2** | 358 | 61 |
| Root cause from the alert's evidence | 75% | 70% | 70% |
| Alert at the exact faulted station | 100% | 70% | 70% |

By fault (watch / threshold, detected of runs, placebo in brackets):

| Fault | Median loss | watch | threshold |
|---|---|---|---|
| torque_degradation | 0% | 6/6 (2), 19.5 min | 6/6 (**5**), 4.4 min |
| station_breakdown | 2.9% | 2/9 (0), 14 min | 3/9 (**3**), 119 min |
| buffer_congestion | **77.9%** | **0/9** | 1/9 (**3**) |
| high_humidity | 0% | 0/3 | 0/3 |

## What this says, plainly

1. **The threshold baseline's extra detections are chance.** It "detects" torque faults
   on the clean line 5 times in 6. Every one of its breakdown detections has a matching
   placebo hit, and they arrive at a median of 119 min. At 358 false alarms a shift, an
   operator ignores it, which is the failure mode the brief warns about.
2. **The watch is slower on the faults both catch** (torque: 19.5 vs 4.4 min). That is the
   8-event window's price for its false-alarm rate. It is a tradeoff, not a win.
3. **Neither catches impactful faults: watch 0/8, threshold 2/8.** One of the threshold's
   two is a placebo-positive, and the other came 178 min late. Congestion at a
   constraining station deadlocks the line. The stations fall silent, and a detector that
   waits for events never fires. **The watch needs a silence detector** (no events from a
   station for N × takt). It is the largest gap this eval found.
4. **The watch's breakdown diagnoses were both wrong** (0/2). A down station emits no
   events while it is down, so its neighbours report `blocked`, and the hypothesis maps
   `blocked` to congestion.
5. **Humidity is invisible by design.** The twin's incident raises *ambient* humidity, and
   the paint rule fires only on zone humidity *in excess of* ambient (ADR-051). Mode A's
   defect physics keys on absolute humidity, though, so weather-driven humidity can cause
   paint defects the watch deliberately ignores. This is a gap, not tuned away here.

## Not measured, and why

- **Containment precision/recall, and defect escape vs EOL-only.** Single-station
  incidents are defect-inert in this twin. Mode C needs S3 torque **and** S8 vibration
  together (ADR-055). An escape-rate comparison would compare zeros.
- **The LLM agent's own diagnosis.** One run spends 7–12 provider requests against a
  50/day free quota. What is measured is the deterministic diagnosis the agent is verified
  against.
