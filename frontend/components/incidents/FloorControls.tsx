"use client";

// The supervisor's two ways to start an investigation (ADR-069):
//   - a demo PRESET: a real live-twin run in a known physical condition (station, severity
//     and onset picked from the plant's own topology by serve/demo_scenarios.py);
//   - an operator REPORT: "something looks wrong at S10" -- opens an incident that the
//     commander investigates like any other. The note is stored as data, never executed,
//     and the API refuses one that identifies an individual operator.

import { useEffect, useRef, useState } from "react";
import { type StationCfg, getDemoScenarios, getSession, openIncident, resetDemo, startDemoScenario } from "@/lib/api";
import {
  Button, Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader,
  DialogTitle, DialogTrigger,
} from "@/components/ui";
import type { IncidentFocus } from "@/components/incidents/IncidentsPanel";

const FOCUS_KIND: Record<string, string> = { delayed_defect: "quality", model_drift: "environment" };

export default function FloorControls({ plant, stations, onRunStarted, onAction, onError }: {
  plant: string;
  stations: StationCfg[];
  onRunStarted: (runId: string, focus: IncidentFocus) => void;
  onAction: (label: string) => void;
  onError: (message: string) => void;
}) {
  const [presets, setPresets] = useState<any[]>([]);
  const [preset, setPreset] = useState("");
  const [reporting, setReporting] = useState(false);
  const [station, setStation] = useState<number | "">("");
  const [kind, setKind] = useState("equipment");
  const [note, setNote] = useState("");

  const [demoMode, setDemoMode] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);

  useEffect(() => {
    getDemoScenarios().then((r) => { setPresets(r.scenarios); setDemoMode(!!r.demo_mode && !!getSession()); }).catch(() => {});
  }, []);

  const resetInFlight = useRef(false);
  async function reset() {
    // A ref, not state: a double-click lands before React re-renders the disabled button.
    if (resetInFlight.current) return;
    resetInFlight.current = true;
    setResetting(true);
    try {
      const r = await resetDemo(plant);
      onRunStarted(r.run_id, { station: null, kind: null, since: new Date().toISOString() });
      onAction("Fresh demo session started");
    } catch (e: any) {
      onError(e?.message ?? String(e));
    } finally {
      resetInFlight.current = false;
      setResetting(false);
    }
  }

  async function startPreset(key: string) {
    setPreset(key);
    try {
      const run = await startDemoScenario(key, plant);
      onRunStarted(run.run_id, { station: run.station_id, kind: FOCUS_KIND[key] ?? null,
                                 since: new Date(Date.now() - 5000).toISOString() });
      onAction(`Preset ${key.replace("_", " ")} started`);
    } catch (e: any) {
      onError(e?.message ?? String(e));
    } finally {
      setPreset("");
    }
  }

  async function report() {
    if (station === "" || note.trim().length < 4) return;
    try {
      await openIncident({ plant_id: plant, kind, station_id: station,
                           title: `Operator report at S${station}`, notes: note.trim() });
      onAction("Issue reported; investigation started");
      setReporting(false);
      setNote("");
    } catch (e: any) {
      onError(e?.message ?? String(e));
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        aria-label="Demo preset"
        value={preset}
        disabled={!presets.length || preset !== ""}
        onChange={(e) => e.target.value && startPreset(e.target.value)}
        className="h-7 w-full min-w-0 border border-line bg-canvas px-1.5 text-2xs text-ink"
      >
        <option value="">Demo preset…</option>
        {presets.map((p) => <option key={p.key} value={p.key}>{p.title}</option>)}
      </select>
      <Button size="xs" variant="outline" onClick={() => setReporting((v) => !v)}>
        {reporting ? "Cancel" : "Report an issue"}
      </Button>
      {demoMode && (
        // A reset cannot be undone, so it asks first (ADR-075). The API path the
        // verifiers use (POST /api/demo/reset) is unchanged.
        <Dialog open={confirmReset} onOpenChange={setConfirmReset}>
          <DialogTrigger asChild>
            <Button size="xs" variant="ghost" disabled={resetting}>
              {resetting ? "Resetting…" : "Reset demo"}
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Reset the demo?</DialogTitle>
              <DialogDescription>
                This stops the current simulation run, closes its open incidents and starts
                a fresh session. The audit trail is kept.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <DialogClose asChild>
                <Button size="sm" variant="outline">Keep current run</Button>
              </DialogClose>
              <Button size="sm" variant="primary" onClick={() => { setConfirmReset(false); reset(); }}>
                Reset demo
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
      {reporting && (
        <div className="flex w-full flex-wrap items-center gap-1.5">
          <select aria-label="Station" value={station}
            onChange={(e) => setStation(e.target.value ? Number(e.target.value) : "")}
            className="h-7 border border-line bg-canvas px-1.5 text-2xs text-ink">
            <option value="">Station…</option>
            {stations.map((s) => <option key={s.station_id} value={s.station_id}>S{s.station_id} {s.station_name}</option>)}
          </select>
          <select aria-label="Issue type" value={kind} onChange={(e) => setKind(e.target.value)}
            className="h-7 border border-line bg-canvas px-1.5 text-2xs text-ink">
            <option value="equipment">Equipment / procedure</option>
            <option value="throughput">Throughput / slow</option>
            <option value="quality">Quality / defects</option>
          </select>
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500}
            placeholder="What did you see? (no operator names)"
            className="h-7 min-w-0 flex-1 border border-line bg-canvas px-2 text-2xs text-ink" />
          <Button size="xs" variant="primary" disabled={station === "" || note.trim().length < 4}
            onClick={report}>Report</Button>
        </div>
      )}
    </div>
  );
}
