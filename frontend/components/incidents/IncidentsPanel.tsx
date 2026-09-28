"use client";

// The supervisor's active incidents (ADR-069, ADR-071): one list + one read-only
// workspace. Decisions happen in the manager's Decision Queue, not here.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  type IncidentDetail, type IncidentSummary, getIncident, getSimilarCases, listIncidents,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { Badge, EmptyState, Panel, SegmentedControl, Skeleton } from "@/components/ui";
import { IncidentWorkspace } from "@/components/incidents/IncidentWorkspace";

export interface IncidentFocus { station: number | null; kind: string | null; since: string }

// What the AI is doing, in floor words, from the investigation + recommendation status.
function aiStatus(i: IncidentSummary): string {
  const r = i.recommendation?.status;
  if (r === "proposed") return "awaiting manager decision";
  if (r === "withheld") return "manual check requested";
  if (r) return r.replace(/_/g, " ");
  const inv = i.investigation;
  if (!inv) return "not investigated yet";
  return inv.status === "completed" ? "investigation complete" : "investigating";
}
const clock = (ts?: string | null) => (ts ? String(ts).slice(11, 16) : "—");

export default function IncidentsPanel({ plant, focus, onFocused, headerActions }: {
  plant: string;
  /** After a preset starts, open ITS incident (the recorded plant can fire others too). */
  focus?: IncidentFocus | null;
  onFocused?: () => void;
  headerActions?: React.ReactNode;
}) {
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<IncidentDetail | null>(null);
  const [similar, setSimilar] = useState<any[]>([]);
  // ADR-070: what is happening NOW by default; the history is one click away (and in the
  // supervisor's incident log).
  const [scope, setScope] = useState<"current" | "all">("current");
  // In refs, NOT in refresh's dependencies: callers pass an inline `onFocused`, a new
  // function every render, which re-created `refresh` and re-ran the polling effect on
  // every render -- 380 list calls a minute from one tab, measured.
  const focusRef = useRef(focus);
  const onFocusedRef = useRef(onFocused);
  focusRef.current = focus;
  onFocusedRef.current = onFocused;

  const refresh = useCallback(async () => {
    try {
      const r = await listIncidents(plant, scope, scope === "all" ? 100 : 30);
      const rows = r.incidents;
      setIncidents(rows);
      const f = focusRef.current;
      const want = f && rows.find((i) => i.created_at >= f.since
        && (f.kind ? i.kind === f.kind : i.station_id === f.station));
      if (want) { setSelected(want.incident_id); onFocusedRef.current?.(); return; }
      // Keep the selection only while it is still in the list: after a reset or a new run
      // the previous session's incident must not stay open (LOOP criterion A).
      setSelected((cur) => (cur && rows.some((i) => i.incident_id === cur) ? cur : rows[0]?.incident_id ?? null));
    } catch { /* keep the last list; the shell's connectivity banner says what is wrong */ }
  }, [plant, scope]);

  useEffect(() => {
    refresh();
    // Gentle: the API allows 300 requests a minute per role and IP, and /supervisor already
    // polls its own feeds; 3 s here pushed a floor tab into 429s (measured).
    const id = setInterval(refresh, 10_000);
    return () => clearInterval(id);
  }, [refresh]);

  useEffect(() => { setSelected(null); }, [plant]);

  useEffect(() => {
    if (!selected) { setDetail(null); return; }
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    // Poll only while something can still change: the investigation is running, or an
    // executed action's outcome window is still being observed. A settled incident is
    // read once.
    const load = () => getIncident(selected).then((d) => {
      if (!live) return;
      setDetail(d);
      // Everything short of settled can still change -- including a recommendation waiting
      // on a MANAGER, whose approval must reach this page without a reload (LOOP N).
      const moving = !["resolved", "closed"].includes(d.incident.status)
        || d.actions.some((a: any) => a.status === "approved" || a.outcome?.status === "observing");
      if (moving) timer = setTimeout(load, 5000);
    }).catch(() => { if (live) timer = setTimeout(load, 10_000); });
    load();
    getSimilarCases(selected).then((r) => live && setSimilar(r.cases)).catch(() => setSimilar([]));
    return () => { live = false; clearTimeout(timer); };
  }, [selected]);

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[20rem_minmax(0,1fr)]">
      <Panel title="Active incidents">
        {headerActions && <div className="mb-3">{headerActions}</div>}
        <SegmentedControl
          name="incident-scope"
          aria-label="Which incidents"
          value={scope}
          onChange={(v) => { setScope(v); setSelected(null); }}
          options={[{ value: "current", label: "Current run" }, { value: "all", label: "All runs" }]}
          className="mb-3"
        />
        {incidents.length === 0 ? (
          <EmptyState
            title={scope === "current" ? "No incidents in the current run" : "No incidents recorded yet"}
            description={scope === "current"
              ? "Start a simulation or a demo preset above, or report an issue."
              : undefined}
          />
        ) : (
          <ul className="max-h-[32rem] space-y-1.5 overflow-y-auto pr-1">
            {incidents.map((i) => {
              const conf = i.recommendation?.confidence ?? i.investigation?.confidence;
              return (
                <li key={i.incident_id}>
                  <button
                    onClick={() => setSelected(i.incident_id)}
                    className={cn(
                      "w-full border px-2.5 py-2 text-left transition-colors",
                      selected === i.incident_id ? "border-primary bg-info/8" : "border-line hover:bg-surface-2",
                    )}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-1">
                      <span className="text-numeric text-2xs font-bold text-ink">{i.incident_id}</span>
                      <Badge variant={i.status === "resolved" ? "ok" : "neutral"}>
                        {i.status.replace(/_/g, " ")}
                      </Badge>
                    </div>
                    <div className="mt-1 line-clamp-2 text-xs text-ink-dim">{i.headline || i.title}</div>
                    {i.seen_before > 0 && (
                      <Badge variant="warn" className="mt-1">seen {i.seen_before}× before</Badge>
                    )}
                    <dl className="mt-1 grid grid-cols-2 gap-x-2 text-2xs">
                      <dt className="text-ink-faint">First detected</dt>
                      <dd className="text-numeric text-ink">{clock(i.trigger_ts || i.created_at)}</dd>
                      <dt className="text-ink-faint">Affected vehicles</dt>
                      <dd className="text-numeric text-ink" title={i.affected?.basis}>{i.affected?.count ?? "—"}</dd>
                      <dt className="text-ink-faint">AI status</dt>
                      <dd className="text-ai-fg">{aiStatus(i)}</dd>
                      <dt className="text-ink-faint">Confidence</dt>
                      <dd className="text-numeric text-ink">{conf != null ? `${Math.round(conf * 100)}%` : "—"}</dd>
                    </dl>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </Panel>
      <div className="min-w-0 space-y-4">
        {detail ? (
          <IncidentWorkspace detail={detail} similar={similar} />
        ) : selected ? <Skeleton className="h-48 w-full" /> : null}
      </div>
    </div>
  );
}
