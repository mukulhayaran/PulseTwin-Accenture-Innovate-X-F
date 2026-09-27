"use client";

// Operational memory at a glance (ADR-064): every evaluated action outcome and how well
// the simulations that justified them predicted what then happened. Read-only; every
// verdict and error here was computed by serve/outcomes.py, not by a model.

import { useEffect, useState } from "react";
import { ActionOutcome, getActionOutcomes } from "@/lib/api";
import { Panel } from "@/components/ui";
import OutcomeCard from "./OutcomeCard";

const POLL_MS = 10000;

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
  return (
    <Panel
      title="Action outcomes"
      note="What approved actions did once applied to the simulated twin, judged by configured thresholds, and how far each simulation's projection landed from the observation."
    >
      {err && <p className="text-2xs text-danger-fg">{err}</p>}
      {!data ? (
        <p className="py-6 text-center text-2xs text-ink-faint">Loading outcomes…</p>
      ) : data.outcomes.length === 0 ? (
        <p className="py-6 text-center text-2xs text-ink-faint">
          No approved action has been applied to a running twin yet. Approve a proposal while
          a live simulation runs; its outcome window opens here.
        </p>
      ) : (
        <>
          {acc && (
            <dl className="text-numeric mb-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
              <div><dt className="text-2xs text-ink-faint">Outcomes</dt><dd>{acc.outcomes}</dd></div>
              <div><dt className="text-2xs text-ink-faint">Evaluated</dt><dd>{acc.evaluated}</dd></div>
              <div>
                <dt className="text-2xs text-ink-faint">Success rate</dt>
                <dd>{acc.success_rate == null ? "—" : `${(acc.success_rate * 100).toFixed(0)}%`}</dd>
              </div>
              <div>
                <dt className="text-2xs text-ink-faint">Mean |projection error|</dt>
                <dd>{acc.mean_abs_projection_error == null ? "— (none projected)"
                  : acc.mean_abs_projection_error.toFixed(3)}</dd>
              </div>
            </dl>
          )}
          <ul className="space-y-2">
            {data.outcomes.map((o: ActionOutcome) => (
              <li key={o.outcome_id}>
                <div className="text-numeric mb-1 text-2xs text-ink-dim">
                  S{o.station_id} · {o.intervention_type.replace(/_/g, " ")} · {o.proposal_id}
                </div>
                <OutcomeCard outcome={o} />
              </li>
            ))}
          </ul>
        </>
      )}
    </Panel>
  );
}
