# Does the proactive watch beat simple threshold alerting?

> Synthetic twin data throughout, both plants, seeds 42–44, git `0daa04d` (stamped in each
> JSON's `git_sha`). These are not claims about a real line (`docs/CLAIMS.md`). Every
> number comes from `artifacts/benchmarks/agent_eval_plant_{a,b}.json`. To regenerate:
> `OMP_NUM_THREADS=1 python -m offline.benchmarks.agent_eval --plant plant_a --seeds 3`
> (6–18 min depending on host load). The results are deterministic: two runs at the same
> code are byte-identical.

## Short answer

**On plant A, yes, clearly. On plant B, mostly, with one fault class it loses.**
- **Plant A:** the watch catches **13/13** faults that cost real output against 4/13 for
  thresholds. Its root cause is right in 100%, it raises about 3 false alarms per shift
  against 320, and its placebo rate is 7% against 41%.
- **Plant B:** it catches **8/11** against thresholds' **9/11**. Thresholds catch a
  *partial* congestion (S21, 84–86% of output lost) within about a minute, and the watch
  misses all three. The watch still wins on false alarms (5.5 against 190 per shift),
  placebo (11% against 33%) and root cause (100% against 67%).
- The watch is still **not faster** than raw thresholds on the faults both catch.

## How we got here: three rounds, each re-measured

| Round | Change | Plant A impactful caught |
|---|---|---|
| 1 (`f0aa70c`) | the watch as built in ADR-051 | **0/8** |
| 2 (`83bfeef`) | + silence detection, ticked by a clock (ADR-057) | 6/8 |
| 3 (`0daa04d`) | + real breakdown physics and PLC down-state events (ADR-059) | **13/13** |

The impactful-run count grows between rounds because round 3 made breakdown severity
physical. Breakdowns now cost 11% (plant A) and 14% (plant B) of output, where before
it was 3%. The denominators differ, so compare within a row, not across rows.

**Tried in round 3 and reverted:** judging starvation on *pace* (cycle + time held
blocked) instead of cycle, to catch plant B's partial congestion. It raised the watch's
false alarms from 3 to **59 per shift** and still missed S21 (ADR-059).

## Method

Faults are injected into the **physical twin** (`offline/live_twin`). They are not
replayed from the committed stream, because that is the stream the watch thresholds were
profiled on, and grading the watch there would be grading it on its own training data.
Each seed warms one engine and forks it per scenario: a clean run, and 9 faults. The
faults are torque at both Mode C stations, zone humidity, and a breakdown and a
congestion at three stations picked by quartile from the config. Each run has 2 h clean,
then the onset, then a 3 h horizon, so 27 fault runs per plant. Every station event and
PLC state event goes, identically, to:

| System | What it is |
|---|---|
| **watch** | `serve/watch.py`: 8-event window, latch, 1 h cooldown, silence ticked every 60 s of twin time |
| **threshold** | the same thresholds on every single event, every crossing an alarm |
| **deadband** | threshold + the watch's cooldown, which isolates what the window buys |

Two controls that a naive eval would skip:
- **Placebo recall.** The same alerter is scored against the fault on the *same-seed clean
  run*, where there is no fault. Detection at or below this rate is chance.
- **Impact.** Units lost vs the same-seed clean fork. A fault costing under 5% of output is
  counted as minor, and recall is also reported on impactful faults only.

## Results (`0daa04d`)

| | A: watch | A: threshold | A: deadband | B: watch | B: threshold | B: deadband |
|---|---|---|---|---|---|---|
| Faults detected (of 27) | **20** | 11 | 11 | 16 | 18 | 17 |
| **Placebo recall** | **7%** | 41% | 41% | **11%** | 33% | 26% |
| **Impactful faults caught** | **13/13** | 4/13 | 4/13 | 8/11 | **9/11** | **9/11** |
| Median detection delay | 13.2 min | 12.6 min | 36.2 min | 19.9 min | 22.8 min | 21.9 min |
| **False alarms per 8 h shift** | **3.0** | 320 | 54 | **5.5** | 190 | 38 |
| Root cause from the alert's evidence | **100%** | 64% | 64% | **100%** | 67% | 65% |
| Alert at the exact faulted station | 85% | 64% | – | 100% | 61% | – |

By fault (detected of runs, placebo in brackets, median delay):

| Fault | A loss | A: watch | A: threshold | B loss | B: watch | B: threshold |
|---|---|---|---|---|---|---|
| station_breakdown | 11.0% | **8/9 (0), 12.7 min, cause 8/8** | 4/9 (3), 107 min, cause 0/4 | 13.9% | **9/9 (1), 19.5 min, cause 9/9** | 6/9 (3), 53 min, cause 0/6 |
| buffer_congestion | 77.9% | **6/9 (0), 13.4 min** | 1/9 (3) | 0.0%* | 1/9 (1), 94 min | **6/9 (3), 1.2 min** |
| torque_degradation | 0% | 6/6 (2), 19.5 min | 6/6 (5), 4.4 min | 0% | 6/6 (1), 27.1 min | 6/6 (3), 21.2 min |
| high_humidity | 0% | 0/3 | 0/3 | 0% | 0/3 | 0/3 |

\* Median over the three stations. Plant B's S21 congestion loses 84–86%, and S11/S31 lose
nothing.

## What this says, plainly

1. **Most of the threshold baseline's detections are chance.** Placebo is 41% (A) and 33%
   (B). It "detects" torque faults on the clean line 5 times in 6 on plant A. Its
   breakdown diagnoses are all wrong (0/10). At 190–320 false alarms a shift, an operator
   stops reading it.
2. **Breakdowns are now caught and correctly diagnosed**: 17/18 across both plants, cause
   right in all 17. A down station completes no units, so its own events never said DOWN,
   and its blocked neighbours read as congestion (0/3 before ADR-059). The twin now emits
   the PLC state change, and the watch uses it.
3. **Deadlock-level congestion is caught by silence** (plant A, 6/6 at about 13 min).
   **Partial congestion is not** (plant B S21, 0/3). A line trickling at 14% never goes
   silent, and a blocked station's work cycle stays under takt. Thresholds catch it in
   about a minute, genuinely: its placebo at S21 is 0. Pace was the obvious fix, and it
   failed (see above). This is the watch's largest remaining gap.
4. **The watch is not faster.** Its 8-event window buys the false-alarm rate. On the faults
   both catch it is about as fast (A) or slower on torque (both plants).
5. **Humidity faults are not detected by any system, by design.** The twin's incident
   raises *ambient* humidity, and the paint rule fires only on zone humidity *in excess
   of* ambient. That is correct for its job (zone climate faults): zone humidity sits
   above 55% for 72% of a humid day, so an absolute alarm would never switch off. The
   defect risk from weather (humidity **and** paint dwell) is scored per unit by the
   defect model, which is not what this eval measures.

## Not measured, and why

- **Containment precision/recall, and defect escape vs EOL-only.** Single-station
  incidents are defect-inert in this twin. Mode C needs S3 torque **and** S8 vibration
  together (ADR-055). An escape-rate comparison would compare zeros.
- **The LLM agent's own diagnosis.** One run spends 7–12 provider requests against a
  50/day free quota. What is measured is the deterministic diagnosis the agent is verified
  against.
