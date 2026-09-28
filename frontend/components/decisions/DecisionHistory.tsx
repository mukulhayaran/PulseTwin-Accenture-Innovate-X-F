"use client";

// Decision History (ADR-071): what was decided, by whom, what was sent, and what it did.

import { useEffect, useState } from "react";
import { type DecisionHistoryItem, getDecisionHistory } from "@/lib/api";
import { Badge, EmptyState, Panel, Skeleton } from "@/components/ui";

const TONE: Record<string, any> = {
  approved: "ok", executed: "ok", modified: "info", rejected: "danger", expired: "neutral",
  more_investigation: "warn",
};

export default function DecisionHistory({ plant }: { plant: string }) {
  const [rows, setRows] = useState<DecisionHistoryItem[] | null>(null);
  useEffect(() => { getDecisionHistory(plant).then((r) => setRows(r.history)).catch(() => setRows([])); }, [plant]);
  return (
    <Panel title="Decision History" note="approved, modified, rejected, sent back and expired">
      {!rows ? <Skeleton className="h-32 w-full" /> : rows.length === 0 ? (
        <EmptyState title="No decisions recorded yet" description="Recommendations you decide in the Decision Queue are recorded here." />
      ) : (
        <ul className="divide-y divide-line">
          {rows.map((h) => (
            <li key={h.recommendation_id} className="grid grid-cols-1 gap-2 py-3 md:grid-cols-[7rem_minmax(0,1fr)_minmax(0,1fr)]">
              <div>
                <div className="text-numeric text-2xs font-bold text-ink-faint">{h.incident_id}</div>
                <Badge variant={TONE[h.decision] ?? "neutral"}>{h.decision.replace(/_/g, " ")}</Badge>
              </div>
              <div className="min-w-0">
                <div className="text-xs font-bold text-ink">{h.action ?? h.problem}</div>
                <div className="text-2xs text-ink-faint">
                  {h.problem} · {h.decided_by ? `by ${h.decided_by}` : "no decision"}
                  {h.decided_at ? ` · ${new Date(h.decided_at).toLocaleString()}` : ""}
                </div>
                {h.note && <div className="mt-0.5 text-2xs text-ink-dim">“{h.note}”</div>}
              </div>
              <div className="text-xs">
                {h.actions.map((a, i) => (
                  <div key={i} className="text-ink-dim">
                    {a.action_type.replace(/_/g, " ")}: {a.status}{a.reference ? ` (${a.reference})` : ""}
                  </div>
                ))}
                {h.outcome && (
                  <div className="mt-0.5 font-bold text-ink">
                    Outcome: {h.outcome.status.replace(/_/g, " ")}
                    {h.outcome.reason && <span className="block text-2xs font-normal text-ink-faint">{h.outcome.reason}</span>}
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
