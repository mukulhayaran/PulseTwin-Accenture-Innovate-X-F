"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Bar, BarChart, CartesianGrid, Cell, ComposedChart, Line, ResponsiveContainer,
  Scatter, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  Bottleneck, Metrics, StationCfg,
  getBottleneck, getHealth, getMetrics, getRole, getStationStats, getStations, getWhatIf,
  getWhatIfStations, setRole,
} from "@/lib/api";
import OfflineBanner from "@/components/shared/OfflineBanner";
import StaleDataIndicator from "@/components/shared/StaleDataIndicator";
import { useAuditToast } from "@/components/shared/AuditToast";

const AXIS = { stroke: "#6b7280", fontSize: 10, fontFamily: "monospace" };
const GRID = "#1f2937";

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

  // Identifies this view to the backend RBAC (serve/middleware/auth.py) instead of
  // relying on lib/api.ts's global "leadership" default.
  useEffect(() => { setRole("manager"); }, []);

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
    getStations(plant).then((r) => setStations(r.stations));
    getStationStats(plant).then(setStats);
    getBottleneck(plant).then(setBn).catch(() => setBn(null));
    getMetrics(plant).then(setMetrics).catch(() => setMetrics(null));
    fetchHistory(plant);
    getWhatIfStations(plant).then((s) => {
      setWfStations(s);
      setWfStation(s[0]?.station_id ?? null);
    });
  }, [plant]);

  async function fetchHistory(p: string) {
    const { apiBase } = await import("@/lib/api");
    const base = await apiBase();
    const r = await fetch(`${base}/api/bottleneck/history?plant_id=${p}&limit=700`);
    setHistory(await r.json());
  }

  // Debounced: sliders fire on every drag, and each call is a grid interpolation.
  useEffect(() => {
    if (wfStation === null) return;
    const t = setTimeout(async () => {
      setWfBusy(true);
      try { setWf(await getWhatIf(plant, wfStation, buffer, cycle)); show("Action logged"); }
      finally { setWfBusy(false); }
    }, 220);
    return () => clearTimeout(t);
  }, [plant, wfStation, buffer, cycle]);

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

  return (
    <div className="min-h-screen bg-bg-primary text-txt-primary font-mono text-[13px]">
      <OfflineBanner
        connected={connected}
        lastKnownAt={lastKnownAt}
        onRetry={() => setRetryNonce((n) => n + 1)}
      />
      <header className="flex items-center gap-6 border-b border-bg-surface px-4 py-2">
        <Link href="/" className="font-semibold tracking-wide text-signal-blue">PULSETWIN</Link>
        <span className="text-txt-muted tracking-widest text-[11px]">MANAGER · ANALYSIS</span>
        <select value={plant} onChange={(e) => setPlant(e.target.value)}
                className="bg-bg-card border border-bg-surface px-2 py-1 rounded">
          <option value="plant_a">Plant A — Midwest Assembly</option>
          <option value="plant_b">Plant B — Southern Truck</option>
        </select>
        <StaleDataIndicator timestamp={lastKnownAt} />
        <nav className="ml-auto flex items-center gap-3 text-[11px]">
          <Link href="/supervisor" className="text-txt-muted hover:text-txt-primary">supervisor</Link>
          <Link href="/leadership" className="text-txt-muted hover:text-txt-primary">leadership</Link>
          <Link href="/ops" className="text-txt-muted hover:text-txt-primary">ops</Link>
          <span className="text-txt-muted border-l border-bg-surface pl-3">
            Role: <span className="text-signal-blue">{getRole()}</span>
          </span>
        </nav>
      </header>

      <div className="p-3 space-y-3">
        {/* -------------------------------------------------------- KPI row */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Kpi label="CURRENT CONSTRAINT"
               value={bn ? `S${bn.current_station}` : "—"}
               sub={bn?.current_station_name}
               tone="text-signal-red" />
          <Kpi label="DETECTION ACCURACY"
               value={bn?.detection_accuracy != null ? `${(bn.detection_accuracy * 100).toFixed(1)}%` : "—"}
               sub="vs 2h sustained truth" />
          <Kpi label="CONSTRAINT SHIFTS"
               value={String(shiftCount)}
               sub={`in ${timeline.length} windows`} />
          <Kpi label="FORECAST SKILL"
               value={metrics ? fmtSigned(metrics.values.forecast_skill_vs_persistence) : "—"}
               sub="vs persistence baseline"
               tone={(metrics?.values.forecast_skill_vs_persistence ?? 0) > 0
                 ? "text-signal-green" : "text-signal-amber"} />
        </div>

        {/* ------------------------------------------- bottleneck timeline */}
        <Panel title="CONSTRAINT MIGRATION"
               note="Each point is a 15-minute window. Red = detector disagreed with the 2-hour sustained truth.">
          <ResponsiveContainer width="100%" height={230}>
            <ComposedChart data={timeline}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="t" tick={AXIS} interval={Math.max(1, Math.floor(timeline.length / 8))} />
              <YAxis tick={AXIS} domain={[0, "dataMax + 2"]}
                     label={{ value: "station", angle: -90, position: "insideLeft", fill: "#6b7280", fontSize: 10 }} />
              <Tooltip contentStyle={{ background: "#111827", border: "1px solid #1f2937", fontSize: 11 }}
                       formatter={(v: any, n: any) => [`S${v}`, n === "station" ? "detected" : "truth"]} />
              <Scatter dataKey="station" isAnimationActive={false}>
                {timeline.map((d, i) => (
                  <Cell key={i} fill={d.correct ? "#10b981" : "#ef4444"} />
                ))}
              </Scatter>
              <Line type="stepAfter" dataKey="truth" stroke="#8b5cf6" dot={false}
                    strokeWidth={1.5} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
          <Legend items={[
            { c: "#10b981", l: "detector correct" },
            { c: "#ef4444", l: "detector wrong" },
            { c: "#8b5cf6", l: "sustained truth (2h)" },
          ]} />
        </Panel>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {/* ------------------------------------------ station league table */}
          <Panel title="STATION LEAGUE TABLE" note="Ranked by lost throughput. * = inferred (no sensors).">
            <table className="w-full text-[11px]">
              <thead className="text-txt-muted">
                <tr className="text-left">
                  <th className="py-1">STN</th><th>UTIL</th><th>BLOCKED</th>
                  <th>STARVED</th><th>LOST UPH</th>
                </tr>
              </thead>
              <tbody>
                {league.map((s) => {
                  const inferred = s.provenance === "inferred";
                  return (
                    <tr key={s.station_id} className="border-t border-bg-surface">
                      <td className={`py-1 ${inferred ? "text-signal-amber" : ""}`}>
                        S{s.station_id} {inferred && "*"}
                        <span className="text-txt-muted ml-1">{nameOf(s.station_id).slice(0, 18)}</span>
                      </td>
                      <td>{(s.util * 100).toFixed(1)}%</td>
                      <td className={s.blocked > 0.05 ? "text-signal-red" : ""}>{(s.blocked * 100).toFixed(1)}%</td>
                      <td className={s.starved > 0.05 ? "text-signal-amber" : ""}>{(s.starved * 100).toFixed(1)}%</td>
                      <td>{s.lost_uph.toFixed(1)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Panel>

          {/* --------------------------------------------- root cause pareto */}
          <Panel title="ROOT CAUSE PARETO"
                 note="Permutation importance on the held-out test split — measured, not assumed.">
            <ResponsiveContainer width="100%" height={260}>
              <ComposedChart data={pareto} layout="vertical" margin={{ left: 110 }}>
                <CartesianGrid stroke={GRID} horizontal={false} />
                <XAxis type="number" tick={AXIS} />
                <YAxis type="category" dataKey="feature" tick={{ ...AXIS, fontSize: 9 }} width={110} />
                <Tooltip contentStyle={{ background: "#111827", border: "1px solid #1f2937", fontSize: 11 }} />
                <Bar dataKey="delta" fill="#3b82f6" isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </Panel>
        </div>

        {/* ------------------------------------------------ what-if console */}
        <Panel title="WHAT-IF SIMULATION"
               note="Interpolated from a precomputed grid of short simulations (ADR-007).">
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.2fr] gap-6">
            <div className="space-y-4">
              <label className="block">
                <span className="text-[11px] text-txt-muted">STATION</span>
                <select value={wfStation ?? ""} onChange={(e) => setWfStation(Number(e.target.value))}
                        className="w-full mt-1 bg-bg-surface border border-bg-surface px-2 py-1 rounded">
                  {wfStations.map((s) => (
                    <option key={s.station_id} value={s.station_id}>
                      S{s.station_id} — {s.station_name}
                    </option>
                  ))}
                </select>
              </label>

              <Slider label="FEED BUFFER" value={buffer} min={-2} max={8} step={1}
                      display={`${buffer >= 0 ? "+" : ""}${buffer} slots`}
                      onChange={setBuffer} />
              <Slider label="CYCLE TIME" value={cycle} min={-10} max={10} step={1}
                      display={`${cycle >= 0 ? "+" : ""}${cycle}%`}
                      onChange={setCycle} />

              <p className="text-[10px] text-txt-muted leading-relaxed">
                <span className="text-signal-amber">Feed buffer</span> = capacity
                <em> upstream</em> of the selected station. Adding capacity downstream of a
                constraint does nothing — a bottleneck is never blocked by what follows it.
              </p>
              <p className="text-[10px] text-txt-muted leading-relaxed">
                Only the two constraints and their neighbours are in the grid — 90 simulated
                scenarios per plant rather than 3,510. A manager tunes the bottleneck, not
                station 4.
              </p>
            </div>

            {wf ? (
              <div>
                <div className="grid grid-cols-2 gap-3 mb-3">
                  <Box title="BASELINE" rows={[
                    ["Throughput", `${wf.baseline.throughput_uph.toFixed(1)} UPH`],
                    ["WIP", `${wf.baseline.wip_units.toFixed(0)} units`],
                    ["Bottleneck", `S${wf.baseline.bottleneck_station}`],
                  ]} />
                  <Box title="PROJECTED" highlight rows={[
                    ["Throughput", `${wf.projected.throughput_uph.toFixed(1)} UPH`],
                    ["WIP", `${wf.projected.wip_units.toFixed(0)} units`],
                    ["Bottleneck", `S${wf.projected.bottleneck_station}`],
                  ]} />
                </div>
                <div className="flex flex-wrap gap-x-6 gap-y-1 text-[12px]">
                  <span>
                    Δ Throughput{" "}
                    <b className={wf.delta.throughput_uph >= 0 ? "text-signal-green" : "text-signal-red"}>
                      {wf.delta.throughput_uph >= 0 ? "+" : ""}{wf.delta.throughput_uph.toFixed(2)} UPH
                    </b>
                  </span>
                  <span>
                    Annual value{" "}
                    <b className={wf.annual_value >= 0 ? "text-signal-green" : "text-signal-red"}>
                      ${Math.round(wf.annual_value).toLocaleString()}
                    </b>
                  </span>
                  {wfBusy && <span className="text-txt-muted">recomputing…</span>}
                </div>
                <div className="mt-2 text-[10px] text-txt-muted leading-relaxed">
                  <div>Mode: <span className="text-signal-amber">{wf.mode}</span> · bottleneck resolved by{" "}
                    <span className="text-signal-amber">{wf.bottleneck_mode}</span> — a station ID is
                    categorical and is never interpolated.</div>
                  <div className="mt-1">{wf.assumptions}</div>
                </div>
              </div>
            ) : (
              <div className="text-txt-muted text-[11px] flex items-center">Loading scenario grid…</div>
            )}
          </div>
        </Panel>
      </div>
      {ToastElement}
    </div>
  );
}

function fmtSigned(v: number | undefined) {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(3)}`;
}

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="bg-bg-card border border-bg-surface rounded p-3">
      <div className="text-[10px] text-txt-muted tracking-widest">{label}</div>
      <div className={`text-2xl font-semibold ${tone ?? ""}`}>{value}</div>
      {sub && <div className="text-[10px] text-txt-muted truncate">{sub}</div>}
    </div>
  );
}

function Panel({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <div className="bg-bg-card border border-bg-surface rounded p-3">
      <div className="flex items-baseline justify-between mb-2">
        <h2 className="text-txt-muted tracking-widest text-[11px]">{title}</h2>
        {note && <span className="text-[10px] text-txt-muted">{note}</span>}
      </div>
      {children}
    </div>
  );
}

function Legend({ items }: { items: { c: string; l: string }[] }) {
  return (
    <div className="flex gap-4 text-[10px] text-txt-muted mt-1">
      {items.map((i) => (
        <span key={i.l} className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-sm" style={{ background: i.c }} />
          {i.l}
        </span>
      ))}
    </div>
  );
}

function Slider({ label, value, min, max, step, display, onChange }: {
  label: string; value: number; min: number; max: number; step: number;
  display: string; onChange: (v: number) => void;
}) {
  return (
    <label className="block">
      <div className="flex justify-between text-[11px]">
        <span className="text-txt-muted">{label}</span>
        <span className="text-signal-blue">{display}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
             onChange={(e) => onChange(Number(e.target.value))}
             className="w-full accent-signal-blue" />
    </label>
  );
}

function Box({ title, rows, highlight }: { title: string; rows: [string, string][]; highlight?: boolean }) {
  return (
    <div className={`border rounded p-2 ${highlight ? "border-signal-blue/50 bg-signal-blue/5" : "border-bg-surface"}`}>
      <div className="text-[10px] text-txt-muted tracking-widest mb-1">{title}</div>
      {rows.map(([k, v]) => (
        <div key={k} className="flex justify-between text-[11px]">
          <span className="text-txt-muted">{k}</span>
          <span>{v}</span>
        </div>
      ))}
    </div>
  );
}
