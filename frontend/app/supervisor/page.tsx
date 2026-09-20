"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  Containment, StationCfg, StreamEvent, UnitDetail, Zone,
  acknowledgeAlert, getContainment, getEscalationPolicy, getRole, getSensorDropouts,
  getStations, getUnit, postFeedback, setRole, streamUrl,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import StaleDataIndicator from "@/components/shared/StaleDataIndicator";
import SensorDropoutAlert from "@/components/supervisor/SensorDropoutAlert";
import EscalationChain from "@/components/supervisor/EscalationChain";
import AlertTriageTable from "@/components/supervisor/AlertTriageTable";
import { useAuditToast } from "@/components/shared/AuditToast";
import {
  AppShell, Badge, Button, DataTable, Panel, SegmentedControl, Skeleton, Stat, Tooltip,
  type Column,
} from "@/components/ui";
import CopilotDock from "@/components/copilot/CopilotDock";

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

interface LiveStation {
  state: string;
  unit_id?: string;
  cycle_time_s?: number;
  risk?: number;
}

export default function SupervisorPage() {
  const [plant, setPlant] = useState("plant_a");
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
  const [lastKnownAt, setLastKnownAt] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);
  // Replay transport. `running` gates the SSE effect; resumeTs is a REF, not state, so
  // the simulated clock advancing on every flush does not retrigger the effect and
  // reopen the connection sixty times a second.
  const [running, setRunning] = useState(true);
  const resumeTs = useRef<string | null>(null);
  const [dropouts, setDropouts] = useState<
    { station_id: number; sensor: string; since: string; duration_s: number }[]
  >([]);
  const [escalation, setEscalation] = useState<any>(null);
  const [acked, setAcked] = useState<Record<string, string>>({});
  const { show, ToastElement } = useAuditToast();
  const reduced = useReducedMotion();

  useEffect(() => { getEscalationPolicy().then(setEscalation).catch(() => setEscalation(null)); }, []);

  const acknowledge = async (unitId: string) => {
    const r = await acknowledgeAlert(unitId, plant);
    setAcked((a) => ({ ...a, [unitId]: r.acknowledged_at }));
    show("Action logged");
  };

  // Identifies this view to the backend RBAC (serve/middleware/auth.py) instead of
  // relying on lib/api.ts's global "leadership" default.
  useEffect(() => { setRole("supervisor"); }, []);

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
    setLive({}); setAlerts([]); setSelected(null); setContainment(null);
  }, [plant]);

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
    streamUrl(plant, speed, resumeTs.current).then((url) => {
      if (cancelled) return;
      es = new EventSource(url);
      es.onopen = () => setConnected(true);
      es.onerror = () => setConnected(false);
      es.addEventListener("station_update", (ev) => push(JSON.parse((ev as MessageEvent).data)));
      es.addEventListener("alert", (ev) => push(JSON.parse((ev as MessageEvent).data)));
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
  }, [plant, speed, push, retryNonce, running]);

  // Switching plant must not resume the NEW plant at the OLD plant's timestamp.
  useEffect(() => { resumeTs.current = null; }, [plant]);

  const restart = () => {
    resumeTs.current = null;
    setLive({});
    setAlerts([]);
    setSelected(null);
    setContainment(null);
    setClock("");
    setRunning(true);
    setRetryNonce((n) => n + 1);
  };

  const openUnit = async (unitId: string) => {
    const [u, c] = await Promise.all([getUnit(unitId, plant), getContainment(unitId, plant)]);
    setSelected(u); setContainment(c);
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

  const containmentCols: Column<Containment["at_risk_units"][number]>[] = [
    { key: "unit", header: "Unit", cell: (u) => <span className="text-numeric text-ink">{u.unit_id}</span> },
    { key: "at", header: "At", cell: (u) => <span className="text-numeric text-ink-dim">S{u.current_station}</span> },
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
      cell: (u) => <span className="text-ink-faint">{u.shared_factors.join(", ")}</span>,
    },
    {
      key: "action", header: "Action", align: "right",
      cell: (u) => (
        <Badge variant={u.action === "HOLD" ? "danger" : u.action === "INSPECT" ? "warn" : "neutral"}>
          {u.action}
        </Badge>
      ),
    },
  ];

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
          {/* Replay transport. Pause closes the SSE connection outright rather than
              buffering: the server is streaming a file, so a paused client that keeps
              the socket open would just accumulate a backlog to flush on resume. */}
          <div className="flex shrink-0 items-center gap-1">
            <Tooltip content={running ? "Pause the replay — the line map keeps its last state" : "Resume from where the replay stopped"}>
              <Button
                variant={running ? "warn" : "ok"}
                size="sm"
                onClick={() => setRunning((r) => !r)}
                aria-label={running ? "Stop simulation" : "Start simulation"}
              >
                {running ? "■ Stop" : "▶ Start"}
              </Button>
            </Tooltip>
            <Tooltip content="Restart the shift from the beginning and clear the alert feed">
              <Button variant="ghost" size="sm" onClick={restart} aria-label="Restart simulation">
                ↻
              </Button>
            </Tooltip>
          </div>

          <SegmentedControl
            name="speed"
            aria-label="Replay speed"
            value={speed}
            onChange={setSpeed}
            options={[1, 5, 10, 50].map((s) => ({ value: s, label: `${s}×` }))}
          />

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
          <Tooltip content={simDate ? `Replay clock — ${simDate}` : "Waiting for the first event"}>
            <span className="text-numeric w-[4.75rem] shrink-0 cursor-help text-right text-2xs tabular-nums text-ink">
              {simTime}
            </span>
          </Tooltip>
          <Badge
            variant={!running ? "neutral" : connected ? "ok" : "danger"}
            dot
            className="w-[4.75rem] justify-center"
          >
            {!running ? "Paused" : connected ? "Live" : "Down"}
          </Badge>
          <span className="w-8 shrink-0">
            <StaleDataIndicator timestamp={lastKnownAt} />
          </span>
        </>
      }
    >
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
                      sub={`CI ${(selected.ci_low * 100).toFixed(0)}–${(selected.ci_high * 100).toFixed(0)}%`}
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

                  <SectionLabel>Root causes</SectionLabel>
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

                  {selected.alert_fired ? (
                    <div className="mt-5 border-t border-line pt-4">
                      <SectionLabel>Escalation</SectionLabel>
                      <EscalationChain levels={escalation?.levels ?? []} currentLevel={1} />
                      <Button
                        variant="outline"
                        size="xs"
                        className="mt-3"
                        onClick={() => acknowledge(selected.unit_id)}
                        disabled={!!acked[selected.unit_id]}
                      >
                        {acked[selected.unit_id]
                          ? `Acknowledged ${acked[selected.unit_id].slice(11, 19)}`
                          : "Acknowledge"}
                      </Button>
                    </div>
                  ) : null}
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
                  title={`Containment — ${containment.total_at_risk} units share this fingerprint`}
                  note={`${containment.containment_window_min} min window · operator identity shown as group only`}
                >
                  <DataTable
                    columns={containmentCols}
                    rows={containment.at_risk_units}
                    rowKey={(u) => u.unit_id}
                    maxHeight={260}
                  />
                </Panel>
              </motion.div>
            )}
          </AnimatePresence>

          {/* ------------------------------------------------------- alert triage */}
          {/* Selecting a row drives the same unit panel the line map does, so the
              ranking and the detail view are never looking at different units. */}
          <AlertTriageTable plant={plant} onSelect={openUnit} refreshKey={retryNonce} />
        </div>

        {/* ------------------------------------------------------------ alert feed */}
        <Panel
          title="Alert feed"
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
                      <span className="text-numeric text-lg font-semibold text-danger-fg">
                        {(a.calibrated_risk * 100).toFixed(0)}%
                      </span>
                      <span className="text-numeric text-2xs text-ink-faint">{a.ts.slice(11, 19)}</span>
                    </div>
                    <div className="text-numeric mt-0.5 truncate text-xs text-ink">
                      {a.unit_id}
                      <span className="ml-1.5 text-ink-faint">
                        {a.variant.toUpperCase()} · lot {a.lot_id}
                      </span>
                    </div>
                    <div className="text-numeric text-2xs text-ink-faint">
                      at S{a.station_id} · cycle {a.cycle_time_s.toFixed(0)}s
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
