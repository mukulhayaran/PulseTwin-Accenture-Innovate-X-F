# Finals Demo Script — ~8.5 Minutes

Supersedes `docs/DEMO_SCRIPT.md` for the finals round; that script still covers the
Round-2 walkthrough (`/supervisor` only) for a judge who missed it. This one assumes they
saw it and spends the time on what changed since.

**Was 7 minutes; ACT 2B (below) adds 90s for the multi-agent orchestration story
(ADR-045), added because a mentor review specifically named multi-agent orchestration as
missing from the prior walkthrough.** If the slot is hard-capped at 7 minutes, cut ACT 4
step 3 (the `SCALING_COST_MODEL.md` table read — it repeats what the CLOSE already shows)
rather than shortening ACT 2B itself.

**Pre-flight, before judges sit down:**

```bash
make build-artifact           # ensure artifacts/pulsetwin.db + stream_<plant>.jsonl are current
docker compose -f docker-compose.prod.yml --profile mqtt-demo up --build -d
PULSETWIN_REQUIRE_FULL_DEMO=1 python scripts/smoke_demo.py
```

Have a second terminal ready with `mosquitto_pub` for Act 2, and the backend process ID
handy for the kill/restart in the same act.

> **Rehearse this once before the finals.** The full stack and MQTT projection were
> executed on 2026-09-22. Keep the smoke command in pre-flight so a local Docker or port
> problem is found before the walkthrough.

The full smoke calls `/api/demo/readiness`; it fails unless database, Redis, MQTT,
publisher, ingestion projection, online scoring, backend, and frontend are all ready.

---

## ACT 1 — "It works" (2 min): brief recap for any new judges

- `/supervisor`, plant A, speed 10×.
- Let an alert fire on a flagged unit. Open it: risk drivers in plain language, the
  containment sweep listing every other in-flight unit sharing its fingerprint.
- **Line:** "You saw this in Round 2. Now let me show you what makes it production-ready."

## ACT 2 — "It breaks gracefully" (2 min): the finals differentiator

1. **Inject a sensor dropout** at S8, live, via the publisher's control channel — not a
   scripted UI toggle, an actual MQTT message the ingestion path has to detect on its own:
   ```bash
   mosquitto_pub -h localhost -p 1883 -t "pulsetwin/plant_a/control/dropout" \
     -m '{"station_id": 8, "signal": "vibration_mm_s", "duration_s": 120}'
   ```
2. Within ~10 seconds, `SensorDropoutAlert.tsx` lights up on `/supervisor`: the S8 tile
   gets a pulsing amber border, and the line-map banner reads "1 sensor dropout active —
   S8 vibration_mm_s."
3. Point at a prediction touching S8: note the widened confidence interval and "using
   last known value" language — the model did not silently keep pretending it had good
   data.
4. **Kill the backend process** (`Ctrl+C` on the `uvicorn`/`docker compose` backend
   service, or `docker compose -f docker-compose.prod.yml stop backend`).
5. `OfflineBanner.tsx` appears within seconds: "⚠ TWIN OFFLINE — Last known state from
   HH:MM:SS (Ns ago), retrying in 15s…" — on every open view, not just `/supervisor`.
6. **Restart the backend.** The banner's auto-retry (or the [Retry Now] button) reconnects
   the frontend without a manual page refresh.
7. **Line:** "In a factory, things fail. The question is whether your system fails
   safely — and whether it TELLS you it's degraded instead of quietly guessing."

## ACT 2B — "It's genuinely multi-agent" (90s): the second finals differentiator

1. On `/copilot` (plant A), click **Investigate this incident**.
2. Point at the agent roster that lights up above the phase trail, in sequence:
   Reliability → Production → Risk, each with its OWN tool calls distinct from the
   others — not one trail pretending to be three.
3. **Line:** "These are three separately-bounded investigators, each with its own tool
   access and its own request budget — not one model asked to role-play three times."
4. When the run finishes, open the tool ledger and point at the persisted `agent` tag on
   a few calls (e.g. `call_0004 — production`): "This isn't decoration. It's what's
   stored — every number this run cites still traces to exactly which specialist
   gathered it, in the same database row the single-agent path has always used."
5. Point at the finding's claims: "A fourth model — the synthesizer — reconciled all
   three specialists' evidence into this one answer. It has zero tools of its own; every
   citation here still goes through the identical deterministic verifier
   (`genai/verifier.py`) the single-agent path always has. Multi-agent didn't loosen the
   guarantee — it's the same gate, checking a wider investigation."
6. **Line:** "This is what 'agentic AI' means here — not a chatbot wrapper, bounded
   agents with disjoint authority, reconciled and re-checked by code that trusts none of
   them." (ADR-045 has the full design trade-off if a judge wants the written version.)

## ACT 3 — "It stays honest" (1.5 min)

1. Switch to `/ops`. Point at the "Viewing as" role selector — set it to `system`.
2. `ModelHealthPanel.tsx`: champion age, live PR-AUC vs training PR-AUC, calibration
   deviation.
3. `DriftMonitor.tsx`: current PSI status per feature. If nothing has drifted in this
   run, say so plainly — "the model isn't degraded right now, and this panel is how we'd
   know if it were, not just hope."
4. Say why there is no retrain button: the data is a static seed-42 simulation, so a
   retrain would reproduce this exact model (ADR-053). Drift here is information for a
   person, not a trigger.

5. Drop the role selector to `manager` on camera — watch `ModelHealthPanel`,
   `DriftMonitor` and `AuditLogViewer` all replace themselves with
   "Requires leadership/system role" via `RoleGate.tsx`. The backend enforces this
   regardless of what renders (`serve/middleware/auth.py::require_role`) — this is not a
   UI-only trick.
6. **Line:** "The model doesn't just predict. It predicts when it CAN'T predict — and
   only the right role can act on that."

## ACT 4 — "It scales" (1.5 min)

1. Switch plant selector to Plant B on any view — full reload, same UI, same code,
   different station count and variant mix. **Line:** "Zero pipeline code changed for
   this."
2. Show `infrastructure/kubernetes/hpa.yaml` and `network-policy.yaml` for 15 seconds:
   "2-to-6 replica autoscaling, and a network policy that makes it structurally
   impossible for the backend to reach the factory floor's MQTT broker — even if the
   backend were fully compromised."
3. Show `docs/SCALING_COST_MODEL.md`'s headline table: "1 plant to 20 plants, and we
   price it against our WORSE-performing simulated plant, not our better one, on purpose."
4. Back on `/ops`, open `AuditLogViewer.tsx`: "Every mutating action — a promoted model,
   an acknowledged alert, a what-if run — timestamped, attributed by role, queryable.
   IATF 16949 asks for exactly this kind of traceability."

## CLOSE

`docs/SCALING_COST_MODEL.md`'s headline table plus `docs/BUSINESS_PROPOSAL.md` §5's
roadmap slide. Closing line: **"Round 2 proved it works. This round proves it survives
contact with a real factory — sensors that die, models that drift, and an IT team that
has to approve the network diagram before any of it touches the floor."**
