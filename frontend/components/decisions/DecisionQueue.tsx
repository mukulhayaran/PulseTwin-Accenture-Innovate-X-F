"use client";

// The manager's Decision Queue (ADR-071): only decisions needing THIS manager's authority,
// selected server-side by the autonomy governor. No operational noise.

import { useCallback, useEffect, useState } from "react";
import { type DecisionCardData, getPendingDecisions } from "@/lib/api";
import { Kpi, Skeleton } from "@/components/ui";
import DecisionCard from "@/components/decisions/DecisionCard";
import DecisionEvidenceDrawer from "@/components/decisions/DecisionEvidenceDrawer";

export default function DecisionQueue({ plant, onAction }: { plant: string; onAction?: (m: string) => void }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof getPendingDecisions>> | null>(null);
  const [evidenceFor, setEvidenceFor] = useState<DecisionCardData | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const load = useCallback(() => getPendingDecisions(plant).then(setData).catch(() => {}), [plant]);
  useEffect(() => {
    load();
    const id = setInterval(load, 10_000);
    return () => clearInterval(id);
  }, [load]);

  const s = data?.summary;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="Pending decisions" value={s ? String(s.pending) : "—"} tone={s?.pending ? "warn" : "default"} accent />
        <Kpi label="High-risk decisions" value={s ? String(s.high_risk) : "—"} tone={s?.high_risk ? "danger" : "default"} />
        <Kpi label="Approved today" value={s ? String(s.approved_today) : "—"} />
        <Kpi label="Actions in progress" value={s ? String(s.in_progress) : "—"} />
      </div>
      <div>
        <h2 className="text-lg font-bold text-ink">Decisions requiring your attention</h2>
        <p className="text-xs text-ink-faint">
          {data ? `${data.decisions.length} approval${data.decisions.length === 1 ? "" : "s"} pending` : "Loading…"}
        </p>
      </div>
      {done && (
        <div role="status" className="border border-ok/40 bg-ok/8 px-3 py-2 text-xs text-ok-fg">{done}</div>
      )}
      {!data ? <Skeleton className="h-48 w-full" /> : data.decisions.length === 0 ? (
        <div className="panel-surface px-6 py-10 text-center">
          <p className="text-sm font-bold text-ink">No approvals required</p>
          <p className="mx-auto mt-1 max-w-md text-xs text-ink-faint">
            PulseTwin is continuing to monitor and investigate production conditions. No actions
            currently require manager authorization.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {data.decisions.map((d) => (
            <DecisionCard key={d.recommendation_id} d={d} onEvidence={() => setEvidenceFor(d)}
              onDone={(m) => { setDone(m); onAction?.(m); load(); }} />
          ))}
        </div>
      )}
      <DecisionEvidenceDrawer recId={evidenceFor?.recommendation_id ?? null} title={evidenceFor?.title ?? ""}
        onClose={() => setEvidenceFor(null)} />
    </div>
  );
}
