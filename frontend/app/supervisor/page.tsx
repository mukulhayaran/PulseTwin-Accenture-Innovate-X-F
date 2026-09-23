"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  AlertStatus, Containment, ContainmentAction, ContainmentDecisionHistory, SimulationMetrics, SimulationRun, SimulationScenario, StationCfg, StreamEvent, TriageRun, UnitDetail, Zone,
  acknowledgeAlert, closeAlert, getAlertStatus, getContainment, getContainmentDecisions, getEscalationPolicy, getRole, getSensorDropouts,
  getActiveSimulation, getSimulation, getSimulationMetrics, getStations, getTriageRun, getUnit, liveSimulationStreamUrl, pauseSimulation,
  injectSimulationIncident, postFeedback, recordContainmentDecision, resumeSimulation, startSimulation,
  stopSimulation, streamUrl,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { usePersonaAccess } from "@/lib/usePersonaAccess";
import AccessRestricted from "@/components/shared/AccessRestricted";
import StaleDataIndicator from "@/components/shared/StaleDataIndicator";
import SensorDropoutAlert from "@/components/supervisor/SensorDropoutAlert";
import EscalationChain from "@/components/supervisor/EscalationChain";
import AlertTriageTable from "@/components/supervisor/AlertTriageTable";
import { useAuditToast } from "@/components/shared/AuditToast";
import {
  AppShell, Badge, Button, DataTable, Panel, SegmentedControl, Select, Skeleton, Stat, Tooltip,
  type Column,
} from "@/components/ui";
import CopilotDock from "@/components/copilot/CopilotDock";
import FindingView from "@/components/copilot/FindingView";

// Station state -> surface treatment. Tint + border rather than a flat fill, so a
// 42-station line reads as a map instead of a block of colour.
const STATE_STYLE: Record<string, string> = {
  working: "border-ok/40 bg-ok/12 text-ok-fg",
  blocked: "border-danger/45 bg-danger/12 text-danger-fg",
  starved: "border-warn/45 bg-warn/12 text-warn-fg",
  down: "border-danger bg-danger/25 text-danger-fg",
  idle: "border-line bg-surface-2 text-ink-faint",
};

const STATE_DOT: Record<string, string> = {
  working: "bg-ok",
  blocked: "bg-danger",
  starved: "bg-warn",
  down: "bg-danger",
  idle: "bg-ink-faint/50",
};

// Alerting units mostly land in the same terminal leaf of every tree
// (docs/MODEL_CARD.md "Known failure modes") -- the model identifies the at-risk set but
// cannot rank within it, so the exact percentage is near-constant across the feed. A band
// avoids the false-precision look of dozens of near-identical decimals; the real number
// still appears, de-emphasized, in the card body below.
function riskBand(risk: number): { label: string; tone: "warn" | "danger" } {
  return risk > 0.5
    ? { label: "High risk", tone: "danger" }
    : { label: "At risk", tone: "warn" };
}

interface LiveStation {
  state: string;
  unit_id?: string;
  cycle_time_s?: number;
  risk?: number;
}

export default function SupervisorPage() {
  const [plant, setPlant] = useState("plant_a");
  const [mode, setMode] = useState<"replay" | "live_synthetic">("replay");
  const [scenario, setScenario] = useState<SimulationScenario>("normal");
  const [seed, setSeed] = useState(42);
  const [severity, setSeverity] = useState(0.7);
  const [affectedStation, setAffectedStation] = useState<number | null>(null);
  const [affectedLot, setAffectedLot] = useState("");
  const [incidentStartS, setIncidentStartS] = useState(0);
  const [activeRun, setActiveRun] = useState<SimulationRun | null>(null);
  const [runMetrics, setRunMetrics] = useState<SimulationMetrics | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [speed, setSpeed] = useState(10);
  const [stations, setStations] = useState<StationCfg[]>([]);
  const [zones, setZones] = useState<Zone[]>([]);
  const [live, setLive] = useState<Record<number, LiveStation>>({});
  const [alerts, setAlerts] = useState<StreamEvent[]>([]);
  const [budget, setBudget] = useState({ used: 0, max: 6 });
  const [clock, setClock] = useState<string>("");
  const [connected, setConnected] = useState(false);
  const [selected, setSelected] = useState<UnitDetail | null>(null);
  const [containment, setContainment] = useState<Containment | null>(null);
  const [voted, setVoted] = useState<Record<string, string>>({});
  const [decisionDraft, setDecisionDraft] = useState<{
    unit: Containment["at_risk_units"][number];
    action: ContainmentAction;
  } | null>(null);
  const [decisionMessage, setDecisionMessage] = useState("");
  const [decisionOwner, setDecisionOwner] = useState("supervisor");
  const [decisionDueStation, setDecisionDueStation] = useState<number | null>(null);
  const [decisionDueAt, setDecisionDueAt] = useState("");
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [decisionError, setDecisionError] = useState<string | null>(null);
  const [decisionHistory, setDecisionHistory] = useState<ContainmentDecisionHistory[]>([]);
  const [lastKnownAt, setLastKnownAt] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);
  // Replay transport. `running` gates the SSE effect; resumeTs is a REF, not state, so
  // the simulated clock advancing on every flush does not retrigger the effect and
  // reopen the connection sixty times a second.
  // Starts paused: the operator presses Start deliberately rather than the replay
  // beginning the instant this tab is opened.
  const [running, setRunning] = useState(false);
  const resumeTs = useRef<string | null>(null);
  const liveSeq = useRef(0);
  const [dropouts, setDropouts] = useState<
    { station_id: number; sensor: string; since: string; duration_s: number }[]
  >([]);
  const [escalation, setEscalation] = useState<any>(null);
  const [acked, setAcked] = useState<Record<string, string>>({});
  const [selectedAlertStatus, setSelectedAlertStatus] = useState<AlertStatus | null>(null);
  const [autoFinding, setAutoFinding] = useState<TriageRun | null>(null);
  const { show, ToastElement } = useAuditToast();
  const reduced = useReducedMotion();

  // An unattended "incident" investigation may already have run for this alert before
  // anyone clicked anything (serve/live_simulation.py::_trigger_auto_investigation).
  // Re-fetches whenever the selected alert changes to a different linked run, and clears
  // when the selection has no linked run at all.
  useEffect(() => {
    const runId = selectedAlertStatus?.triage_run_id;
    if (!runId) {
      setAutoFinding(null);
      return;
    }
    let cancelled = false;
    getTriageRun(runId).then((r) => { if (!cancelled) setAutoFinding(r); })
      .catch(() => { if (!cancelled) setAutoFinding(null); });
    return () => { cancelled = true; };
  }, [selectedAlertStatus?.triage_run_id]);

  useEffect(() => { getEscalationPolicy().then(setEscalation).catch(() => setEscalation(null)); }, []);
  useEffect(() => {
    getContainmentDecisions(plant, mode === "live_synthetic" ? activeRun?.run_id : null)
      .then(setDecisionHistory).catch(() => setDecisionHistory([]));
  }, [plant, mode, activeRun?.run_id]);

  const acknowledge = async (unitId: string) => {
    const r = await acknowledgeAlert(
      unitId,
      plant,
      mode === "live_synthetic" ? activeRun?.run_id : null,
    );
    setAcked((a) => ({ ...a, [unitId]: r.acknowledged_at }));
    const runId = mode === "live_synthetic" ? activeRun?.run_id : null;
    getAlertStatus(unitId, plant, runId).then(setSelectedAlertStatus).catch(() => undefined);
    show("Action logged");
  };

  const closeSelectedAlert = async () => {
    if (!selected || !activeRun?.run_id) return;
    const result = await closeAlert(selected.unit_id, plant, activeRun.run_id);
    setSelectedAlertStatus((current) => current ? {
      ...current, status: result.status, closed_at: result.closed_at,
    } : current);
    getSimulationMetrics(activeRun.run_id).then(setRunMetrics).catch(() => undefined);
    show("Alert closed");
  };

  // Gates entry on whatever role /login left in place, THEN locks this page to its own
  // fixed persona once granted -- see lib/usePersonaAccess.ts. "supervisor" is the floor
  // of the hierarchy, so this never actually blocks anyone; it stays for symmetry with
  // /manager and /leadership rather than being a special case. Identifies this view to
  // the backend RBAC (serve/middleware/auth.py) instead of relying on lib/api.ts's
  // global "leadership" default.
  const access = usePersonaAccess("supervisor");

  // Sensor dropouts -- real infra state, not simulated -- polled independently of the
  // replay clock so a dropout is visible even if the stream itself is paused.
  //
  // getSensorDropouts, NOT getSystemHealth: this page pins itself to `supervisor` above,
  // and /api/system/health requires `manager`. That mismatch returned 403 on every poll,
  // silently caught below, so this panel was permanently empty.
  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      getSensorDropouts(plant)
        .then((r) => { if (!cancelled) setDropouts(r.dropout_details ?? []); })
        .catch(() => { /* transient health-endpoint failure must not crash the page */ });
    };
    poll();
    const id = setInterval(poll, 10_000);
    return () => { cancelled = true; clearInterval(id); };
  }, [plant]);

  // SSE at 50x delivers ~1,400 events/sec. One setState per event locks the browser at
  // exactly the moment the demo shows off speed control, so events are buffered and
  // flushed once per animation frame (docs/AUDIT.md L25).
  const buffer = useRef<StreamEvent[]>([]);
  const frame = useRef<number | null>(null);

  const flush = useCallback(() => {
    frame.current = null;
    const batch = buffer.current;
    if (!batch.length) return;
    buffer.current = [];

    setLive((prev) => {
      const next = { ...prev };
      for (const e of batch) {
        next[e.station_id] = {
          state: e.station_state === "working" ? "working" : e.station_state,
          unit_id: e.unit_id,
          cycle_time_s: e.cycle_time_s,
          risk: e.calibrated_risk,
        };
      }
      return next;
    });

    const last = batch[batch.length - 1];
    setClock(last.ts);
    resumeTs.current = last.ts;
    if (last.alerts_budget) setBudget({ used: last.alerts_used ?? 0, max: last.alerts_budget });

    const newAlerts = batch.filter((e) => e.alert);
    if (newAlerts.length) {
      setAlerts((prev) => [...newAlerts.reverse(), ...prev].slice(0, 40));
    }
    setLastKnownAt(new Date().toISOString());
  }, []);

  const push = useCallback((e: StreamEvent) => {
    buffer.current.push(e);
    if (frame.current === null) frame.current = requestAnimationFrame(flush);
  }, [flush]);

  useEffect(() => {
    getStations(plant).then((r) => { setStations(r.stations); setZones(r.zones); });
    setLive({}); setAlerts([]); setSelected(null); setContainment(null); setSelectedAlertStatus(null);
    setDecisionDraft(null); setDecisionMessage(""); setDecisionError(null);
  }, [plant]);

  useEffect(() => {
    if (mode !== "live_synthetic") return;
    getActiveSimulation(plant)
      .then((run) => {
        setActiveRun(run);
        setRunning(run?.status === "running");
        if (run?.simulation_time) setClock(run.simulation_time);
      })
      .catch(() => setRunError("Could not read the active synthetic run."));
  }, [mode, plant]);

  useEffect(() => {
    if (mode !== "live_synthetic" || !activeRun) return;
    let cancelled = false;
    const poll = () => Promise.all([
      getSimulation(activeRun.run_id),
      getSimulationMetrics(activeRun.run_id),
    ]).then(([run, metrics]) => {
        if (cancelled) return;
        setActiveRun(run);
        setRunMetrics(metrics);
        if (run.simulation_time) setClock(run.simulation_time);
        if (["stopped", "completed", "failed"].includes(run.status)) setRunning(false);
      })
      .catch(() => { if (!cancelled) setRunError("Live run status is unavailable."); });
    const id = setInterval(poll, 1000);
    return () => { cancelled = true; clearInterval(id); };
  }, [mode, activeRun?.run_id]);

  useEffect(() => {
    if (!running) {
      // Paused: no connection at all. The line map keeps its last state rather than
      // clearing, which is the same principle as OfflineBanner retaining the last known
      // state -- a paused twin still shows what it last saw.
      setConnected(false);
      return;
    }
    let es: EventSource | null = null;
    let cancelled = false;
    const urlPromise = mode === "live_synthetic" && activeRun
      ? liveSimulationStreamUrl(activeRun.run_id, liveSeq.current)
      : streamUrl(plant, speed, resumeTs.current);
    urlPromise.then((url) => {
      if (cancelled) return;
      es = new EventSource(url);
      es.onopen = () => setConnected(true);
      es.onerror = () => setConnected(false);
      const accept = (ev: Event) => {
        const parsed = JSON.parse((ev as MessageEvent).data) as StreamEvent;
        if (parsed.seq) liveSeq.current = Math.max(liveSeq.current, parsed.seq);
        push(parsed);
      };
      es.addEventListener("station_update", accept);
      es.addEventListener("alert", accept);
      es.addEventListener("inspection_outcome", (ev) => {
        const parsed = JSON.parse((ev as MessageEvent).data) as StreamEvent;
        if (parsed.seq) liveSeq.current = Math.max(liveSeq.current, parsed.seq);
        setContainment((current) => current ? {
          ...current,
          at_risk_units: current.at_risk_units.map((unit) => {
            if (unit.unit_id !== parsed.unit_id || !unit.manual_action) return unit;
            const acted = unit.manual_action === "HOLD" || unit.manual_action === "INSPECT";
            const defect = parsed.actual_defect === 1;
            return {
              ...unit,
              decision_status: "VALIDATED",
              actual_outcome: parsed.actual_defect ?? null,
              outcome_at: parsed.ts,
              validation_result: acted
                ? defect ? "correct_intervention" : "unnecessary_intervention"
                : defect ? "missed_defect" : "correct_monitor",
            };
          }),
        } : current);
        setSelectedAlertStatus((current) =>
          current && current.alert_id.endsWith(`:${parsed.unit_id}`)
            ? {
                ...current,
                status: "VALIDATED",
                validated_at: parsed.ts,
                outcome: parsed.actual_defect ?? null,
                outcome_at: parsed.ts,
              }
            : current
        );
      });
    });
    // Deliberately does NOT setConnected(false): this cleanup runs on every speed and
    // plant change, and marking the twin offline for the ~50ms of an intentional
    // reconnect flashed the full-width TWIN OFFLINE banner on each one -- which both
    // lies to the operator and shifted the entire page (measured CLS 0.14). A genuine
    // failure still lands here via the new EventSource's onerror below.
    return () => { cancelled = true; es?.close(); };
    // retryNonce has no effect on what the stream does -- it exists so OfflineBanner's
    // [Retry Now] button (and its auto-retry ticker) can force this effect to re-run and
    // open a fresh EventSource without duplicating the connection logic above.
  }, [plant, speed, push, retryNonce, running, mode, activeRun]);

  // Switching plant must not resume the NEW plant at the OLD plant's timestamp.
  useEffect(() => { resumeTs.current = null; }, [plant]);

  const resetView = () => {
    resumeTs.current = null;
    liveSeq.current = 0;
    setLive({});
    setAlerts([]);
    setSelected(null);
    setSelectedAlertStatus(null);
    setContainment(null);
    setDecisionDraft(null);
    setDecisionMessage("");
    setDecisionError(null);
    setRunMetrics(null);
    setClock("");
    setRetryNonce((n) => n + 1);
  };

  const begin = async () => {
    setRunError(null);
    if (mode === "replay") {
      setRunning(true);
      return;
    }
    try {
      if (activeRun?.producer === "mqtt" && activeRun.status === "running") {
        setRunning(true);
        return;
      }
      const run = activeRun?.status === "paused"
        ? await resumeSimulation(activeRun.run_id)
        : await startSimulation(plant, scenario, seed, speed, severity, affectedStation, affectedLot, incidentStartS);
      setActiveRun(run);
      setRunning(true);
    } catch {
      setRunError("Could not start the live synthetic run. Stop the active run or retry.");
    }
  };

  const pause = async () => {
    setRunError(null);
    if (mode === "live_synthetic" && activeRun && activeRun.producer !== "mqtt") {
      try {
        const run = await pauseSimulation(activeRun.run_id);
        setActiveRun(run);
      } catch {
        setRunError("Could not pause the live synthetic run.");
        return;
      }
    }
    setRunning(false);
  };

  const restart = async () => {
    if (mode === "live_synthetic" && activeRun?.producer === "mqtt") {
      resetView();
      setRunning(activeRun.status === "running");
      return;
    }
    if (mode === "live_synthetic" && activeRun && ["running", "paused"].includes(activeRun.status)) {
      await stopSimulation(activeRun.run_id).catch(() => undefined);
    }
    setActiveRun(null);
    resetView();
    if (mode === "replay") {
      setRunning(true);
      return;
    }
    try {
      const run = await startSimulation(plant, scenario, seed, speed, severity, affectedStation, affectedLot, incidentStartS);
      setActiveRun(run);
      setRunning(true);
    } catch {
      setRunError("Could not restart the live synthetic run.");
    }
  };

  const endRun = async () => {
    if (mode === "live_synthetic" && activeRun && activeRun.producer !== "mqtt") {
      const run = await stopSimulation(activeRun.run_id);
      setActiveRun(run);
    }
    setRunning(false);
  };

  const injectIncident = async () => {
    if (!activeRun || activeRun.producer === "mqtt" || scenario === "normal") return;
    setRunError(null);
    try {
      const run = await injectSimulationIncident(
        activeRun.run_id, scenario, severity, affectedStation, affectedLot,
      );
      setActiveRun(run);
      show("Synthetic incident injected");
    } catch {
      setRunError("Could not inject the incident into this run.");
    }
  };

  const openUnit = async (unitId: string) => {
    setDecisionDraft(null);
    setDecisionMessage("");
    setDecisionError(null);
    const runId = mode === "live_synthetic" ? activeRun?.run_id : null;
    const [u, c, status] = await Promise.all([
      getUnit(unitId, plant, clock || null, runId),
      getContainment(unitId, plant, clock || null, runId),
      getAlertStatus(unitId, plant, runId).catch(() => null),
    ]);
    setSelected(u); setContainment(c); setSelectedAlertStatus(status);
    if (status?.acknowledged_at) {
      setAcked((current) => ({ ...current, [unitId]: status.acknowledged_at! }));
    }
  };

  const vote = async (unitId: string, verdict: string) => {
    setVoted((v) => ({ ...v, [unitId]: verdict }));
    await postFeedback(plant, unitId, verdict);
    show("Action logged");
  };

  const budgetTone =
    budget.used >= budget.max ? "danger" : budget.used >= budget.max - 1 ? "warn" : "ok";

  const simTime = useMemo(() => (clock ? clock.slice(11, 19) : "--:--:--"), [clock]);
  const simDate = useMemo(() => (clock ? clock.slice(0, 10) : ""), [clock]);

  const beginDecision = (unit: Containment["at_risk_units"][number]) => {
    setDecisionDraft({ unit, action: unit.manual_action ?? unit.action });
    setDecisionMessage("");
    setDecisionOwner("supervisor");
    const nextInspection = stations.find((station) =>
      !!station.is_inspection && (unit.current_station === null || station.station_id >= unit.current_station)
    );
    setDecisionDueStation(nextInspection?.station_id ?? null);
    setDecisionDueAt("");
    setDecisionError(null);
  };

  const saveDecision = async () => {
    if (!containment || !decisionDraft || decisionMessage.trim().length < 4) return;
    setDecisionBusy(true);
    setDecisionError(null);
    try {
      const saved = await recordContainmentDecision(
        plant,
        containment.flagged_unit,
        decisionDraft.unit.unit_id,
        decisionDraft.action,
        decisionMessage.trim(),
        mode === "live_synthetic" ? activeRun?.run_id : null,
        clock || null,
        decisionOwner,
        decisionDueStation,
        decisionDueAt || null,
      );
      setContainment((current) => current && current.flagged_unit === saved.flagged_unit_id
        ? {
            ...current,
            at_risk_units: current.at_risk_units.map((unit) => unit.unit_id === saved.target_unit_id
              ? {
                  ...unit,
                  manual_action: saved.selected_action,
                  decision_recorded_at: saved.created_at,
                }
              : unit),
          }
        : current);
      setDecisionDraft(null);
      setDecisionMessage("");
      if (selected && selected.unit_id === containment.flagged_unit) {
        getAlertStatus(
          selected.unit_id, plant, mode === "live_synthetic" ? activeRun?.run_id : null,
        ).then(setSelectedAlertStatus).catch(() => undefined);
      }
      getContainmentDecisions(plant, mode === "live_synthetic" ? activeRun?.run_id : null)
        .then(setDecisionHistory).catch(() => undefined);
      show("Containment decision logged");
    } catch {
      setDecisionError("Could not save this decision. Check the connection and try again.");
    } finally {
      setDecisionBusy(false);
    }
  };

  const containmentCols: Column<Containment["at_risk_units"][number]>[] = [
    { key: "unit", header: "Unit", cell: (u) => <span className="text-numeric text-ink">{u.unit_id}</span> },
    {
      key: "at", header: "At",
      cell: (u) => <span className="text-numeric text-ink-dim">
        {u.current_station === null ? "—" : `S${u.current_station}`}
      </span>,
    },
    {
      key: "risk", header: "Risk", align: "right",
      cell: (u) => (
        <span className={cn("text-numeric font-semibold", u.risk > 0.5 ? "text-danger-fg" : "text-warn-fg")}>
          {(u.risk * 100).toFixed(0)}%
        </span>
      ),
    },
    {
      key: "shared", header: "Shared factors",
      // Group labels only -- never an operator ID (CLAUDE.md §3, operator privacy).
      cell: (u) => (
        <Tooltip content={u.explanation}>
          <span className="cursor-help text-ink-faint">
            {u.shared_factors.join(", ").replaceAll("_", " ")}
          </span>
        </Tooltip>
      ),
    },
    {
      key: "action", header: "Suggested", align: "right",
      cell: (u) => (
        <Badge variant={u.action === "HOLD" ? "danger" : u.action === "INSPECT" ? "warn" : "neutral"}>
          {u.action}
        </Badge>
      ),
    },
    {
      key: "decision", header: "Human decision", align: "right",
      cell: (u) => (
        <div className="flex items-center justify-end gap-1.5">
          {u.manual_action && (
            <div className="flex flex-col items-end gap-0.5">
              <Badge variant={u.manual_action === "HOLD" ? "danger" : u.manual_action === "INSPECT" ? "warn" : "ok"}>
                {u.manual_action}
              </Badge>
              <span className="text-2xs text-ink-faint">
                {u.validation_result?.replaceAll("_", " ") ?? u.decision_status?.toLowerCase()}
              </span>
            </div>
          )}
          <Button variant="outline" size="xs" onClick={() => beginDecision(u)}>
            {u.manual_action ? "Update" : "Decide"}
          </Button>
        </div>
      ),
    },
  ];

  if (access === null) return null;
  if (access === false) return <AccessRestricted minRole="supervisor" />;

  return (
    <AppShell
      persona="Supervisor"
      plant={plant}
      onPlantChange={setPlant}
      role={getRole()}
      // A deliberate pause is NOT an outage: without `|| !running` the offline banner
      // would claim "TWIN OFFLINE" the moment an operator pressed Stop, and start an
      // auto-retry countdown against a connection nobody wants reopened.
      connected={connected || !running}
      lastKnownAt={lastKnownAt}
      onRetry={() => setRetryNonce((n) => n + 1)}
      controls={
        <>
          <SegmentedControl
            name="source-mode"
            aria-label="Simulation source"
            value={mode}
            onChange={(next) => {
              setMode(next);
              setRunning(false);
              setRunError(null);
              resetView();
            }}
            options={[
              { value: "replay", label: "Replay" },
              { value: "live_synthetic", label: "Live synthetic" },
            ]}
          />

          {mode === "live_synthetic" && activeRun?.producer !== "mqtt" && (
            <div className="flex items-center gap-1">
              <Select
                value={scenario}
                onChange={setScenario}
                ariaLabel="Synthetic scenario"
                options={[
                  { value: "normal", label: "Normal production" },
                  { value: "torque_degradation", label: "Torque degradation" },
                  { value: "bad_material_lot", label: "Bad material lot" },
                  { value: "high_humidity", label: "High humidity" },
                  { value: "rushed_checklist", label: "Rushed checklist" },
                  { value: "sensor_dropout", label: "Sensor dropout" },
                  { value: "station_breakdown", label: "Station breakdown" },
                  { value: "buffer_congestion", label: "Buffer congestion" },
                ]}
              />
              <label className="flex h-7 items-center gap-1 rounded-md border border-line bg-surface-2 px-2 text-2xs text-ink-faint">
                Seed
                <input
                  type="number"
                  value={seed}
                  min={0}
                  max={2147483647}
                  onChange={(event) => setSeed(Number(event.target.value) || 0)}
                  className="text-numeric w-14 bg-transparent text-right text-ink outline-none"
                />
              </label>
              <label className="flex h-7 items-center gap-1 rounded-md border border-line bg-surface-2 px-2 text-2xs text-ink-faint">
                Severity
                <input type="range" min={0} max={1} step={0.1} value={severity}
                  onChange={(event) => setSeverity(Number(event.target.value))}
                  className="w-16 accent-current" />
                <span className="text-numeric w-7 text-right text-ink">{Math.round(severity * 100)}%</span>
              </label>
              <label className="flex h-7 items-center gap-1 rounded-md border border-line bg-surface-2 px-2 text-2xs text-ink-faint">
                Station
                <select value={affectedStation ?? ""}
                  onChange={(event) => setAffectedStation(event.target.value ? Number(event.target.value) : null)}
                  className="max-w-20 bg-transparent text-ink outline-none">
                  <option value="">Auto</option>
                  {stations.map((station) => <option key={station.station_id} value={station.station_id}>S{station.station_id}</option>)}
                </select>
              </label>
              <label className="flex h-7 items-center gap-1 rounded-md border border-line bg-surface-2 px-2 text-2xs text-ink-faint">
                Lot
                <input value={affectedLot} placeholder="auto" maxLength={40}
                  onChange={(event) => setAffectedLot(event.target.value)}
                  className="w-16 bg-transparent text-ink outline-none" />
              </label>
              <label className="flex h-7 items-center gap-1 rounded-md border border-line bg-surface-2 px-2 text-2xs text-ink-faint">
                Starts
                <input type="number" min={0} max={86400} step={30} value={incidentStartS}
                  onChange={(event) => setIncidentStartS(Math.max(0, Number(event.target.value) || 0))}
                  className="text-numeric w-14 bg-transparent text-right text-ink outline-none" />
                s
              </label>
              <Tooltip content="Generate a new random seed">
                <Button
                  variant="ghost"
                  size="xs"
                  aria-label="Generate random simulation seed"
                  onClick={() => setSeed(crypto.getRandomValues(new Uint32Array(1))[0] & 0x7fffffff)}
                >
                  Random
                </Button>
              </Tooltip>
              {activeRun?.status === "running" && scenario !== "normal" && (
                <Button variant="outline" size="xs" onClick={injectIncident}>Inject incident</Button>
              )}
            </div>
          )}

          <div className="flex shrink-0 items-center gap-1">
            <Tooltip content={activeRun?.producer === "mqtt"
              ? (running ? "Pause this browser's MQTT monitor" : "Resume this browser's MQTT monitor")
              : (running ? "Pause while keeping the shared plant state" : "Start or resume this simulation")}>
              <Button
                variant={running ? "warn" : "ok"}
                size="sm"
                onClick={running ? pause : begin}
                aria-label={running ? "Pause simulation" : "Start simulation"}
              >
                {running ? "Pause" : activeRun?.status === "paused" ? "Resume" : "Start"}
              </Button>
            </Tooltip>
            <Tooltip content="Start a new deterministic run and clear the local view">
              <Button variant="ghost" size="sm" onClick={restart} aria-label="Restart simulation">
                ↻
              </Button>
            </Tooltip>
            {mode === "live_synthetic" && activeRun && (
              <Button variant="ghost" size="sm" onClick={endRun}
                aria-label={activeRun.producer === "mqtt" ? "Disconnect from MQTT run" : "Stop live synthetic run"}>
                {activeRun.producer === "mqtt" ? "Disconnect" : "Stop"}
              </Button>
            )}
          </div>

          {activeRun?.producer !== "mqtt" && (
            <SegmentedControl
              name="speed"
              aria-label="Simulation speed"
              value={speed}
              onChange={setSpeed}
              options={[1, 5, 10, 50].map((s) => ({ value: s, label: `${s}×` }))}
            />
          )}

          {/* Budget is SERVER-derived (ADR-012): it survives a refresh and view switches,
              which client-side accumulation could not, since localStorage is banned. */}
          <Tooltip content="Alerts raised this shift against the human-attention budget. Derived server-side from the replay clock.">
            <div className="flex shrink-0 items-center gap-2 rounded-lg border border-line bg-surface px-2.5 py-1">
              <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-faint">
                Alerts
              </span>
              <span
                className={cn(
                  "text-numeric text-xs font-semibold",
                  budgetTone === "danger" ? "text-danger-fg" : budgetTone === "warn" ? "text-warn-fg" : "text-ok-fg"
                )}
              >
                {budget.used}/{budget.max}
              </span>
              <span className="h-1 w-16 overflow-hidden rounded-full bg-surface-3">
                <motion.span
                  className={cn(
                    "block h-full rounded-full",
                    budgetTone === "danger" ? "bg-danger" : budgetTone === "warn" ? "bg-warn" : "bg-ok"
                  )}
                  animate={{ width: `${Math.min(100, (budget.used / budget.max) * 100)}%` }}
                  transition={reduced ? { duration: 0 } : { type: "spring", stiffness: 200, damping: 26 }}
                />
              </span>
            </div>
          </Tooltip>
        </>
      }
      status={
        // Fixed widths, and deliberately terse: these swap between "--:--:--"/a real
        // timestamp and "Live"/"Down", so an unpinned width re-flows the whole ml-auto
        // cluster. The date moves into a tooltip because carrying it inline pushed the
        // header past one line once the webfont swapped in (Inter is wider than the
        // fallback), which dropped MAIN 32px at ~850ms -- load CLS 0.145.
        <>
          <Tooltip content={simDate ? `Simulation clock — ${simDate}` : "Waiting for the first event"}>
            <span className="text-numeric w-[4.75rem] shrink-0 cursor-help text-right text-2xs tabular-nums text-ink">
              {simTime}
            </span>
          </Tooltip>
          <Badge
            variant={!running ? "neutral" : connected ? "ok" : "danger"}
            dot
            className="w-[4.75rem] justify-center"
          >
            {!running ? "Paused" : connected
              ? (activeRun?.producer === "mqtt" ? "MQTT live" : mode === "live_synthetic" ? "Synthetic" : "Replay")
              : "Down"}
          </Badge>
          <span className="w-8 shrink-0">
            <StaleDataIndicator timestamp={lastKnownAt} />
          </span>
        </>
      }
    >
      <div className={cn(
        "mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-xs",
        mode === "live_synthetic"
          ? "border-ai/35 bg-ai/10 text-ai-fg"
          : "border-info/30 bg-info/10 text-info-fg"
      )}>
        <span className="font-semibold uppercase tracking-[0.12em]">
          {mode === "live_synthetic"
            ? "Live synthetic simulation — no real factory data connected"
            : "Recorded replay — committed synthetic shift"}
        </span>
        <span className="text-numeric text-2xs opacity-80">
          {mode === "live_synthetic"
            ? activeRun
              ? `${activeRun.run_id} · ${activeRun.producer === "mqtt" ? "MQTT transport" : `seed ${activeRun.seed}`} · ${activeRun.scenario.replaceAll("_", " ")} · ${activeRun.event_count} events · ${activeRun.units_in_progress} units · ${runMetrics?.active_alerts ?? activeRun.alerts_count} active alerts · ${runMetrics?.validated_decisions ?? 0} validated · ${runMetrics?.alert_correctness_rate == null ? "—" : `${(runMetrics.alert_correctness_rate * 100).toFixed(0)}%`} alert correctness · ${runMetrics?.estimated_escaped_defects_avoided ?? 0} avoided`
              : `not started · seed ${seed} · ${scenario.replaceAll("_", " ")}`
            : "Reproducible recorded events"}
        </span>
      </div>
      {runError && (
        <div role="alert" className="mb-4 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger-fg">
          {runError}
        </div>
      )}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.9fr)_minmax(320px,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          {/* ------------------------------------------------------------ line map */}
          <Panel
            title="Line map"
            note={
              <span className="flex flex-wrap items-center justify-end gap-x-3 gap-y-1">
                <Legend dot="bg-ok" label="working" />
                <Legend dot="bg-danger" label="blocked" />
                <Legend dot="bg-warn" label="starved" />
                <span className="flex items-center gap-1 text-warn-fg">
                  <span className="hatch-inferred size-2.5 rounded-sm border border-warn/40" />
                  inferred (no sensors)
                </span>
              </span>
            }
          >
            {/* min-height is reserved on BOTH branches: without it the skeleton-to-data
                swap resized the panel and shifted the whole page (measured load CLS
                0.145). Sized for a 30-station line; plant B's 42 still grow past it,
                which is a smaller shift than collapsing from nothing. */}
            {stations.length === 0 ? (
              <div className="min-h-[27rem] space-y-5">
                {[12, 6, 12].map((count, z) => (
                  <div key={z} className="space-y-2">
                    <Skeleton className="h-2.5 w-28" />
                    <div className="flex flex-wrap gap-1.5">
                      {Array.from({ length: count }, (_, i) => (
                        <Skeleton key={i} className="h-14 w-[76px]" style={{ animationDelay: `${i * 25}ms` }} />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="min-h-[27rem] space-y-5">
                {zones.map((z) => (
                  <div key={z.id}>
                    <div className="mb-2 flex items-center gap-2">
                      <span className="text-2xs font-medium uppercase tracking-[0.16em] text-ink-faint">
                        {z.name}
                      </span>
                      <span className="h-px flex-1 bg-line" />
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {stations
                        .filter((s) => s.zone === z.id)
                        .map((s) => {
                          const l = live[s.station_id];
                          const manual = s.sensor_tier === "manual";
                          const dropout = dropouts.find((d) => d.station_id === s.station_id);
                          const state = l?.state ?? "idle";
                          return (
                            <Tooltip
                              key={s.station_id}
                              content={
                                <span>
                                  <b className="text-ink">{s.station_name}</b>
                                  <br />
                                  state {state}
                                  {l?.cycle_time_s ? ` · cycle ${l.cycle_time_s.toFixed(0)}s` : ""}
                                  {manual && (
                                    <>
                                      <br />
                                      <span className="text-warn-fg">
                                        Manual station — no continuous sensors. Values are inferred.
                                      </span>
                                    </>
                                  )}
                                  {dropout && (
                                    <>
                                      <br />
                                      <span className="text-warn-fg">
                                        {dropout.sensor} sensor offline since {dropout.since}
                                      </span>
                                    </>
                                  )}
                                </span>
                              }
                            >
                              <button
                                onClick={() => l?.unit_id && openUnit(l.unit_id)}
                                aria-label={`Station ${s.station_id}, ${s.station_name}, ${state}`}
                                className={cn(
                                  "relative h-14 w-[76px] overflow-hidden rounded-md border px-2 py-1.5 text-left",
                                  // colour + transform only: this repaints at up to
                                  // 1,400 events/sec and must never trigger layout.
                                  "transition-[background-color,border-color,color,transform] duration-300 ease-soft",
                                  "hover:-translate-y-px hover:brightness-125",
                                  STATE_STYLE[state] ?? STATE_STYLE.idle,
                                  dropout && "ring-2 ring-warn ring-offset-1 ring-offset-canvas"
                                )}
                              >
                                {manual && (
                                  <span aria-hidden className="hatch-inferred absolute inset-0 opacity-70" />
                                )}
                                <span className="relative flex items-start justify-between">
                                  <span className="text-numeric text-xs font-semibold">S{s.station_id}</span>
                                  <span className={cn("mt-1 size-1.5 shrink-0 rounded-full", STATE_DOT[state])} />
                                </span>
                                <span className="relative mt-0.5 block truncate text-2xs leading-tight opacity-70">
                                  {s.station_name}
                                </span>
                                <span className="text-numeric relative block text-2xs opacity-90">
                                  {l?.cycle_time_s ? `${l.cycle_time_s.toFixed(0)}s` : "—"}
                                </span>
                                {manual && (
                                  <span className="absolute bottom-0.5 right-1 text-2xs font-bold leading-none text-warn-fg">
                                    INF
                                  </span>
                                )}
                              </button>
                            </Tooltip>
                          );
                        })}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <SensorDropoutAlert dropouts={dropouts} />

          {/* ------------------------------------------------------- unit tracker */}
          <AnimatePresence mode="popLayout">
            {selected && (
              <motion.div
                key={selected.unit_id}
                initial={reduced ? false : { opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduced ? undefined : { opacity: 0, y: -8 }}
                transition={{ duration: 0.22, ease: [0.2, 0.8, 0.25, 1] }}
              >
                <Panel
                  title={
                    <span className="text-numeric normal-case tracking-normal text-ink">
                      Unit {selected.unit_id}
                      <span className="ml-2 text-ink-faint">
                        {selected.variant.toUpperCase()} · lot {selected.lot_id}
                      </span>
                    </span>
                  }
                  actions={
                    <Button variant="ghost" size="xs" onClick={() => setSelected(null)} aria-label="Close unit">
                      ✕
                    </Button>
                  }
                >
                  <div className="mb-5 flex flex-wrap gap-6">
                    <Stat
                      label="Risk"
                      value={`${(selected.calibrated_risk * 100).toFixed(0)}%`}
                      sub={`Derived range ${(selected.ci_low * 100).toFixed(0)}–${(selected.ci_high * 100).toFixed(0)}%`}
                      tone={selected.calibrated_risk > 0.5 ? "danger" : "warn"}
                    />
                    <Stat
                      label="Lead time"
                      value={`${selected.lead_time_min.toFixed(0)} min`}
                      sub="before final gate"
                    />
                    <Stat
                      label="Alert"
                      value={selected.alert_fired ? "Fired" : "Below budget"}
                      tone={selected.alert_fired ? "danger" : "default"}
                    />
                  </div>

                  <SectionLabel>Risk drivers</SectionLabel>
                  <div className="mb-5 space-y-2">
                    {selected.factors.map((f) => (
                      <FactorBar
                        key={f.rank}
                        f={f}
                        max={Math.max(...selected.factors.map((x) => Math.abs(x.contribution)))}
                        reduced={!!reduced}
                      />
                    ))}
                  </div>

                  <SectionLabel>Journey</SectionLabel>
                  <div className="mb-1 flex flex-wrap gap-1">
                    {selected.journey.map((j) => (
                      <span
                        key={j.station_id}
                        className="text-numeric rounded-md border border-line bg-surface-2 px-1.5 py-0.5 text-2xs"
                      >
                        S{j.station_id}
                        <span className="ml-1 text-ink-faint">{j.cycle_s.toFixed(0)}s</span>
                      </span>
                    ))}
                  </div>

                  {selected.alert_fired || selectedAlertStatus ? (
                    <div className="mt-5 border-t border-line pt-4">
                      <SectionLabel>Escalation</SectionLabel>
                      <EscalationChain levels={escalation?.levels ?? []} currentLevel={1} />
                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        <Button
                          variant="outline"
                          size="xs"
                          onClick={() => acknowledge(selected.unit_id)}
                          disabled={!!acked[selected.unit_id] || selectedAlertStatus?.status === "CLOSED"}
                        >
                          {acked[selected.unit_id]
                            ? `Acknowledged ${acked[selected.unit_id].slice(11, 19)}`
                            : "Acknowledge"}
                        </Button>
                        {selectedAlertStatus && (
                          <Badge variant={selectedAlertStatus.status === "CLOSED" ? "ok" : "neutral"}>
                            {selectedAlertStatus.status.toLowerCase()}
                          </Badge>
                        )}
                        {selectedAlertStatus?.status === "VALIDATED" && activeRun?.run_id && (
                          <Button variant="ok" size="xs" onClick={closeSelectedAlert}>
                            Close alert
                          </Button>
                        )}
                      </div>
                    </div>
                  ) : null}

                  {autoFinding && (
                    <div className="mt-5 border-t border-line pt-4">
                      <div className="mb-1.5 flex items-center gap-2">
                        <Badge variant="ai">AI</Badge>
                        <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-faint">
                          Auto-investigated before this alert was even opened here
                        </span>
                      </div>
                      <FindingView run={autoFinding} />
                    </div>
                  )}
                </Panel>
              </motion.div>
            )}
          </AnimatePresence>

          {/* --------------------------------------------------------- containment */}
          <AnimatePresence>
            {containment && containment.at_risk_units.length > 0 && (
              <motion.div
                initial={reduced ? false : { opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduced ? undefined : { opacity: 0 }}
                transition={{ duration: 0.22 }}
              >
                <Panel
                  tone="warn"
                  title={`Containment — ${containment.total_at_risk} units share production context`}
                  note={`${containment.containment_window_min} min window · operator identity shown as group only`}
                >
                  <DataTable
                    columns={containmentCols}
                    rows={containment.at_risk_units}
                    rowKey={(u) => u.unit_id}
                    maxHeight={260}
                  />
                  {decisionDraft && (
                    <div className="mt-3 rounded-md border border-line-strong bg-surface-2 p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div>
                          <div className="text-xs font-semibold text-ink">
                            Manual decision for {decisionDraft.unit.unit_id}
                          </div>
                          <div className="mt-0.5 text-2xs text-ink-faint">
                            System suggestion: {decisionDraft.unit.action}
                          </div>
                        </div>
                        <div className="flex gap-1" role="group" aria-label="Containment action">
                          {(["MONITOR", "INSPECT", "HOLD"] as ContainmentAction[]).map((action) => (
                            <Button
                              key={action}
                              size="xs"
                              variant={decisionDraft.action === action
                                ? action === "HOLD" ? "danger" : action === "INSPECT" ? "warn" : "ok"
                                : "outline"}
                              aria-pressed={decisionDraft.action === action}
                              onClick={() => setDecisionDraft((draft) => draft ? { ...draft, action } : draft)}
                            >
                              {action}
                            </Button>
                          ))}
                        </div>
                      </div>
                      <label htmlFor="containment-decision-message" className="mt-3 block text-2xs font-medium text-ink-dim">
                        Message for later validation
                      </label>
                      <textarea
                        id="containment-decision-message"
                        value={decisionMessage}
                        maxLength={500}
                        rows={2}
                        placeholder="Example: Inspect paint finish at the next quality gate."
                        onChange={(event) => setDecisionMessage(event.target.value)}
                        className="mt-1 w-full resize-y rounded-md border border-line-strong bg-surface px-2.5 py-2 text-xs text-ink outline-none placeholder:text-ink-faint focus:border-info"
                      />
                      <div className="mt-3 grid gap-2 sm:grid-cols-3">
                        <label className="text-2xs font-medium text-ink-dim">
                          Owner
                          <input
                            value={decisionOwner}
                            maxLength={100}
                            onChange={(event) => setDecisionOwner(event.target.value)}
                            className="mt-1 w-full rounded-md border border-line-strong bg-surface px-2 py-1.5 text-xs text-ink outline-none focus:border-info"
                          />
                        </label>
                        <label className="text-2xs font-medium text-ink-dim">
                          Due station
                          <input
                            type="number"
                            min={1}
                            value={decisionDueStation ?? ""}
                            onChange={(event) => setDecisionDueStation(event.target.value ? Number(event.target.value) : null)}
                            className="mt-1 w-full rounded-md border border-line-strong bg-surface px-2 py-1.5 text-xs text-ink outline-none focus:border-info"
                          />
                        </label>
                        <label className="text-2xs font-medium text-ink-dim">
                          Due time
                          <input
                            type="datetime-local"
                            value={decisionDueAt}
                            onChange={(event) => setDecisionDueAt(event.target.value)}
                            className="mt-1 w-full rounded-md border border-line-strong bg-surface px-2 py-1.5 text-xs text-ink outline-none focus:border-info"
                          />
                        </label>
                      </div>
                      <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
                        <span className="text-2xs text-ink-faint">
                          Required. Do not enter operator names or personal data. {decisionMessage.length}/500
                        </span>
                        <div className="flex gap-1.5">
                          <Button
                            variant="ghost"
                            size="xs"
                            disabled={decisionBusy}
                            onClick={() => { setDecisionDraft(null); setDecisionMessage(""); setDecisionError(null); }}
                          >
                            Cancel
                          </Button>
                          <Button
                            variant="ok"
                            size="xs"
                            disabled={decisionBusy || decisionMessage.trim().length < 4}
                            onClick={saveDecision}
                          >
                            {decisionBusy ? "Saving…" : "Save decision"}
                          </Button>
                        </div>
                      </div>
                      {decisionError && <p role="alert" className="mt-2 text-2xs text-danger-fg">{decisionError}</p>}
                    </div>
                  )}
                  {decisionHistory.length > 0 && (
                    <div className="mt-4 border-t border-line pt-3">
                      <SectionLabel>Decision history</SectionLabel>
                      <div className="space-y-1.5">
                        {decisionHistory.slice(0, 5).map((decision) => (
                          <div key={decision.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-surface-2 px-2.5 py-1.5 text-2xs">
                            <span className="text-numeric text-ink">{decision.target_unit_id}</span>
                            <span className="text-ink-dim">
                              {decision.selected_action} · {decision.decided_by_user || decision.decided_by_role}
                              {decision.due_station ? ` · due S${decision.due_station}` : ""}
                            </span>
                            <Badge variant={decision.validation_result === "correct_intervention" ? "ok" : decision.validation_result ? "warn" : "neutral"}>
                              {(decision.validation_result ?? decision.status).replaceAll("_", " ")}
                            </Badge>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </Panel>
              </motion.div>
            )}
          </AnimatePresence>

          {/* ------------------------------------------------------- alert triage */}
          {/* Selecting a row drives the same unit panel the line map does, so the
              ranking and the detail view are never looking at different units. */}
          <AlertTriageTable plant={plant} onSelect={openUnit} refreshKey={retryNonce} atTs={clock} />
        </div>

        {/* ------------------------------------------------------------ alert feed */}
        <Panel
          title={
            <Tooltip content="The model flags the at-risk set; it does not rank within it. Alerting units on this plant currently share one calibrated score, so this feed orders by time, not by risk. See docs/MODEL_CARD.md 'Known failure modes'.">
              <span className="cursor-help border-b border-dashed border-ink-faint/40">
                Alert feed
              </span>
            </Tooltip>
          }
          note={alerts.length ? `${alerts.length} shown` : undefined}
          className="xl:sticky xl:top-[60px] xl:max-h-[calc(100vh-76px)]"
          bodyClassName="p-3"
        >
          {alerts.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-12 text-center">
              <span className="flex size-8 items-center justify-center rounded-full border border-ok/30 bg-ok/10 text-ok-fg">
                ✓
              </span>
              <p className="text-xs text-ink-dim">No alerts yet</p>
              <p className="max-w-48 text-2xs leading-relaxed text-ink-faint">
                The line is inside the alarm budget.
              </p>
            </div>
          ) : (
            <div className="scroll-fade-b max-h-[calc(100vh-150px)] space-y-2 overflow-y-auto pr-1">
              <AnimatePresence initial={false}>
                {alerts.map((a, i) => (
                  <motion.article
                    key={`${a.unit_id}-${a.ts}`}
                    // No `layout` prop: it measures every card's box on each render, and
                    // this list re-renders on the rAF flush with up to 40 cards in it --
                    // per-frame forced layout is the exact cost the buffering above
                    // exists to avoid. The enter animation is transform+opacity only.
                    initial={reduced ? false : { opacity: 0, x: 16, scale: 0.97 }}
                    animate={{ opacity: 1, x: 0, scale: 1 }}
                    transition={{ duration: 0.25, ease: [0.2, 0.8, 0.25, 1] }}
                    className="rounded-lg border border-danger/30 bg-danger/[0.06] p-2.5"
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <Badge variant={riskBand(a.calibrated_risk).tone}>
                        {riskBand(a.calibrated_risk).label}
                      </Badge>
                      <span className="text-numeric text-2xs text-ink-faint">{a.ts.slice(11, 19)}</span>
                    </div>
                    <div className="text-numeric mt-0.5 truncate text-xs text-ink">
                      {a.unit_id}
                      <span className="ml-1.5 text-ink-faint">
                        {a.variant.toUpperCase()} · lot {a.lot_id}
                      </span>
                    </div>
                    <div className="text-numeric text-2xs text-ink-faint">
                      at S{a.station_id} · cycle {a.cycle_time_s.toFixed(0)}s ·{" "}
                      {(a.calibrated_risk * 100).toFixed(0)}%
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      <Button variant="outline" size="xs" onClick={() => openUnit(a.unit_id)}>
                        Show units ↗
                      </Button>
                      <Button
                        variant={voted[a.unit_id] === "useful" ? "ok" : "ghost"}
                        size="xs"
                        aria-label="Mark alert useful"
                        onClick={() => vote(a.unit_id, "useful")}
                      >
                        Useful
                      </Button>
                      <Button
                        variant={voted[a.unit_id] === "false_alarm" ? "danger" : "ghost"}
                        size="xs"
                        aria-label="Mark alert a false alarm"
                        onClick={() => vote(a.unit_id, "false_alarm")}
                      >
                        False alarm
                      </Button>
                    </div>
                  </motion.article>
                ))}
              </AnimatePresence>
            </div>
          )}
        </Panel>
      </div>
      {ToastElement}
      <CopilotDock plant={plant}
        onAction={show} />
    </AppShell>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-2 text-2xs font-medium uppercase tracking-[0.14em] text-ink-faint">
      {children}
    </div>
  );
}

function Legend({ dot, label }: { dot: string; label: string }) {
  return (
    <span className="flex items-center gap-1">
      <span className={cn("size-2 rounded-full", dot)} />
      {label}
    </span>
  );
}

function FactorBar({
  f, max, reduced,
}: {
  f: { plain_language: string; contribution: number; direction: string };
  max: number;
  reduced: boolean;
}) {
  const pct = Math.min(100, (Math.abs(f.contribution) / (max || 1)) * 100);
  const up = f.direction === "high";
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 text-xs">
        <span className="min-w-0 truncate text-ink-dim">{f.plain_language}</span>
        <span className={cn("text-numeric shrink-0 font-medium", up ? "text-danger-fg" : "text-info-fg")}>
          {up ? "▲" : "▼"} {f.contribution.toFixed(3)}
        </span>
      </div>
      <div className="mt-1 h-1 overflow-hidden rounded-full bg-surface-3">
        <motion.div
          className={cn("h-full rounded-full", up ? "bg-danger" : "bg-info")}
          initial={reduced ? false : { width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.45, ease: [0.2, 0.8, 0.25, 1] }}
        />
      </div>
    </div>
  );
}
