"use client";

// The compact intervention comparison for one dynamic simulation experiment (ADR-064).
// Baseline row: absolute values. Every other row: the PAIRED change against that baseline
// on identical seeds, with its 95% interval. Every figure is simulated, so every one wears
// the Inferred treatment (contract #1). The verdict column is the backend's deterministic
// rule -- does the interval clear zero in the better direction -- not a judgement made here.

import { useEffect, useState } from "react";
import {
  ExperimentScenario, SimInterval, SimMetric, SimulationExperiment, getSimulationExperiment,
} from "@/lib/api";
import { Badge, DataTable, InferredBadge, Tooltip, type Column } from "@/components/ui";

const ACTIVE = new Set(["queued", "running"]);
const OBJECTIVE_METRIC: Record<string, SimMetric> = {
  throughput: "throughput_uph", starvation: "starved_pct", blocking: "blocked_pct",
  downtime: "down_pct", quality: "expected_defects", wip: "wip_mean",
};
const LOWER_BETTER = new Set<SimMetric>(["wip_mean", "blocked_pct", "starved_pct", "down_pct",
                                         "downtime_s", "expected_defects", "alerts"]);
const VERDICT: Record<string, { text: string; tone: "ok" | "warn" | "neutral" }> = {
  improved: { text: "improvement clear of zero", tone: "ok" },
  too_uncertain: { text: "too uncertain", tone: "warn" },
  no_improvement: { text: "no improvement", tone: "neutral" },
  no_scenarios: { text: "nothing runnable", tone: "neutral" },
};

function fmt(v: number, digits: number, signed: boolean) {
  const s = v.toFixed(digits);
  return signed && v > 0 ? `+${s}` : s;
}

function Cell({ iv, digits, signed, unit }: {
  iv: SimInterval | undefined | null; digits: number; signed: boolean; unit?: string;
}) {
  if (!iv) return <span className="text-ink-faint">—</span>;
  return (
    <Tooltip content={`${signed ? "Paired change" : "Mean"} over ${iv.n} seeds · 95% interval ` +
      `${iv.ci_low.toFixed(digits)} to ${iv.ci_high.toFixed(digits)} · seeds p10–p90 ` +
      `${iv.p10.toFixed(digits)} to ${iv.p90.toFixed(digits)} · simulated (inferred)`}>
      <span className="text-numeric inline-flex cursor-help flex-col items-end leading-tight">
        <span className="text-xs text-ink">{fmt(iv.mean, digits, signed)}{unit ?? ""}</span>
        <span className="whitespace-nowrap text-2xs text-ink-faint">
          [{fmt(iv.ci_low, digits, signed)}, {fmt(iv.ci_high, digits, signed)}]
        </span>
      </span>
    </Tooltip>
  );
}

function confidence(s: ExperimentScenario, metric: SimMetric) {
  if (!s.supported) return <Badge variant="neutral">not simulated</Badge>;
  if (s.is_baseline) return <span className="text-2xs text-ink-faint">{s.seeds_ok} seeds</span>;
  const d = s.vs_baseline?.[metric];
  if (!d) return <span className="text-2xs text-ink-faint">no result</span>;
  const clear = LOWER_BETTER.has(metric) ? d.ci_high < 0 : d.ci_low > 0;
  const worse = LOWER_BETTER.has(metric) ? d.ci_low > 0 : d.ci_high < 0;
  return clear ? <Badge variant="ok">clear gain</Badge>
    : worse ? <Badge variant="danger">clear loss</Badge>
    : <Badge variant="neutral">overlaps zero</Badge>;
}

export default function ExperimentComparison({ experimentId }: { experimentId: string }) {
  const [exp, setExp] = useState<SimulationExperiment | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = () =>
      getSimulationExperiment(experimentId)
        .then((e) => {
          if (cancelled) return;
          setExp(e);
          if (ACTIVE.has(e.status)) timer = setTimeout(tick, 3000);
        })
        .catch((e) => !cancelled && setErr(String(e)));
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [experimentId]);

  if (err) return <p className="text-2xs text-danger-fg">Experiment {experimentId}: {err}</p>;
  if (!exp) return <p className="text-2xs text-ink-faint">Loading experiment {experimentId}…</p>;

  const metric = OBJECTIVE_METRIC[exp.objective] ?? "throughput_uph";
  const v = exp.verdict ? VERDICT[exp.verdict] : null;
  const val = (s: ExperimentScenario, m: SimMetric) =>
    s.is_baseline ? s.metrics[m] : s.vs_baseline?.[m];

  const cols: Column<ExperimentScenario>[] = [
    {
      key: "label", header: "Intervention",
      cell: (s) => (
        <Tooltip content={s.supported ? (s.simulated_as ?? "") : (s.reason ?? "not supported")}>
          <span className="cursor-help text-xs text-ink">
            {s.is_baseline ? "Baseline (no change)" : s.label}
            {!s.supported && <span className="ml-1 text-2xs text-ink-faint">— {s.reason}</span>}
          </span>
        </Tooltip>
      ),
    },
    { key: "uph", header: "Throughput (UPH)", align: "right",
      cell: (s) => <Cell iv={val(s, "throughput_uph")} digits={2} signed={!s.is_baseline} /> },
    { key: "starved", header: "Starved", align: "right",
      cell: (s) => <Cell iv={val(s, "starved_pct")} digits={1} signed={!s.is_baseline} unit="%" /> },
    { key: "blocked", header: "Blocked", align: "right",
      cell: (s) => <Cell iv={val(s, "blocked_pct")} digits={1} signed={!s.is_baseline} unit="%" /> },
    { key: "wip", header: "WIP", align: "right",
      cell: (s) => <Cell iv={val(s, "wip_mean")} digits={1} signed={!s.is_baseline} /> },
    { key: "defects", header: "Defect impact", align: "right",
      cell: (s) => <Cell iv={val(s, "expected_defects")} digits={3} signed={!s.is_baseline} /> },
    { key: "conf", header: "Confidence", align: "right", cell: (s) => confidence(s, metric) },
  ];

  return (
    <div className="border border-line bg-canvas p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Tooltip content={exp.method_detail}>
          <span><Badge variant="info">Fresh simulation</Badge></span>
        </Tooltip>
        <InferredBadge />
        <span className="text-numeric text-2xs text-ink-dim">
          {exp.experiment_id} · S{exp.station_id} · {exp.seed_count} seeds × {(exp.horizon_s / 3600).toFixed(1)} h
          · objective {exp.objective}
        </span>
        <span className="flex-1" />
        {ACTIVE.has(exp.status) ? (
          <Badge variant="info" dot>
            simulating {exp.progress.results_done}/{exp.progress.results_total}
          </Badge>
        ) : exp.status !== "done" ? (
          <Badge variant="danger">{exp.status}{exp.error ? `: ${exp.error}` : ""}</Badge>
        ) : v ? <Badge variant={v.tone}>{v.text}</Badge> : null}
      </div>
      <DataTable columns={cols} rows={exp.scenarios} rowKey={(s) => s.scenario_id}
                 empty="No scenarios." />
      <p className="text-numeric mt-2 text-2xs leading-relaxed text-ink-faint">
        Baseline row absolute; other rows are the paired change vs baseline on the same seeds,
        with 95% intervals. Defect impact = expected defects (sum of inspected units'
        propensity). {exp.simulator_version ? `Simulator ${exp.simulator_version}` : ""}
        {exp.runtime_s != null ? ` · ${exp.runtime_s.toFixed(0)} s` : ""}
        {exp.progress.results_failed > 0 ? ` · ${exp.progress.results_failed} seeds failed` : ""}
      </p>
    </div>
  );
}
