# Finals Demo Script — 7 Minutes

Supersedes `docs/DEMO_SCRIPT.md` for the finals round; that script still covers the
Round-2 walkthrough (`/supervisor` only) for a judge who missed it. This one assumes they
saw it and spends all seven minutes on what changed.

**Pre-flight, before judges sit down:**

```bash
make build-artifact           # ensure artifacts/pulsetwin.db + stream_<plant>.jsonl are current
docker compose -f docker-compose.prod.yml up --build -d   # postgres, redis, mqtt, migrate, backend, ingestion
python -m offline.load_to_postgres                        # bridge the artifact into postgres
python -m ingestion.mqtt_publisher --plant-id plant_a --speed 10 &   # start the live feed
cd frontend && npm run dev                                          # or the static export, per docker-compose.yml
```

Have a second terminal ready with `mosquitto_pub` for Act 2, and the backend process ID
handy for the kill/restart in the same act. Kick off one retrain now
(`RetrainingConsole` → **Trigger Retrain**, or `POST /api/admin/trigger-retrain`) so Act 3
has a finished comparison report to show.

> **Rehearse this at least once end to end before the finals.** As of 2026-09-16 the
> production stack has never been started — no Docker daemon was available on the build
> machine — so Act 2's dropout injection and the kill/restart have been verified by unit
> test and code review, not by running them. That is the single highest-risk gap in this
> script; see `docs/STATE.md` → NEXT ACTION.

---

## ACT 1 — "It works" (2 min): brief recap for any new judges

- `/supervisor`, plant A, speed 10×.
- Let an alert fire on a flagged unit. Open it: root causes in plain language, the
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

## ACT 3 — "It stays honest" (1.5 min)

1. Switch to `/ops`. Point at the "Viewing as" role selector — set it to `system`.
2. `ModelHealthPanel.tsx`: champion age, live PR-AUC vs training PR-AUC, calibration
   deviation.
3. `DriftMonitor.tsx`: current PSI status per feature. If nothing has drifted in this
   run, say so plainly — "the model isn't degraded right now, and this panel is how we'd
   know if it were, not just hope."
4. `RetrainingConsole.tsx`: show the champion-vs-challenger comparison table and the
   PROMOTE/REJECT recommendation.

   **Do not start a retrain here and wait for it.** It trains a real model and takes
   minutes, not seconds — far longer than the 90 seconds this act gets. Click **Trigger
   Retrain** during pre-flight, before the judges sit down, so the completed comparison
   report is on screen by the time you reach this step. Clicking it live is fine as a
   gesture ("this is the button, it is `system`-role only, and it is in the audit log"),
   as long as you do not stand there watching a spinner.
5. Drop the role selector to `manager` on camera — watch `ModelHealthPanel`,
   `DriftMonitor`, `AuditLogViewer`, and the retrain button all replace themselves with
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
