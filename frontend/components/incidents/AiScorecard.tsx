"use client";

// ADR-067 agent reliability scorecard. Every tile is a query over persisted rows
// (serve/ai_operations.py) and shows its sample size; a metric with no data reads "—",
// never a plausible default.

import { useEffect, useState } from "react";
import { getAiMetrics } from "@/lib/api";
import { Panel } from "@/components/ui";

const TILES: [string, string][] = [
  ["incidents_total", "Incidents"], ["ai_investigated", "AI-investigated"],
  ["successful_investigations", "Investigations completed"], ["abstention_rate", "Abstention rate"],
  ["human_acceptance_rate", "Human acceptance"], ["rejection_rate", "Rejected"],
  ["modification_rate", "Modified"], ["evidence_coverage", "Evidence coverage"],
  ["avg_investigation_steps", "Avg steps"], ["median_investigation_latency_s", "Median latency"],
  ["ai_cost_per_incident_usd", "AI cost / incident"], ["provider_fallback_rate", "Provider fallback"],
  ["degraded_mode_success_rate", "Deterministic-mode success"],
  ["llm_plan_schema_failure_rate", "LLM plan schema failures"],
  ["invented_tool_rejections", "Invented-tool rejections"],
  ["narrative_unsupported_claim_rate", "Unsupported-claim rate (verifier)"],
  ["simulation_prediction_abs_error", "Twin prediction |error|"],
  ["incident_recurrence_rate", "Recurrence"], ["mean_time_to_resolution_s", "Mean time to resolution"],
];

function fmt(m: any): string {
  if (!m || m.value == null) return "—";
  if (m.unit === "ratio") return `${(m.value * 100).toFixed(0)}%`;
  if (m.unit === "s") return m.value >= 120 ? `${(m.value / 60).toFixed(1)} min` : `${m.value.toFixed(0)} s`;
  if (m.unit === "USD") return `$${m.value.toFixed(3)}`;
  return String(m.value);
}

export default function AiScorecard({ plant }: { plant?: string }) {
  const [m, setM] = useState<Record<string, any> | null>(null);
  useEffect(() => {
    let live = true;
    const load = () => getAiMetrics(plant).then((r) => live && setM(r)).catch(() => {});
    load();
    const id = setInterval(load, 10_000);
    return () => { live = false; clearInterval(id); };
  }, [plant]);
  return (
    <Panel title="Agent reliability scorecard" tone="ai" note="Derived from persisted incidents, steps, decisions, actions and outcomes; n = sample size">
      <div className="grid grid-cols-2 gap-px bg-line sm:grid-cols-3 xl:grid-cols-5">
        {TILES.map(([k, label]) => (
          <div key={k} className="bg-surface p-3">
            <div className="text-2xs font-bold uppercase tracking-[0.12em] text-ink-faint">{label}</div>
            <div className="text-numeric mt-1 text-lg font-bold text-ink">{fmt(m?.[k])}</div>
            <div className="text-numeric text-2xs text-ink-faint">n = {m?.[k]?.n ?? 0}</div>
          </div>
        ))}
      </div>
    </Panel>
  );
}
