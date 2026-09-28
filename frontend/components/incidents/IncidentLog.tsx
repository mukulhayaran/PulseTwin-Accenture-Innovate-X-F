"use client";

// The supervisor's incident log (ADR-070): every incident on the plant, newest first, one
// readable line each. The line is the server's headline -- written from the incident's own
// record -- so the log says what the investigation actually found, not a status code.

import { useEffect, useState } from "react";
import { type IncidentSummary, listIncidents } from "@/lib/api";
import { Badge, DataTable, Panel, type Column } from "@/components/ui";

const TONE: Record<string, any> = {
  resolved: "ok", awaiting_decision: "info", needs_manual_check: "warn",
  investigation_failed: "danger", closed: "neutral", recommendation_rejected: "neutral",
};

const when = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—"
    : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
};

export default function IncidentLog({ plant }: { plant: string }) {
  const [rows, setRows] = useState<IncidentSummary[] | null>(null);

  useEffect(() => {
    let live = true;
    const load = () => listIncidents(plant, "all", 100)
      .then((r) => live && setRows(r.incidents)).catch(() => {});
    load();
    const id = setInterval(load, 20_000);
    return () => { live = false; clearInterval(id); };
  }, [plant]);

  const columns: Column<IncidentSummary>[] = [
    { key: "when", header: "Opened", cell: (r) => <span className="text-numeric text-2xs text-ink-faint">{when(r.created_at)}</span> },
    { key: "id", header: "Incident", cell: (r) => <span className="text-numeric text-2xs font-bold text-ink">{r.incident_id}</span> },
    { key: "by", header: "Raised by", cell: (r) => (
      <span className="text-2xs text-ink-dim">{r.source === "manual" ? "operator" : r.source === "quality_detector" ? "quality gate" : "detector"}</span>
    ) },
    { key: "what", header: "What happened", cell: (r) => (
      <span className="text-xs text-ink">
        {r.headline}
        {r.seen_before > 0 && <span className="ml-1.5 text-2xs text-warn-fg">(seen {r.seen_before}× before)</span>}
      </span>
    ) },
    { key: "status", header: "Status", cell: (r) => (
      <Badge variant={TONE[r.status] ?? (r.status.startsWith("outcome_") ? "ok" : "neutral")}>
        {r.status.replace(/_/g, " ")}
      </Badge>
    ) },
  ];

  return (
    <Panel title="Operations Log" note={rows ? `${rows.length} incidents, all runs, newest first` : undefined}>
      <DataTable
        columns={columns}
        rows={rows ?? []}
        rowKey={(r) => r.incident_id}
        maxHeight={440}
        empty={rows ? "No incidents recorded yet." : "Loading…"}
      />
    </Panel>
  );
}
