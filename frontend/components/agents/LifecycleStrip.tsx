"use client";

// The shared agent lifecycle (ADR-064), one strip for every agent card and proposal.
// Stages are DERIVED from recorded state -- the proposal's is computed server-side
// (serve/routers/proposals.py::lifecycle); an agent run's is derived here from its own
// status, phase and experiments. A stage the record cannot establish is shown as not
// reached, never assumed.

import type { ExperimentListItem, Lifecycle, LifecycleStage, TriageRun } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Badge, Tooltip } from "@/components/ui";

export const STAGES: LifecycleStage[] = [
  "investigating", "forming_hypotheses", "simulating", "comparing", "verifying",
  "awaiting_approval", "applied", "monitoring", "outcome_evaluated",
];

const LABEL: Record<LifecycleStage, string> = {
  investigating: "Investigating",
  forming_hypotheses: "Forming hypotheses",
  simulating: "Simulating",
  comparing: "Comparing",
  verifying: "Verifying",
  awaiting_approval: "Awaiting approval",
  applied: "Applied",
  monitoring: "Monitoring",
  outcome_evaluated: "Outcome evaluated",
};

const TERMINAL: Record<string, string> = {
  rejected: "Rejected by a person",
  no_action: "No action warranted",
  no_agent: "No agent run",
  agent_failed: "Agent failed",
  finding_rejected: "Finding failed verification",
  run_failed: "Run failed",
};

/** An agent run's lifecycle, from its own recorded status, live phase and experiments. */
export function runLifecycle(run: TriageRun, experiments: ExperimentListItem[]): Lifecycle {
  const active = run.status === "queued" || run.status === "running";
  const hasExp = experiments.length > 0;
  const expActive = experiments.some((e) => e.status === "queued" || e.status === "running");
  const hyps = (run.finding?.hypotheses?.length ?? 0) > 0;
  let phase: LifecycleStage | null;
  if (active) {
    const p = run.progress?.phase ?? "";
    phase = expActive || p === "Running simulation" ? "simulating"
      : p === "Comparing interventions" ? "comparing"
      : p === "Verifying every number" ? "verifying"
      : p === "Composing the finding" ? (hasExp ? "comparing" : "investigating")
      : "investigating";
  } else if (run.status === "verified" || run.status === "verified_with_redactions") {
    phase = (run.finding?.proposed_actions?.length ?? 0) > 0 ? "awaiting_approval" : "verifying";
  } else {
    phase = null;
  }
  const terminal = run.status === "rejected" ? "finding_rejected"
    : run.status === "failed" ? "run_failed" : null;
  const reached: LifecycleStage[] = [];
  const end = phase ? STAGES.indexOf(phase) : active ? 0 : STAGES.indexOf("verifying");
  for (const s of STAGES.slice(0, end + 1)) {
    if (s === "forming_hypotheses" && !hyps) continue;
    if ((s === "simulating" || s === "comparing") && !hasExp && s !== phase) continue;
    reached.push(s);
  }
  return { phase, reached: terminal ? reached.filter((s) => s !== "verifying") : reached,
           terminal, stages: STAGES };
}

export default function LifecycleStrip({ lifecycle }: { lifecycle: Lifecycle }) {
  const reached = new Set(lifecycle.reached);
  return (
    <div className="flex flex-wrap items-center gap-x-1 gap-y-1.5" aria-label="Agent lifecycle">
      {STAGES.map((s, i) => {
        const current = lifecycle.phase === s && !lifecycle.terminal;
        const done = reached.has(s) && !current;
        return (
          <div key={s} className="flex items-center gap-1">
            <Tooltip content={current ? `Current stage: ${LABEL[s]}`
              : done ? `${LABEL[s]} — recorded` : `${LABEL[s]} — not reached or not recorded`}>
              <span
                className={cn(
                  "cursor-help border px-1.5 py-0.5 text-2xs",
                  current ? "border-primary bg-primary font-bold text-on-primary"
                    : done ? "border-ink/30 bg-surface-2 text-ink"
                    : "border-dashed border-line text-ink-faint",
                )}
                aria-current={current ? "step" : undefined}
              >
                {LABEL[s]}
              </span>
            </Tooltip>
            {i < STAGES.length - 1 && <span aria-hidden className="text-2xs text-ink-faint">›</span>}
          </div>
        );
      })}
      {lifecycle.terminal && (
        <Badge variant={lifecycle.terminal === "rejected" || lifecycle.terminal === "no_action"
          ? "neutral" : "danger"}>
          {TERMINAL[lifecycle.terminal] ?? lifecycle.terminal.replace(/_/g, " ")}
        </Badge>
      )}
    </div>
  );
}
