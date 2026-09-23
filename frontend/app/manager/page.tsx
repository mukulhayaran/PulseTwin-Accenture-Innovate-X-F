"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Bar, CartesianGrid, Cell, ComposedChart, Line, Scatter, Tooltip as RTooltip, XAxis, YAxis,
} from "recharts";
import {
  Bottleneck, Metrics, StationCfg,
  getBottleneck, getHealth, getMetrics, getRole, getStationStats, getStations, getWhatIf,
  getWhatIfStations,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { axisProps, gridProps, theme } from "@/lib/theme";
import { usePersonaAccess } from "@/lib/usePersonaAccess";
import AccessRestricted from "@/components/shared/AccessRestricted";
import StaleDataIndicator from "@/components/shared/StaleDataIndicator";
import { useAuditToast } from "@/components/shared/AuditToast";
import {
  AppShell, Badge, ChartFrame, ChartLegend, ChartTooltip, DataTable, Kpi, Panel,
  Select, Skeleton, SkeletonChart, SkeletonTable, Slider, Tooltip, type Column,
} from "@/components/ui";
import CopilotDock from "@/components/copilot/CopilotDock";

export default function ManagerPage() {
  const [plant, setPlant] = useState("plant_a");
  const [stations, setStations] = useState<StationCfg[]>([]);
  const [stats, setStats] = useState<any[]>([]);
  const [bn, setBn] = useState<Bottleneck | null>(null);
  const [history, setHistory] = useState<any[]>([]);
  const [metrics, setMetrics] = useState<Metrics | null>(null);

  const [wfStations, setWfStations] = useState<any[]>([]);
  const [wfStation, setWfStation] = useState<number | null>(null);
  const [buffer, setBuffer] = useState(4);
  const [cycle, setCycle] = useState(0);
  const [wf, setWf] = useState<any>(null);
  const [wfBusy, setWfBusy] = useState(false);

  const [connected, setConnected] = useState(true);
  const [lastKnownAt, setLastKnownAt] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);
  const { show, ToastElement } = useAuditToast();

  // Gates entry on whatever role /login left in place, THEN locks this page to its own
  // fixed persona once granted -- see lib/usePersonaAccess.ts. Identifies this view to
  // the backend RBAC (serve/middleware/auth.py) instead of relying on lib/api.ts's
  // global "leadership" default.
  const access = usePersonaAccess("manager");

  // Connectivity poll -- independent of the plant-scoped data fetches below, so a
  // dropped connection is visible (OfflineBanner) even between plant switches.
  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      getHealth()
        .then(() => { if (!cancelled) { setConnected(true); setLastKnownAt(new Date().toISOString()); } })
        .catch(() => { if (!cancelled) setConnected(false); });
    };
    poll();
    const id = setInterval(poll, 10_000);
    return () => { cancelled = true; clearInterval(id); };
  }, [retryNonce]);

  useEffect(() => {
    // Access not yet resolved, or this role was refused entry -- these all 403 at
    // "supervisor" and below (require_role("manager")), and an uncaught rejection from
    // any of them is an unhandled-promise crash in dev, not just a wasted call.
    if (access !== true) return;
    getStations(plant).then((r) => setStations(r.stations));
    getStationStats(plant).then(setStats);
    getBottleneck(plant).then(setBn).catch(() => setBn(null));
    getMetrics(plant).then(setMetrics).catch(() => setMetrics(null));
    fetchHistory(plant);
    getWhatIfStations(plant).then((s) => {
      setWfStations(s);
      setWfStation(s[0]?.station_id ?? null);
    });
  }, [plant, access]);

  async function fetchHistory(p: string) {
    const { apiBase } = await import("@/lib/api");
    const base = await apiBase();
    const r = await fetch(`${base}/api/bottleneck/history?plant_id=${p}&limit=700`);
    setHistory(await r.json());
  }

  // Debounced: sliders fire on every drag, and each call is a grid interpolation.
  useEffect(() => {
    if (wfStation === null || access !== true) return;
    const t = setTimeout(async () => {
      setWfBusy(true);
      try { setWf(await getWhatIf(plant, wfStation, buffer, cycle)); show("Action logged"); }
      catch { /* refused (role) or transient -- the panel just keeps its last value */ }
      finally { setWfBusy(false); }
    }, 220);
    return () => clearTimeout(t);
  }, [plant, wfStation, buffer, cycle, access]);

  const nameOf = useMemo(() => {
    const m = new Map(stations.map((s) => [s.station_id, s]));
    return (id: number) => m.get(id)?.station_name ?? `S${id}`;
  }, [stations]);

  const timeline = useMemo(
    () => history.map((h, i) => ({
      i,
      station: h.bottleneck_station,
      truth: h.ground_truth_station,
      correct: h.correct,
      shift: h.shift_event,
      t: h.window_start?.slice(5, 16) ?? "",
    })),
    [history]
  );

  const league = useMemo(
    () => stats.slice(0, 12).map((s) => ({ ...s, name: `S${s.station_id}` })),
    [stats]
  );

  const pareto = useMemo(() => {
    const imp = metrics?.permutation_importance ?? [];
    const top = imp.slice(0, 8);
    const total = top.reduce((a, b) => a + Math.max(0, b.delta_pr_auc), 0) || 1;
    let cum = 0;
    return top.map((r) => {
      cum += Math.max(0, r.delta_pr_auc);
      return {
        feature: r.feature.length > 26 ? r.feature.slice(0, 24) + "…" : r.feature,
        delta: r.delta_pr_auc,
        cumulative: (cum / total) * 100,
      };
    });
  }, [metrics]);

  const shiftCount = timeline.filter((t) => t.shift).length;

  const leagueCols: Column<any>[] = [
    {
      key: "stn", header: "Station",
      cell: (s) => {
        const inferred = s.provenance === "inferred";
        return (
          <span className="flex min-w-0 items-center gap-1.5">
            <span className={cn("text-numeric font-medium", inferred && "text-warn-fg")}>
              S{s.station_id}
            </span>
            <span className="truncate text-ink-faint">{nameOf(s.station_id).slice(0, 20)}</span>
            {inferred && (
              <Tooltip content="No continuous sensors at this station — utilisation is inferred, not measured.">
                <span><Badge variant="warn">INF</Badge></span>
              </Tooltip>
            )}
          </span>
        );
      },
    },
    { key: "util", header: "Util", align: "right", cell: (s) => <Num>{(s.util * 100).toFixed(1)}%</Num> },
    {
      key: "blocked", header: "Blocked", align: "right",
      cell: (s) => <Num tone={s.blocked > 0.05 ? "danger" : undefined}>{(s.blocked * 100).toFixed(1)}%</Num>,
    },
    {
      key: "starved", header: "Starved", align: "right",
      cell: (s) => <Num tone={s.starved > 0.05 ? "warn" : undefined}>{(s.starved * 100).toFixed(1)}%</Num>,
    },
    { key: "lost", header: "Lost UPH", align: "right", cell: (s) => <Num>{s.lost_uph.toFixed(1)}</Num> },
  ];

  if (access === null) return null;
  if (access === false) return <AccessRestricted minRole="manager" />;

  return (
    <AppShell
      persona="Analysis"
      plant={plant}
      onPlantChange={setPlant}
      role={getRole()}
      connected={connected}
      lastKnownAt={lastKnownAt}
      onRetry={() => setRetryNonce((n) => n + 1)}
      status={<StaleDataIndicator timestamp={lastKnownAt} />}
    >
      <div className="space-y-4">
        {/* ----------------------------------------------------------- KPI row */}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Kpi
            label="Current constraint"
            value={bn ? `S${bn.current_station}` : "—"}
            sub={bn?.current_station_name}
            tone="danger"
            accent
          />
          <Kpi
            label="Detection accuracy"
            value={bn?.detection_accuracy != null ? `${(bn.detection_accuracy * 100).toFixed(1)}%` : "—"}
            sub="vs 2h sustained truth"
          />
          <Kpi label="Constraint shifts" value={String(shiftCount)} sub={`in ${timeline.length} windows`} />
          <Kpi
            label="Forecast skill"
            value={metrics ? fmtSigned(metrics.values.forecast_skill_vs_persistence) : "—"}
            sub="vs persistence baseline"
            tone={(metrics?.values.forecast_skill_vs_persistence ?? 0) > 0 ? "ok" : "warn"}
          />
        </div>

        {/* -------------------------------------------- bottleneck timeline */}
        <Panel
          title="Constraint migration"
          note="Each point is a 15-minute window. Red = detector disagreed with the 2-hour sustained truth."
        >
          {timeline.length === 0 ? (
            <SkeletonChart height={230} />
          ) : (
            <>
              <ChartFrame height={230}>
                <ComposedChart data={timeline} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
                  <CartesianGrid {...gridProps} />
                  <XAxis
                    dataKey="t"
                    {...axisProps}
                    interval={Math.max(1, Math.floor(timeline.length / 8))}
                  />
                  <YAxis {...axisProps} domain={[0, "dataMax + 2"]} width={44} />
                  <RTooltip
                    cursor={{ stroke: theme.lineStrong, strokeDasharray: "3 3" }}
                    content={
                      <ChartTooltip
                        rows={(p) => (
                          <div className="space-y-0.5">
                            {p.map((r, i) => (
                              <div key={i} className="flex items-center justify-between gap-4 text-xs">
                                <span className="text-ink-dim">
                                  {r.dataKey === "station" ? "detected" : "sustained truth"}
                                </span>
                                <span className="text-numeric font-semibold text-ink">S{r.value}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      />
                    }
                  />
                  <Scatter dataKey="station" isAnimationActive={false}>
                    {timeline.map((d, i) => (
                      <Cell key={i} fill={d.correct ? theme.ok : theme.danger} />
                    ))}
                  </Scatter>
                  <Line
                    type="stepAfter" dataKey="truth" stroke={theme.ai} dot={false}
                    strokeWidth={1.5} isAnimationActive={false}
                  />
                </ComposedChart>
              </ChartFrame>
              <ChartLegend
                items={[
                  { color: theme.ok, label: "detector correct" },
                  { color: theme.danger, label: "detector wrong" },
                  { color: theme.ai, label: "sustained truth (2h)", shape: "line" },
                ]}
              />
            </>
          )}
        </Panel>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {/* --------------------------------------- station league table */}
          <Panel title="Station league table" note="Ranked by lost throughput.">
            {league.length === 0 ? (
              <SkeletonTable rows={8} cols={5} />
            ) : (
              <DataTable columns={leagueCols} rows={league} rowKey={(s) => String(s.station_id)} />
            )}
          </Panel>

          {/* ------------------------------------------ risk-driver pareto */}
          <Panel
            title="Risk driver Pareto"
            note="Permutation importance on the held-out test split — measured, not assumed."
          >
            {pareto.length === 0 ? (
              <SkeletonChart height={260} />
            ) : (
              <ChartFrame height={260}>
                <ComposedChart data={pareto} layout="vertical" margin={{ left: 104, right: 12, top: 4 }}>
                  <CartesianGrid {...gridProps} vertical horizontal={false} />
                  <XAxis type="number" {...axisProps} />
                  <YAxis
                    type="category" dataKey="feature" width={104}
                    {...axisProps} tick={{ ...axisProps.tick, fontSize: 9 }}
                  />
                  <RTooltip
                    cursor={{ fill: theme.surface2 }}
                    content={
                      <ChartTooltip
                        rows={(p) => (
                          <div className="flex items-center justify-between gap-4 text-xs">
                            <span className="text-ink-dim">Δ PR-AUC</span>
                            <span className="text-numeric font-semibold text-ink">
                              {Number(p[0]?.value).toFixed(4)}
                            </span>
                          </div>
                        )}
                      />
                    }
                  />
                  <Bar dataKey="delta" radius={[0, 3, 3, 0]} isAnimationActive={false} minPointSize={2}>
                    {pareto.map((_, i) => (
                      // Sequential intensity by rank, one hue -- this is a ranked
                      // magnitude list, not categorical series, so a flat fill was
                      // throwing away the "most important driver" story the order
                      // already tells. Darkest/most saturated at the top.
                      <Cell key={i} fillOpacity={1 - i * (0.55 / Math.max(1, pareto.length - 1))} fill={theme.info} />
                    ))}
                  </Bar>
                </ComposedChart>
              </ChartFrame>
            )}
          </Panel>
        </div>

        {/* ---------------------------------------------- what-if console */}
        <Panel
          title="What-if simulation"
          note="Interpolated from a precomputed grid of short simulations (ADR-007)."
          actions={wfBusy ? <Badge variant="info" dot>Recomputing</Badge> : undefined}
        >
          <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]">
            <div className="space-y-5">
              <div>
                <div className="mb-2 text-2xs font-medium uppercase tracking-[0.14em] text-ink-faint">
                  Station
                </div>
                {wfStations.length === 0 ? (
                  <Skeleton className="h-7 w-full" />
                ) : (
                  <Select
                    ariaLabel="What-if station"
                    className="w-full"
                    value={String(wfStation ?? "")}
                    onChange={(v) => setWfStation(Number(v))}
                    options={wfStations.map((s) => ({
                      value: String(s.station_id),
                      label: `S${s.station_id} — ${s.station_name}`,
                    }))}
                  />
                )}
              </div>

              <Slider
                label="Feed buffer" value={buffer} min={-2} max={8}
                display={`${buffer >= 0 ? "+" : ""}${buffer} slots`} onChange={setBuffer}
              />
              <Slider
                label="Cycle time" value={cycle} min={-10} max={10}
                display={`${cycle >= 0 ? "+" : ""}${cycle}%`} onChange={setCycle}
              />

              <div className="space-y-2.5 rounded-lg border border-line bg-surface-2/60 p-3">
                <p className="text-2xs leading-relaxed text-ink-faint">
                  <span className="font-medium text-warn-fg">Feed buffer</span> is capacity{" "}
                  <em>upstream</em> of the selected station. Adding capacity downstream of a
                  constraint does nothing — a bottleneck is never blocked by what follows it.
                </p>
                <p className="text-2xs leading-relaxed text-ink-faint">
                  Only the two constraints and their neighbours are in the grid — 90 simulated
                  scenarios per plant rather than 3,510. A manager tunes the bottleneck, not
                  station 4.
                </p>
              </div>
            </div>

            {wf ? (
              <div className="min-w-0">
                <div className="mb-4 grid grid-cols-2 gap-3">
                  <ScenarioBox
                    title="Baseline"
                    rows={[
                      ["Throughput", `${wf.baseline.throughput_uph.toFixed(1)} UPH`],
                      ["WIP", `${wf.baseline.wip_units.toFixed(0)} units`],
                      ["Bottleneck", `S${wf.baseline.bottleneck_station}`],
                    ]}
                  />
                  <ScenarioBox
                    title="Projected"
                    highlight
                    rows={[
                      ["Throughput", `${wf.projected.throughput_uph.toFixed(1)} UPH`],
                      ["WIP", `${wf.projected.wip_units.toFixed(0)} units`],
                      ["Bottleneck", `S${wf.projected.bottleneck_station}`],
                    ]}
                  />
                </div>

                <div className="mb-3 flex flex-wrap gap-x-6 gap-y-2">
                  <DeltaStat
                    label="Δ Throughput"
                    value={`${wf.delta.throughput_uph >= 0 ? "+" : ""}${wf.delta.throughput_uph.toFixed(2)} UPH`}
                    positive={wf.delta.throughput_uph >= 0}
                  />
                  <DeltaStat
                    label="Annual value"
                    value={`$${Math.round(wf.annual_value).toLocaleString()}`}
                    positive={wf.annual_value >= 0}
                  />
                </div>

                <div className="space-y-1 rounded-lg border border-line bg-surface-2/60 p-3 text-2xs leading-relaxed text-ink-faint">
                  <div>
                    Mode <span className="text-warn-fg">{wf.mode}</span> · bottleneck resolved by{" "}
                    <span className="text-warn-fg">{wf.bottleneck_mode}</span> — a station ID is
                    categorical and is never interpolated.
                  </div>
                  <div>{wf.assumptions}</div>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <Skeleton className="h-24" />
                  <Skeleton className="h-24" />
                </div>
                <Skeleton className="h-5 w-56" />
                <Skeleton className="h-14 w-full" />
              </div>
            )}
          </div>
        </Panel>
      </div>
      {ToastElement}
      <CopilotDock plant={plant}
        onAction={show} />
    </AppShell>
  );
}

function fmtSigned(v: number | undefined) {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(3)}`;
}

function Num({ children, tone }: { children: React.ReactNode; tone?: "danger" | "warn" }) {
  return (
    <span
      className={cn(
        "text-numeric",
        tone === "danger" ? "text-danger-fg" : tone === "warn" ? "text-warn-fg" : "text-ink-dim"
      )}
    >
      {children}
    </span>
  );
}

function ScenarioBox({
  title, rows, highlight,
}: {
  title: string; rows: [string, string][]; highlight?: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border p-3",
        highlight ? "border-info/40 bg-info/[0.07]" : "border-line bg-surface-2/50"
      )}
    >
      <div className="mb-2 text-2xs font-medium uppercase tracking-[0.14em] text-ink-faint">
        {title}
      </div>
      <div className="space-y-1">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-baseline justify-between gap-2 text-xs">
            <span className="text-ink-faint">{k}</span>
            <span className="text-numeric font-medium text-ink">{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function DeltaStat({ label, value, positive }: { label: string; value: string; positive: boolean }) {
  return (
    <div>
      <div className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-faint">{label}</div>
      <div className={cn("text-numeric text-lg font-semibold", positive ? "text-ok-fg" : "text-danger-fg")}>
        {value}
      </div>
    </div>
  );
}
