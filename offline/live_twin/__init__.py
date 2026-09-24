"""The TRUE live digital twin: a long-running discrete-event simulation of the line.

This is a SEPARATE DEPLOYABLE, like `ingestion/`, `mlops/` and `genai/`. `serve/` never
imports it -- it cannot, because the simulator reaches `offline/config_schema.py`, which
imports PyYAML, and PyYAML is not on the import-wall allowlist (CLAUDE.md section 2).
`serve/simulation_manager.py` launches it with `subprocess.Popen` exactly as
`serve/routers/admin.py` launches retraining and `serve/routers/triage.py` launches the
agent (ADR-036), and the two communicate only through the ops database.

HOW THIS DIFFERS FROM WHAT WAS HERE BEFORE. `serve/live_simulation.py` replays the
committed JSONL stream and, on an incident, calls `_raise_risk()` -- literally
`calibrated_risk += 0.5 * severity`. That is a useful scripted demo and it is KEPT, under
the honest name "scenario injection". It is not a simulation: the risk number is written
by the incident, not computed from it.

Here, an incident changes a PHYSICAL quantity and nothing else:

    incident -> station/tool/ambient state -> sensor readings -> features
             -> the real trained scorer -> Platt calibration -> operating threshold
             -> alert -> containment -> inspection outcome

There is no path in this package that writes a risk value. `engine.py` obtains every risk
from `serve.scorer.Scorer.predict`, and `tests/test_live_simulation_engine.py` asserts
the source contains no assignment to a risk field outside that call.
"""
