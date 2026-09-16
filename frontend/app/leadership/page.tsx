"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Metrics, getHealth, getInstrumentationPlan, getMetrics, getRole, getRoi, setRole } from "@/lib/api";
import OfflineBanner from "@/components/shared/OfflineBanner";
import StaleDataIndicator from "@/components/shared/StaleDataIndicator";

type Scenario = "pessimistic" | "base" | "optimistic";

const AXIS = { stroke: "#6b7280", fontSize: 10, fontFamily: "monospace" };

export default function LeadershipPage() {
  const [plant, setPlant] = useState("plant_a");
  const [scenario, setScenario] = useState<Scenario>("base");
  const [roi, setRoi] = useState<any>(null);
  const [plan, setPlan] = useState<any>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [showMetrics, setShowMetrics] = useState(false);
  const [connected, setConnected] = useState(true);
  const [lastKnownAt, setLastKnownAt] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);

  // Identifies this view to the backend RBAC (serve/middleware/auth.py) instead of
  // relying on lib/api.ts's global "leadership" default.
  useEffect(() => { setRole("leadership"); }, []);

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
    getRoi(plant, scenario).then(setRoi).catch(() => setRoi(null));
    getInstrumentationPlan(plant).then(setPlan).catch(() => setPlan(null));
    getMetrics(plant).then(setMetrics).catch(() => setMetrics(null));
  }, [plant, scenario]);

  // Waterfall from a stacked bar with a transparent base — Recharts has no native one.
  const bridge = useMemo(() => {
    if (!roi) return [];
    let run = 0;
    const rows = roi.components.map((c: any) => {
      const base = c.value >= 0 ? run : run + c.value;
      const row = { label: c.label, base, delta: Math.abs(c.value), value: c.value, source: c.source };
      run += c.value;
      return row;
    });
    rows.push({ label: "System cost", base: run - roi.system_cost, delta: roi.system_cost,
                value: -roi.system_cost, source: "Implementation + year one" });
    run -= roi.system_cost;
    rows.push({ label: "NET", base: 0, delta: Math.abs(run), value: run, source: "Sum of the above" });
    return rows;
  }, [roi]);

  const money = (v: number) =>
    `${v < 0 ? "-" : ""}$${Math.abs(v) >= 1e6 ? (Math.abs(v) / 1e6).toFixed(2) + "M" : Math.round(Math.abs(v)).toLocaleString()}`;

  return (
    <div className="min-h-screen bg-bg-primary text-txt-primary font-mono text-[13px]">
      <OfflineBanner
        connected={connected}
        lastKnownAt={lastKnownAt}
        onRetry={() => setRetryNonce((n) => n + 1)}
      />
      <header className="flex items-center gap-6 border-b border-bg-surface px-4 py-2">
        <Link href="/" className="font-semibold tracking-wide text-signal-blue">PULSETWIN</Link>
        <span className="text-txt-muted tracking-widest text-[11px]">LEADERSHIP · INVESTMENT</span>
        <select value={plant} onChange={(e) => setPlant(e.target.value)}
                className="bg-bg-card border border-bg-surface px-2 py-1 rounded">
          <option value="plant_a">Plant A — Midwest Assembly</option>
          <option value="plant_b">Plant B — Southern Truck</option>
        </select>
        <StaleDataIndicator timestamp={lastKnownAt} />
        <div className="flex gap-1">
          {(["pessimistic", "base", "optimistic"] as Scenario[]).map((s) => (
            <button key={s} onClick={() => setScenario(s)}
                    className={`px-2 py-0.5 rounded border text-[11px] ${
                      scenario === s ? "border-signal-purple text-signal-purple"
                                     : "border-bg-surface text-txt-muted hover:text-txt-primary"}`}>
              {s}
            </button>
          ))}
        </div>
        <nav className="ml-auto flex items-center gap-3 text-[11px]">
          <Link href="/supervisor" className="text-txt-muted hover:text-txt-primary">supervisor</Link>
          <Link href="/manager" className="text-txt-muted hover:text-txt-primary">manager</Link>
          <Link href="/ops" className="text-txt-muted hover:text-txt-primary">ops</Link>
          <span className="text-txt-muted border-l border-bg-surface pl-3">
            Role: <span className="text-signal-purple">{getRole()}</span>
          </span>
        </nav>
      </header>

      <div className="p-3 space-y-3">
        {!roi && <div className="text-txt-muted text-[11px]">Loading…</div>}

        {roi && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Kpi label="NET VALUE" value={`${money(roi.net_value)}/yr`}
                   sub={`${scenario} scenario`} tone="text-signal-green" />
              <Kpi label="SYSTEM ROI"
                   value={roi.roi_pct != null ? `${Math.round(roi.roi_pct)}%` : "—"}
                   sub={`on ${money(roi.system_cost)} invested`} />
              <Kpi label="PAYBACK"
                   value={roi.payback_months != null ? `${roi.payback_months.toFixed(1)} mo` : "—"}
                   sub="from measured recall" />
              <Kpi label="DEFECTS CAUGHT"
                   value={`${Math.round(roi.basis.units_per_year * roi.basis.defect_rate * roi.basis.recall_at_budget).toLocaleString()}/yr`}
                   sub={`${(roi.basis.recall_at_budget * 100).toFixed(0)}% recall at budget`} />
            </div>

            {/* ---------------------------------------------- the honest basis */}
            <Panel title="HOW THIS IS DERIVED"
                   note="No assumed catch rate. Every figure traces to a measurement or a sourced constant.">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-[11px]">
                <Fact k="Units / year" v={Math.round(roi.basis.units_per_year).toLocaleString()}
                      note={`${roi.basis.achieved_uph.toFixed(1)} UPH achieved × 3 shifts × 250 days`} />
                <Fact k="Defect rate" v={`${(roi.basis.defect_rate * 100).toFixed(2)}%`}
                      note="measured on the held-out test week" />
                <Fact k="Recall at budget" v={`${(roi.basis.recall_at_budget * 100).toFixed(0)}%`}
                      note="at 6 alerts/shift, not at the cost optimum" />
                <Fact k="Escape rate" v={`${(roi.basis.escape_rate * 100).toFixed(1)}%`}
                      note="defects inspection misses → warranty exposure" />
              </div>
            </Panel>

            {/* ------------------------------------------------ value bridge */}
            <Panel title="VALUE BRIDGE" note="Hover any bar for its source.">
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={bridge} margin={{ bottom: 40 }}>
                  <CartesianGrid stroke="#1f2937" vertical={false} />
                  <XAxis dataKey="label" tick={{ ...AXIS, fontSize: 9 }} angle={-20}
                         textAnchor="end" interval={0} height={50} />
                  <YAxis tick={AXIS} tickFormatter={(v) => `${(v / 1e6).toFixed(1)}M`} />
                  <Tooltip
                    contentStyle={{ background: "#111827", border: "1px solid #1f2937", fontSize: 11, maxWidth: 320 }}
                    formatter={(_v: any, _n: any, p: any) => [money(p.payload.value), p.payload.label]}
                    labelFormatter={(l: any) => {
                      const row = bridge.find((b: any) => b.label === l);
                      return row?.source ? `Source: ${row.source}` : l;
                    }}
                  />
                  <Bar dataKey="base" stackId="a" fill="transparent" isAnimationActive={false} />
                  <Bar dataKey="delta" stackId="a" isAnimationActive={false}>
                    {bridge.map((b: any, i: number) => (
                      <Cell key={i} fill={b.label === "NET" ? "#3b82f6" : b.value >= 0 ? "#10b981" : "#ef4444"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </Panel>

            {/* --------------------------------- the alarm budget trade-off */}
            <Panel title="THE BINDING CONSTRAINT IS HUMAN ATTENTION"
                   note="The two thresholds disagree by two orders of magnitude. Both are published.">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-[12px]">
                <div className="border border-bg-surface rounded p-3">
                  <div className="text-[10px] text-txt-muted tracking-widest mb-1">COST-OPTIMAL</div>
                  <div className="text-xl font-semibold text-signal-amber">
                    p ≥ {metrics?.values.cost_optimal_threshold?.toFixed(4) ?? "—"}
                  </div>
                  <p className="text-[10px] text-txt-muted mt-1 leading-relaxed">
                    A miss costs ~155× a false alarm, so the economics say alarm on almost
                    everything — hundreds per shift. Arithmetically correct, operationally
                    impossible.
                  </p>
                </div>
                <div className="border border-signal-green/40 bg-signal-green/5 rounded p-3">
                  <div className="text-[10px] text-txt-muted tracking-widest mb-1">WE OPERATE HERE</div>
                  <div className="text-xl font-semibold text-signal-green">
                    p ≥ {metrics?.values.budget_threshold?.toFixed(3) ?? "—"}
                  </div>
                  <p className="text-[10px] text-txt-muted mt-1 leading-relaxed">
                    {metrics ? `${(metrics.values.precision_at_budget * 100).toFixed(0)}% precision, ${(metrics.values.recall_at_budget * 100).toFixed(0)}% recall` : "—"}
                    {" "}at ~6 alerts/shift. Wickens (2008): supervisor response degrades beyond 6–8.
                  </p>
                </div>
                <div className="border border-bg-surface rounded p-3">
                  <div className="text-[10px] text-txt-muted tracking-widest mb-1">DECISION AVAILABLE</div>
                  <div className="text-xl font-semibold text-signal-blue">
                    +{money(roi.marginal_value_of_doubling_budget ?? 0)}
                  </div>
                  <p className="text-[10px] text-txt-muted mt-1 leading-relaxed">
                    Value of doubling the alert budget, against{" "}
                    {money(roi.marginal_cost_of_doubling_budget ?? 0)} of extra response cost.
                    A real staffing decision with a real price.
                  </p>
                </div>
              </div>
            </Panel>

            {/* ------------------------------------ instrumentation planner */}
            {plan && (
              <Panel title="INSTRUMENTATION ROI PLANNER" note={plan.method}>
                <table className="w-full text-[11px]">
                  <thead className="text-txt-muted">
                    <tr className="text-left">
                      <th className="py-1">STATION</th><th>SENSOR</th><th>COST</th>
                      <th>ΔPR-AUC</th><th>VALUE/YR</th><th>PAYBACK</th><th>WINDOW</th><th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {plan.plan.map((r: any) => (
                      <tr key={r.station_id} className="border-t border-bg-surface">
                        <td className="py-1">S{r.station_id} <span className="text-txt-muted">{r.station_name}</span></td>
                        <td className="text-txt-muted">{r.sensor_type.replace(/_/g, " ")}</td>
                        <td>${r.cost.toLocaleString()}</td>
                        <td className={r.delta_pr_auc > 0 ? "text-signal-green" : "text-txt-muted"}>
                          {r.delta_pr_auc > 0 ? "+" : ""}{r.delta_pr_auc.toFixed(4)}
                        </td>
                        <td>{money(r.annual_value)}</td>
                        <td>{r.payback_months != null ? `${r.payback_months.toFixed(1)} mo` : "—"}</td>
                        <td className="text-txt-muted">{r.window}</td>
                        <td className={
                          r.recommendation === "PROCEED" ? "text-signal-green"
                            : r.recommendation === "DEFER - no measured model benefit" ? "text-txt-muted"
                            : "text-signal-amber"}>
                          {r.recommendation}
                        </td>
                      </tr>
                    ))}
                    <tr className="border-t-2 border-bg-surface font-semibold">
                      <td className="py-1">TOTAL</td><td></td>
                      <td>${plan.total_investment.toLocaleString()}</td><td></td>
                      <td>{money(plan.total_annual_value)}</td><td></td><td></td><td></td>
                    </tr>
                  </tbody>
                </table>
              </Panel>
            )}

            {/* ------------------------------------------ rollout timeline */}
            <Panel title="ROLLOUT" note="Shadow mode first. The twin never writes to a production system.">
              <div className="grid grid-cols-1 md:grid-cols-4 gap-3 text-[11px]">
                {[
                  { p: "Phase 1", w: "8 weeks", t: "Shadow mode", items: ["Predict silently", "Measure precision vs real inspection", "Zero operator exposure", "Tune the alarm budget"] },
                  { p: "Phase 2", w: "12 weeks", t: "Advisory alerts", items: ["One line", "Supervisor UI live", "Feedback loop on", "Containment trialled"] },
                  { p: "Phase 3", w: "16 weeks", t: "MES integration", items: ["Quality holds", "Auto-containment lists", "Manager + Leadership views", "Phase 1 retrofit"] },
                  { p: "Phase 4", w: "ongoing", t: "Multi-plant", items: ["Plant B onboard", "Config-only adaptation", "Per-site models", "Central dashboard"] },
                ].map((ph) => (
                  <div key={ph.p} className="border border-bg-surface rounded p-3">
                    <div className="text-signal-purple font-semibold">{ph.p}</div>
                    <div className="text-txt-muted text-[10px] mb-1">{ph.w}</div>
                    <div className="mb-2">{ph.t}</div>
                    <ul className="space-y-0.5 text-[10px] text-txt-muted">
                      {ph.items.map((i) => <li key={i}>├─ {i}</li>)}
                    </ul>
                  </div>
                ))}
              </div>
            </Panel>

            {/* ------------------------------------------ model metrics panel */}
            <div className="bg-bg-card border border-bg-surface rounded p-3">
              <button onClick={() => setShowMetrics((s) => !s)}
                      className="text-txt-muted tracking-widest text-[11px] hover:text-txt-primary">
                {showMetrics ? "▼" : "▶"} MODEL METRICS — the numbers behind every figure above
              </button>
              {showMetrics && metrics && (
                <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-3 text-[11px]">
                  <Metric k="PR-AUC" v={metrics.values.pr_auc?.toFixed(3)}
                          note={`95% CI ${metrics.values.pr_auc_ci_low?.toFixed(3)}–${metrics.values.pr_auc_ci_high?.toFixed(3)}`} />
                  <Metric k="Oracle ceiling" v={metrics.values.oracle_pr_auc?.toFixed(3)}
                          note="simulator's own latent propensity — no model can beat it" highlight />
                  <Metric k="Captured" v={`${((metrics.values.oracle_capture ?? 0) * 100).toFixed(0)}%`}
                          note="our PR-AUC as a share of that ceiling" highlight />
                  <Metric k="ROC-AUC" v={metrics.values.roc_auc?.toFixed(3)} />
                  <Metric k="Brier" v={metrics.values.brier?.toFixed(4)} note="calibration error" />
                  <Metric k="Bottleneck accuracy" v={`${((metrics.values.bottleneck_detection_accuracy ?? 0) * 100).toFixed(1)}%`}
                          note="vs a 2h sustained-truth definition" />
                  <Metric k="Forecast skill" v={metrics.values.forecast_skill_vs_persistence?.toFixed(3)}
                          note="vs persistence baseline" />
                  <Metric k="Soft-sensor ablation" v={`${(metrics.values.soft_sensor_ablation_delta ?? 0) >= 0 ? "+" : ""}${metrics.values.soft_sensor_ablation_delta?.toFixed(4)}`}
                          note="PR-AUC from unsensored-station evidence" />
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="bg-bg-card border border-bg-surface rounded p-3">
      <div className="text-[10px] text-txt-muted tracking-widest">{label}</div>
      <div className={`text-2xl font-semibold ${tone ?? ""}`}>{value}</div>
      {sub && <div className="text-[10px] text-txt-muted">{sub}</div>}
    </div>
  );
}

function Panel({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <div className="bg-bg-card border border-bg-surface rounded p-3">
      <div className="flex items-baseline justify-between gap-4 mb-2">
        <h2 className="text-txt-muted tracking-widest text-[11px] shrink-0">{title}</h2>
        {note && <span className="text-[10px] text-txt-muted text-right">{note}</span>}
      </div>
      {children}
    </div>
  );
}

function Fact({ k, v, note }: { k: string; v: string; note: string }) {
  return (
    <div>
      <div className="text-txt-muted text-[10px]">{k}</div>
      <div className="text-[15px] font-semibold">{v}</div>
      <div className="text-[9px] text-txt-muted leading-tight">{note}</div>
    </div>
  );
}

function Metric({ k, v, note, highlight }: { k: string; v?: string; note?: string; highlight?: boolean }) {
  return (
    <div className={highlight ? "border-l-2 border-signal-purple pl-2" : ""}>
      <div className="text-txt-muted text-[10px]">{k}</div>
      <div className="text-[15px] font-semibold">{v ?? "—"}</div>
      {note && <div className="text-[9px] text-txt-muted leading-tight">{note}</div>}
    </div>
  );
}
