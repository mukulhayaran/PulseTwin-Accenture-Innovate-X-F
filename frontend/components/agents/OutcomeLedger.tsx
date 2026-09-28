"use client";

// Operational memory at a glance (ADR-064): every evaluated action outcome and how well
// the simulations that justified them predicted what then happened. Read-only; every
// verdict and error here was computed by serve/outcomes.py, not by a model.

import { useEffect, useState } from "react";
import { ActionOutcome, getActionOutcomes } from "@/lib/api";
import { EmptyState, Panel } from "@/components/ui";
import OutcomeCard from "./OutcomeCard";

const POLL_MS = 10000;

const ACTION_LABEL: Record<string, string> = {
  increase_feed_buffer: "Conveyor / feed-buffer adjustment",
  maintenance_ticket: "Maintenance work order",
  hvac_setpoint: "Paint-zone HVAC correction",
  rebalance_cycle: "Line rebalance",
  shift_weld_load: "Weld-load adjustment",
  resequence_paint_queue: "Paint-queue resequencing",
};

const TERMINAL_EVALUATED = new Set([
  "successful", "partially_successful", "no_material_effect", "harmful",
]);

function actionLabel(type: string) {
  return ACTION_LABEL[type] ?? type.replace(/_/g, " ");
}

function groupOutcomes(outcomes: ActionOutcome[]) {
  const grouped = new Map<string, ActionOutcome[]>();
  for (const outcome of outcomes) {
    const key = `${outcome.station_id}:${outcome.intervention_type}`;
    grouped.set(key, [...(grouped.get(key) ?? []), outcome]);
  }
  return [...grouped.values()];
}

function runSummary(outcomes: ActionOutcome[]) {
  const full = outcomes.filter((o) => o.status === "successful").length;
  const partial = outcomes.filter((o) => o.status === "partially_successful").length;
  const evaluated = outcomes.filter((o) => TERMINAL_EVALUATED.has(o.status)).length;
  const unresolved = outcomes.length - evaluated;
  const parts = [
    `${outcomes.length} independent twin run${outcomes.length === 1 ? "" : "s"}`,
    evaluated ? `${full} full, ${partial} partial` : "evaluation pending",
  ];
  if (unresolved) parts.push(`${unresolved} pending/cancelled`);
  return parts.join(" · ");
}

function projectionErrorLabel(outcomes: ActionOutcome[], value: number | null) {
  if (value == null) return "— (none projected)";
  const projected = outcomes.filter((o) => o.prediction_error != null);
  if (projected.length && projected.every((o) => o.metric === "stalled_share")) {
    return `${(value * 100).toFixed(1)} pp`;
  }
  return value.toFixed(3);
}

export default function OutcomeLedger({ plant }: { plant: string }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof getActionOutcomes>> | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      getActionOutcomes(plant, 10)
        .then((d) => !cancelled && setData(d))
        .catch((e) => !cancelled && setErr(String(e)));
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [plant]);

  const acc = data?.accuracy;
  const groups = groupOutcomes(data?.outcomes ?? []);
  return (
    <Panel
      title="Action outcomes"
      note="Latest measured result per action. Repeated validations are grouped as independent twin runs, with earlier evidence available on demand."
    >
      {err && <p className="text-2xs text-danger-fg">{err}</p>}
      {!data ? (
        <p className="py-6 text-center text-2xs text-ink-faint">Loading outcomes…</p>
      ) : data.outcomes.length === 0 ? (
        <EmptyState
          title="No outcomes yet"
          description="Approve a recommendation in the Decision Queue while a live simulation runs; its outcome window opens here."
        />
      ) : (
        <>
          {acc && (
            <dl className="text-numeric mb-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
              <div><dt className="text-2xs text-ink-faint">Twin runs</dt><dd>{acc.outcomes}</dd></div>
              <div><dt className="text-2xs text-ink-faint">Evaluated</dt><dd>{acc.evaluated}</dd></div>
              <div>
                <dt className="text-2xs text-ink-faint">Helped (full + partial)</dt>
                <dd>{acc.success_rate == null ? "—" : `${(acc.success_rate * 100).toFixed(0)}%`}</dd>
              </div>
              <div>
                <dt className="text-2xs text-ink-faint">Mean |projection error|</dt>
                <dd>{projectionErrorLabel(data.outcomes, acc.mean_abs_projection_error)}</dd>
              </div>
            </dl>
          )}
          <ul className="space-y-2">
            {groups.map((runs) => {
              const latest = runs[0];
              const earlier = runs.slice(1);
              return (
                <li key={`${latest.station_id}:${latest.intervention_type}`}>
                  <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
                    <span className="text-xs font-semibold text-ink">
                      S{latest.station_id} · {actionLabel(latest.intervention_type)}
                    </span>
                    <span className="text-numeric text-2xs text-ink-dim">
                      {runSummary(runs)}
                    </span>
                  </div>
                  <OutcomeCard outcome={latest} />
                  {earlier.length > 0 && (
                    <details className="mt-2 border-l-2 border-line pl-3">
                      <summary className="cursor-pointer text-2xs font-semibold uppercase tracking-[0.1em] text-ink-dim hover:text-primary">
                        View {earlier.length} earlier validation run{earlier.length === 1 ? "" : "s"}
                      </summary>
                      <ul className="mt-2 space-y-2">
                        {earlier.map((outcome) => (
                          <li key={outcome.outcome_id}>
                            <div className="text-numeric mb-1 text-2xs text-ink-faint">
                              Independent run · {outcome.proposal_id}
                            </div>
                            <OutcomeCard outcome={outcome} />
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </Panel>
  );
}
