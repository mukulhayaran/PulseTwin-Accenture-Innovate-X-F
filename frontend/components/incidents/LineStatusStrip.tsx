"use client";

// The supervisor's three questions in one glance (LOOP §20): is the line healthy, is the
// AI working on anything, and what needs attention. Read from the same current-run lists
// the page's panels use; nothing here is decided in the browser.

import { useEffect, useState } from "react";
import { type IncidentSummary, listIncidents, listVehicles } from "@/lib/api";
import { cn } from "@/lib/cn";

const OPEN = (i: IncidentSummary) => !["resolved", "closed"].includes(i.status);

function aiNow(open: IncidentSummary[]): { text: string; tone: "ok" | "ai" | "warn" } {
  if (!open.length) return { text: "Monitoring", tone: "ok" };
  const i = open[0];
  const r = i.recommendation?.status;
  const where = i.station_id != null ? ` S${i.station_id}` : "";
  if (r === "proposed") return { text: `Awaiting manager decision${where}`, tone: "warn" };
  if (r === "withheld") return { text: `Manual check requested${where}`, tone: "warn" };
  if (r === "executed" || r === "approved" || r === "modified") return { text: `Verifying the fix${where}`, tone: "ai" };
  return { text: `Investigating${where}`, tone: "ai" };
}

// Stations with an open incident first, then vehicles the backend marks at risk,
// inspection-required or involved -- "which stations / vehicles", not just a count.
function attentionText(open: IncidentSummary[] | null, vehicles: number | null): string | null {
  if (open == null || vehicles == null) return null;
  const stations = [...new Set(open.map((i) => i.station_id).filter((s) => s != null))].map((s) => `S${s}`);
  const parts = [...stations, ...(vehicles ? [`${vehicles} vehicle${vehicles === 1 ? "" : "s"}`] : [])];
  return parts.length ? parts.join(" · ") : "Nothing";
}

export default function LineStatusStrip({ plant }: { plant: string }) {
  const [open, setOpen] = useState<IncidentSummary[] | null>(null);
  const [attention, setAttention] = useState<number | null>(null);
  useEffect(() => {
    let live = true;
    const load = () => {
      listIncidents(plant, "current").then((r) => live && setOpen(r.incidents.filter(OPEN))).catch(() => {});
      listVehicles(plant, { limit: 1 }).then((v) => live && setAttention(
        (v.counts.at_risk ?? 0) + (v.counts.inspection_required ?? 0) + (v.counts.incident ?? 0))).catch(() => {});
    };
    load();
    const id = setInterval(load, 5_000);
    return () => { live = false; clearInterval(id); };
  }, [plant]);

  const degraded = (open ?? []).filter((i) => i.kind === "throughput" || i.kind === "equipment");
  const line = open == null ? null : degraded.length
    ? { text: `Degraded at ${degraded.map((i) => (i.station_id != null ? `S${i.station_id}` : "line")).join(", ")}`, tone: "warn" as const }
    : { text: "Healthy", tone: "ok" as const };
  const ai = open == null ? null : aiNow(open);
  const cells: { k: string; v: string | null; tone?: "ok" | "ai" | "warn" }[] = [
    { k: "Line", v: line?.text ?? null, tone: line?.tone },
    { k: "AI", v: ai?.text ?? null, tone: ai?.tone },
    { k: "Needs attention", v: attentionText(open, attention) },
  ];
  return (
    <div className="mb-4 grid grid-cols-1 gap-px bg-line sm:grid-cols-3" aria-label="Line status">
      {cells.map((c) => (
        <div key={c.k} className="flex items-center gap-2.5 bg-surface px-4 py-3">
          <span aria-hidden className={cn("size-2.5 shrink-0 rounded-full",
            c.tone === "ok" ? "bg-ok" : c.tone === "warn" ? "bg-warn" : c.tone === "ai" ? "animate-pulse bg-ai" : "bg-line")} />
          <span className="text-2xs font-bold uppercase tracking-[0.12em] text-ink-faint">{c.k}</span>
          <span className="truncate text-sm font-bold text-ink">{c.v ?? "—"}</span>
        </div>
      ))}
    </div>
  );
}
