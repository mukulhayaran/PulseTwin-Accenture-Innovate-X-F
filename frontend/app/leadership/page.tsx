"use client";

import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Tooltip as RTooltip, XAxis, YAxis } from "recharts";
import { Metrics, getHealth, getInstrumentationPlan, getMetrics, getRole, getRoi, setRole } from "@/lib/api";
import { cn } from "@/lib/cn";
import { axisProps, gridProps, theme } from "@/lib/theme";
import StaleDataIndicator from "@/components/shared/StaleDataIndicator";
import {
  AppShell, Badge, ChartFrame, ChartTooltip, DataTable, Fact, Kpi, Panel, SegmentedControl,
  SkeletonChart, SkeletonStats, SkeletonTable, Tooltip, type Column,
} from "@/components/ui";

type Scenario = "pessimistic" | "base" | "optimistic";

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

  const planCols: Column<any>[] = [
    {
      key: "station", header: "Station",
      cell: (r) => (
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="text-numeric font-medium text-ink">S{r.station_id}</span>
          <span className="truncate text-ink-faint">{r.station_name}</span>
        </span>
      ),
    },
    { key: "sensor", header: "Sensor", cell: (r) => <span className="text-ink-dim">{r.sensor_type.replace(/_/g, " ")}</span> },
    { key: "cost", header: "Cost", align: "right", cell: (r) => <span className="text-numeric text-ink-dim">${r.cost.toLocaleString()}</span> },
    {
      key: "delta", header: "Δ PR-AUC", align: "right",
      cell: (r) => (
        <span className={cn("text-numeric", r.delta_pr_auc > 0 ? "text-ok-fg" : "text-ink-faint")}>
          {r.delta_pr_auc > 0 ? "+" : ""}{r.delta_pr_auc.toFixed(4)}
        </span>
      ),
    },
    { key: "value", header: "Value/yr", align: "right", cell: (r) => <span className="text-numeric text-ink">{money(r.annual_value)}</span> },
    {
      key: "payback", header: "Payback", align: "right",
      cell: (r) => (
        <span className="text-numeric text-ink-dim">
          {r.payback_months != null ? `${r.payback_months.toFixed(1)} mo` : "—"}
        </span>
      ),
    },
    { key: "window", header: "Window", cell: (r) => <span className="text-2xs text-ink-faint">{r.window}</span> },
    {
      key: "rec", header: "", align: "right",
      cell: (r) => (
        <Badge
          variant={
            r.recommendation === "PROCEED" ? "ok"
              : r.recommendation.startsWith("DEFER") ? "neutral" : "warn"
          }
        >
          {r.recommendation.startsWith("DEFER") ? "Defer" : r.recommendation}
        </Badge>
      ),
    },
  ];

  return (
    <AppShell
      persona="Investment"
      plant={plant}
      onPlantChange={setPlant}
      role={getRole()}
      connected={connected}
      lastKnownAt={lastKnownAt}
      onRetry={() => setRetryNonce((n) => n + 1)}
      controls={
        <SegmentedControl
          name="scenario"
          aria-label="ROI scenario"
          value={scenario}
          onChange={setScenario}
          options={[
            { value: "pessimistic" as Scenario, label: "Pessimistic" },
            { value: "base" as Scenario, label: "Base" },
            { value: "optimistic" as Scenario, label: "Optimistic" },
          ]}
        />
      }
      status={<StaleDataIndicator timestamp={lastKnownAt} />}
    >
      <div className="space-y-4">
        {!roi ? (
          <>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              {[0, 1, 2, 3].map((i) => (
                <Panel key={i} bodyClassName="p-4">
                  <SkeletonStats count={1} />
                </Panel>
              ))}
            </div>
            <Panel title="Value bridge"><SkeletonChart height={260} /></Panel>
          </>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Kpi label="Net value" value={`${money(roi.net_value)}/yr`} sub={`${scenario} scenario`} tone="ok" accent />
              <Kpi
                label="System ROI"
                value={roi.roi_pct != null ? `${Math.round(roi.roi_pct)}%` : "—"}
                sub={`on ${money(roi.system_cost)} invested`}
              />
              <Kpi
                label="Payback"
                value={roi.payback_months != null ? `${roi.payback_months.toFixed(1)} mo` : "—"}
                sub="from measured recall"
              />
              <Kpi
                label="Defects caught"
                value={`${Math.round(roi.basis.units_per_year * roi.basis.defect_rate * roi.basis.recall_at_budget).toLocaleString()}/yr`}
                sub={`${(roi.basis.recall_at_budget * 100).toFixed(0)}% recall at budget`}
              />
            </div>

            {/* --------------------------------------------- the honest basis */}
            <Panel
              title="How this is derived"
              note="No assumed catch rate. Every figure traces to a measurement or a sourced constant."
            >
              <div className="grid grid-cols-2 gap-5 md:grid-cols-4">
                <Fact
                  label="Units / year"
                  value={Math.round(roi.basis.units_per_year).toLocaleString()}
                  note={`${roi.basis.achieved_uph.toFixed(1)} UPH achieved × 3 shifts × 250 days`}
                />
                <Fact
                  label="Defect rate"
                  value={`${(roi.basis.defect_rate * 100).toFixed(2)}%`}
                  note="measured on the held-out test week"
                />
                <Fact
                  label="Recall at budget"
                  value={`${(roi.basis.recall_at_budget * 100).toFixed(0)}%`}
                  note="at 6 alerts/shift, not at the cost optimum"
                />
                <Fact
                  label="Escape rate"
                  value={`${(roi.basis.escape_rate * 100).toFixed(1)}%`}
                  note="defects inspection misses → warranty exposure"
                />
              </div>
            </Panel>

            {/* ----------------------------------------------- value bridge */}
            <Panel title="Value bridge" note="Hover any bar for its source.">
              <ChartFrame height={272}>
                <BarChart data={bridge} margin={{ bottom: 44, top: 4, right: 8, left: -4 }}>
                  <CartesianGrid {...gridProps} />
                  <XAxis
                    dataKey="label" {...axisProps} angle={-20} textAnchor="end" interval={0} height={54}
                    tick={{ ...axisProps.tick, fontSize: 9 }}
                  />
                  <YAxis {...axisProps} width={48} tickFormatter={(v) => `${(v / 1e6).toFixed(1)}M`} />
                  <RTooltip
                    cursor={{ fill: theme.surface2 }}
                    content={
                      <ChartTooltip
                        title={(l) => String(l)}
                        rows={(p) => {
                          const row: any = p[0]?.payload ?? {};
                          return (
                            <div className="flex items-center justify-between gap-4 text-xs">
                              <span className="text-ink-dim">Contribution</span>
                              <span
                                className={cn(
                                  "text-numeric font-semibold",
                                  row.value >= 0 ? "text-ok-fg" : "text-danger-fg"
                                )}
                              >
                                {money(row.value ?? 0)}
                              </span>
                            </div>
                          );
                        }}
                        // Sourced economics, CLAUDE.md §3: no dollar figure without
                        // the assumption behind it.
                        footer={(p) => {
                          const row: any = p[0]?.payload ?? {};
                          return row.source ? `Source: ${row.source}` : null;
                        }}
                      />
                    }
                  />
                  <Bar dataKey="base" stackId="a" fill="transparent" isAnimationActive={false} />
                  <Bar dataKey="delta" stackId="a" radius={[3, 3, 0, 0]} isAnimationActive={false}>
                    {bridge.map((b: any, i: number) => (
                      <Cell
                        key={i}
                        fill={b.label === "NET" ? theme.info : b.value >= 0 ? theme.ok : theme.danger}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ChartFrame>
            </Panel>

            {/* ------------------------------ the alarm budget trade-off */}
            <Panel
              title="The binding constraint is human attention"
              note="The two thresholds disagree by two orders of magnitude. Both are published."
            >
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <ThresholdCard
                  label="Cost-optimal"
                  value={`p ≥ ${metrics?.values.cost_optimal_threshold?.toFixed(4) ?? "—"}`}
                  tone="warn"
                  body="A miss costs ~155× a false alarm, so the economics say alarm on almost everything — hundreds per shift. Arithmetically correct, operationally impossible."
                />
                <ThresholdCard
                  label="We operate here"
                  value={`p ≥ ${metrics?.values.budget_threshold?.toFixed(3) ?? "—"}`}
                  tone="ok"
                  selected
                  body={
                    <>
                      {metrics
                        ? `${(metrics.values.precision_at_budget * 100).toFixed(0)}% precision, ${(metrics.values.recall_at_budget * 100).toFixed(0)}% recall`
                        : "—"}{" "}
                      at ~6 alerts/shift. Wickens (2008): supervisor response degrades beyond 6–8.
                    </>
                  }
                />
                <ThresholdCard
                  label="Decision available"
                  value={`+${money(roi.marginal_value_of_doubling_budget ?? 0)}`}
                  tone="info"
                  body={
                    <>
                      Value of doubling the alert budget, against{" "}
                      {money(roi.marginal_cost_of_doubling_budget ?? 0)} of extra response cost. A
                      real staffing decision with a real price.
                    </>
                  }
                />
              </div>
            </Panel>

            {/* ------------------------------- instrumentation planner */}
            <Panel title="Instrumentation ROI planner" note={plan?.method}>
              {!plan ? (
                <SkeletonTable rows={5} cols={8} />
              ) : (
                <DataTable
                  columns={planCols}
                  rows={plan.plan}
                  rowKey={(r) => String(r.station_id)}
                  footer={
                    <>
                      <td className="py-2 text-xs font-semibold text-ink">Total</td>
                      <td />
                      <td className="text-numeric py-2 pr-3 text-right text-xs text-ink">
                        ${plan.total_investment.toLocaleString()}
                      </td>
                      <td />
                      <td className="text-numeric py-2 pr-3 text-right text-xs text-ok-fg">
                        {money(plan.total_annual_value)}
                      </td>
                      <td colSpan={3} />
                    </>
                  }
                />
              )}
            </Panel>

            {/* ------------------------------------- rollout timeline */}
            <Panel title="Rollout" note="Shadow mode first. The twin never writes to a production system.">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {[
                  { p: "Phase 1", w: "8 weeks", t: "Shadow mode", items: ["Predict silently", "Measure precision vs real inspection", "Zero operator exposure", "Tune the alarm budget"] },
                  { p: "Phase 2", w: "12 weeks", t: "Advisory alerts", items: ["One line", "Supervisor UI live", "Feedback loop on", "Containment trialled"] },
                  { p: "Phase 3", w: "16 weeks", t: "MES integration", items: ["Quality holds", "Auto-containment lists", "Manager + Leadership views", "Phase 1 retrofit"] },
                  { p: "Phase 4", w: "ongoing", t: "Multi-plant", items: ["Plant B onboard", "Config-only adaptation", "Per-site models", "Central dashboard"] },
                ].map((ph, i) => (
                  <div
                    key={ph.p}
                    className="relative rounded-lg border border-line bg-surface-2/50 p-3.5 transition-colors hover:border-line-strong"
                  >
                    <span
                      aria-hidden
                      className="absolute inset-x-3.5 top-0 h-0.5 rounded-b-full bg-ai"
                      style={{ opacity: 1 - i * 0.2 }}
                    />
                    <div className="text-xs font-semibold text-ai-fg">{ph.p}</div>
                    <div className="text-numeric text-2xs text-ink-faint">{ph.w}</div>
                    <div className="mb-2.5 mt-1.5 text-xs font-medium text-ink">{ph.t}</div>
                    <ul className="space-y-1">
                      {ph.items.map((it) => (
                        <li key={it} className="flex gap-1.5 text-2xs leading-relaxed text-ink-faint">
                          <span className="text-ai/60">─</span>
                          {it}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </Panel>

            {/* ------------------------------------- model metrics panel */}
            <Panel
              title={
                <button
                  onClick={() => setShowMetrics((s) => !s)}
                  className="flex items-center gap-1.5 rounded text-2xs font-semibold uppercase tracking-[0.14em] text-ink-faint transition-colors hover:text-ink"
                  aria-expanded={showMetrics}
                >
                  <span className={cn("transition-transform duration-200", showMetrics && "rotate-90")}>
                    ▶
                  </span>
                  Model metrics — the numbers behind every figure above
                </button>
              }
              bodyClassName={showMetrics ? "p-4" : "p-0"}
            >
              {showMetrics && metrics && (
                <div className="grid grid-cols-2 gap-5 md:grid-cols-4">
                  <Fact
                    label="PR-AUC" value={metrics.values.pr_auc?.toFixed(3) ?? "—"}
                    note={`95% CI ${metrics.values.pr_auc_ci_low?.toFixed(3)}–${metrics.values.pr_auc_ci_high?.toFixed(3)}`}
                  />
                  <Fact
                    label="Oracle ceiling" value={metrics.values.oracle_pr_auc?.toFixed(3) ?? "—"}
                    note="simulator's own latent propensity — no model can beat it" accent
                  />
                  <Fact
                    label="Captured" value={`${((metrics.values.oracle_capture ?? 0) * 100).toFixed(0)}%`}
                    note="our PR-AUC as a share of that ceiling" accent
                  />
                  <Fact label="ROC-AUC" value={metrics.values.roc_auc?.toFixed(3) ?? "—"} />
                  <Fact label="Brier" value={metrics.values.brier?.toFixed(4) ?? "—"} note="calibration error" />
                  <Fact
                    label="Bottleneck accuracy"
                    value={`${((metrics.values.bottleneck_detection_accuracy ?? 0) * 100).toFixed(1)}%`}
                    note="vs a 2h sustained-truth definition"
                  />
                  <Fact
                    label="Forecast skill"
                    value={metrics.values.forecast_skill_vs_persistence?.toFixed(3) ?? "—"}
                    note="vs persistence baseline"
                  />
                  <Fact
                    label="Soft-sensor ablation"
                    value={`${(metrics.values.soft_sensor_ablation_delta ?? 0) >= 0 ? "+" : ""}${metrics.values.soft_sensor_ablation_delta?.toFixed(4)}`}
                    note="PR-AUC from unsensored-station evidence"
                  />
                </div>
              )}
            </Panel>
          </>
        )}
      </div>
    </AppShell>
  );
}

function ThresholdCard({
  label, value, body, tone, selected,
}: {
  label: string;
  value: string;
  body: React.ReactNode;
  tone: "ok" | "warn" | "info";
  selected?: boolean;
}) {
  const TONE = {
    ok: { text: "text-ok-fg", ring: "border-ok/40 bg-ok/[0.07]" },
    warn: { text: "text-warn-fg", ring: "border-line bg-surface-2/50" },
    info: { text: "text-info-fg", ring: "border-line bg-surface-2/50" },
  }[tone];

  return (
    <div className={cn("relative rounded-lg border p-3.5", TONE.ring)}>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-faint">{label}</span>
        {selected && <Badge variant="ok">Active</Badge>}
      </div>
      <div className={cn("text-numeric text-xl font-semibold", TONE.text)}>{value}</div>
      <p className="mt-2 text-2xs leading-relaxed text-ink-faint">{body}</p>
    </div>
  );
}
