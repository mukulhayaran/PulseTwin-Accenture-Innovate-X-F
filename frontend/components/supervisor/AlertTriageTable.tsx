"use client";

// Autonomous alert triage: what to look at first, and why.
//
// The ranking is computed in the BACKEND by fixed rules
// (serve/routers/intelligence.py). Nothing here re-ranks, and the copilot is told not to
// either -- a class this panel shows is a class the rules decided, which is what makes
// it arguable with.
//
// Two things this panel refuses to do:
//   * hide a row. LOW_CONFIDENCE is a RANK, not a deletion. An alert the operator never
//     sees is an alert nobody can overrule.
//   * present a ranking built on a constant. When the backend reports that a dimension
//     could not discriminate, that notice is rendered above the table, not swallowed.

import { useEffect, useRef, useState } from "react";
import { AlertTriage, AlertTriageRow, getAlertTriage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Badge, DataTable, Panel, Skeleton, Tooltip } from "@/components/ui";

const CLASS_VARIANT: Record<string, "danger" | "warn" | "info" | "neutral"> = {
  CRITICAL: "danger",
  INVESTIGATE: "warn",
  MONITOR: "info",
  LOW_CONFIDENCE: "neutral",
};

const CLASS_ORDER = ["CRITICAL", "INVESTIGATE", "MONITOR", "LOW_CONFIDENCE"];

// Re-poll cadence once the replay is producing an anchor. Far coarser than the SSE flush
// (up to 60/s) -- the underlying ranking only needs to catch up with the clock, not track
// every event -- so this stays a fixed interval read against a ref rather than an effect
// dependency on `atTs` itself.
const POLL_MS = 8000;

export default function AlertTriageTable({
  plant,
  limit = 40,
  onSelect,
  refreshKey,
  atTs,
}: {
  plant: string;
  limit?: number;
  onSelect?: (unitId: string) => void;
  // Bump this (e.g. the same nonce a replay restart or an offline-recovery retry already
  // increments) to force the effect below to tear down and restart.
  refreshKey?: number | string;
  // Replay-clock anchor. Empty/undefined means the replay has not produced an event yet
  // this session, so the panel shows a waiting state rather than falling back to the
  // backend's whole-artifact default (serve/routers/intelligence.py::alert_triage scopes
  // to `prediction_ts <= at_ts` when given one) -- Stop/Restart are expected to change
  // what this panel shows, which a permanently whole-backlog query never could.
  atTs?: string;
}) {
  const [data, setData] = useState<AlertTriage | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const atTsRef = useRef(atTs);
  atTsRef.current = atTs;

  useEffect(() => {
    setData(null);
    setErr(null);
    if (!atTs) return; // nothing has happened in this replay session yet

    let cancelled = false;
    const load = () => {
      getAlertTriage(plant, limit, atTsRef.current)
        .then((d) => !cancelled && setData(d))
        .catch((e) => !cancelled && setErr(String(e)));
    };
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
    // Only plant/limit/refreshKey, and the has-an-anchor-at-all transition, restart the
    // effect -- every subsequent clock tick is picked up by the poll via atTsRef, not by
    // re-running this effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plant, limit, refreshKey, Boolean(atTs)]);

  return (
    <Panel
      title="Alert triage"
      note="Every candidate worth inspecting, not only budget-fired alerts."
      actions={
        data ? (
          <div className="flex shrink-0 gap-1">
            {CLASS_ORDER.map((c) =>
              data.counts_by_class[c] ? (
                <Badge key={c} variant={CLASS_VARIANT[c]}>
                  {data.counts_by_class[c]} {c.toLowerCase().replace(/_/g, " ")}
                </Badge>
              ) : null
            )}
          </div>
        ) : null
      }
    >
      {err && <p className="text-2xs text-danger-fg">{err}</p>}

      {!atTs && !err && (
        <p className="py-6 text-center text-2xs text-ink-faint">
          Waiting for the replay to start — triage scopes to what has happened so far.
        </p>
      )}

      {atTs && !data && !err && (
        <div className="space-y-2">
          <Skeleton className="h-3 w-1/2" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-5/6" />
        </div>
      )}

      {data && (
        <>
          {data.diagnostics.notes.length > 0 && (
            <div className="mb-3 rounded-lg border border-warn/30 bg-warn/[0.06] p-2.5">
              {data.diagnostics.notes.map((n) => (
                <p key={n} className="text-2xs leading-relaxed text-ink-dim">
                  {n}
                </p>
              ))}
            </div>
          )}

          <p className="mb-2 text-2xs text-ink-faint">
            {data.count} candidates · {data.already_fired} already alerted · the rest are
            above the cost-optimal inspection threshold but below what the attention
            budget could reach.
          </p>

          <DataTable<AlertTriageRow>
            rows={data.alerts}
            rowKey={(r) => r.unit_id}
            maxHeight={420}
            empty="No candidates."
            columns={[
              {
                key: "rank",
                header: "#",
                cell: (r: AlertTriageRow) => (
                  <span className="text-numeric text-ink-faint">{r.rank}</span>
                ),
              },
              {
                key: "unit_id",
                header: "Unit",
                cell: (r: AlertTriageRow) => (
                  <button
                    type="button"
                    onClick={() => onSelect?.(r.unit_id)}
                    className={cn(
                      "text-numeric underline-offset-2",
                      onSelect ? "hover:text-ink hover:underline" : "cursor-default"
                    )}
                  >
                    {r.unit_id}
                  </button>
                ),
              },
              {
                key: "triage_class",
                header: "Class",
                cell: (r: AlertTriageRow) => (
                  <Tooltip content={r.reasons.join(" ")}>
                    <span className="cursor-help">
                      <Badge variant={CLASS_VARIANT[r.triage_class] ?? "neutral"}>
                        {r.triage_class.replace(/_/g, " ")}
                      </Badge>
                    </span>
                  </Tooltip>
                ),
              },
              {
                key: "severity",
                header: "Severity",
                cell: (r: AlertTriageRow) => (
                  <Tooltip
                    content={`Risk as a multiple of this plant's own alert threshold (${data.budget_threshold.toFixed(4)}).`}
                  >
                    <span className="text-numeric cursor-help">
                      {r.severity_x_threshold?.toFixed(2) ?? "—"}×
                    </span>
                  </Tooltip>
                ),
              },
              {
                key: "station",
                header: "Station",
                cell: (r: AlertTriageRow) => (
                  <Tooltip content="The station named by this unit's top-ranked factor — the one the risk is actually about, not the last station on the line.">
                    <span className="text-numeric cursor-help">
                      {r.implicated_station != null ? `S${r.implicated_station}` : "—"}
                    </span>
                  </Tooltip>
                ),
              },
              {
                key: "lead",
                header: "Lead",
                cell: (r: AlertTriageRow) => (
                  <span className="text-numeric">
                    {r.lead_time_min != null ? `${r.lead_time_min.toFixed(1)}m` : "—"}
                  </span>
                ),
              },
              {
                key: "peers",
                header: "Peers",
                cell: (r: AlertTriageRow) => (
                  <Tooltip content="Other units in the same half-hour window that are themselves above the alert threshold.">
                    <span className="text-numeric cursor-help">
                      {r.peers_above_threshold}
                    </span>
                  </Tooltip>
                ),
              },
              {
                key: "prov",
                header: "",
                cell: (r: AlertTriageRow) =>
                  r.provenance === "inferred" ? (
                    <Tooltip content="A sensor at this unit's implicated station is dark, so the inputs behind this score are reconstructed rather than measured.">
                      <span className="cursor-help">
                        <Badge variant="warn">INF</Badge>
                      </span>
                    </Tooltip>
                  ) : null,
              },
            ]}
          />

          <p className="mt-2 text-2xs leading-relaxed text-ink-faint">
            {data.method}
          </p>
        </>
      )}
    </Panel>
  );
}
