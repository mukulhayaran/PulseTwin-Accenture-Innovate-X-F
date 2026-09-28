"use client";

// The supervisor's Vehicle Timeline (ADR-071): every vehicle on the current run as a
// compact row; expand one to read its whole story, fetched lazily. Filtering, search and
// paging happen on the server -- the list never ships every unit. While the run is live
// the list refreshes every few seconds and a vehicle that moves flashes.

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { type Vehicle, type VehicleStatus, type VehicleTimelineData, getVehicleTimeline, listVehicles } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Badge, Button, IconChevronDown, IconSearch, Panel, Skeleton } from "@/components/ui";
import VehicleDetails from "@/components/vehicles/VehicleDetails";

const FILTERS: { value: "all" | VehicleStatus; label: string }[] = [
  { value: "all", label: "All" }, { value: "normal", label: "Normal" },
  { value: "at_risk", label: "At Risk" }, { value: "inspection_required", label: "Inspection Required" },
  { value: "incident", label: "Incident" }, { value: "completed", label: "Completed" },
];
const STATUS_BADGE: Record<VehicleStatus, { label: string; variant: any }> = {
  normal: { label: "NORMAL", variant: "neutral" }, at_risk: { label: "⚠ AT RISK", variant: "warn" },
  inspection_required: { label: "INSPECTION REQUIRED", variant: "danger" },
  incident: { label: "INCIDENT", variant: "ai" }, completed: { label: "COMPLETED", variant: "ok" },
};
const PAGE = 25;
// Live cadence vs settled: 3 s keeps motion visible inside the 300 req/min budget.
const LIVE_MS = 3_000;
const IDLE_MS = 15_000;

export default function VehicleTimeline({ plant }: { plant: string }) {
  const [filter, setFilter] = useState<"all" | VehicleStatus>("all");
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const [data, setData] = useState<Awaited<ReturnType<typeof listVehicles>> | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const live = data?.run?.status === "running";

  useEffect(() => { const t = setTimeout(() => setQ(query.trim()), 300); return () => clearTimeout(t); }, [query]);
  useEffect(() => { setLimit(PAGE); }, [filter, q, plant]);

  const isLive = live;
  useEffect(() => {
    let live = true;
    const load = () => listVehicles(plant, { status: filter, q: q || undefined, limit })
      .then((r) => live && setData(r)).catch(() => {});
    load();
    const id = setInterval(load, isLive ? LIVE_MS : IDLE_MS);
    return () => { live = false; clearInterval(id); };
  }, [plant, filter, q, limit, isLive]);

  return (
    <Panel title="Vehicle Timeline"
      note={data?.run ? `run ${data.run.run_id} · ${data.total} vehicle(s) shown` : "no simulation run yet"}>
      {data?.run && (
        <div className="mb-2 flex items-center gap-2 text-2xs text-ink-faint">
          <Badge variant={live ? "ok" : "neutral"} dot={live}>{live ? "LIVE" : data.run.status.toUpperCase()}</Badge>
          {live ? `updating every ${LIVE_MS / 1000} s` : "run not running -- showing its last state"}
          {data.run.simulation_time && <span className="text-numeric">· line clock {String(data.run.simulation_time).slice(11, 19)}</span>}
        </div>
      )}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <button key={f.value} type="button" onClick={() => setFilter(f.value)}
            className={cn("h-7 border px-2.5 text-2xs font-bold uppercase tracking-[0.08em]",
              filter === f.value ? "border-primary bg-primary text-on-primary" : "border-line text-ink-dim hover:bg-surface-2")}>
            {f.label}
            {data && f.value !== "all" && <span className="ml-1 font-normal opacity-80">{data.counts[f.value] ?? 0}</span>}
          </button>
        ))}
        <label className="ml-auto flex h-7 items-center gap-1.5 border border-line bg-canvas px-2">
          <IconSearch className="size-3.5 text-ink-faint" />
          <input value={query} onChange={(e) => setQuery(e.target.value)} maxLength={40}
            placeholder="Unit, variant, S19, lot…" aria-label="Search vehicles"
            className="w-44 bg-transparent text-2xs text-ink outline-none" />
        </label>
      </div>
      {!data ? <Skeleton className="h-40 w-full" /> : data.vehicles.length === 0 ? (
        <p className="py-6 text-center text-xs text-ink-faint">
          {data.run ? "No vehicles match the selected filters." : "Start a simulation to see vehicles move through the line."}
        </p>
      ) : (
        <ul className="space-y-1.5">
          {data.vehicles.map((v) => (
            <VehicleRow key={v.unit_id} v={v} plant={plant} runId={data.run?.run_id ?? null} live={live}
              open={open === v.unit_id} onToggle={() => setOpen((o) => (o === v.unit_id ? null : v.unit_id))} />
          ))}
        </ul>
      )}
      {data && data.total > data.vehicles.length && (
        <div className="mt-3 text-center">
          <Button size="xs" variant="outline" onClick={() => setLimit((l) => Math.min(l + PAGE, 100))}
            disabled={limit >= 100}>
            {limit >= 100 ? "Showing the first 100 -- narrow with a filter or search" : `Load more (${data.total - data.vehicles.length} more)`}
          </Button>
        </div>
      )}
    </Panel>
  );
}

function VehicleRow({ v, plant, runId, live, open, onToggle }: {
  v: Vehicle; plant: string; runId: string | null; live: boolean; open: boolean; onToggle: () => void;
}) {
  const [detail, setDetail] = useState<VehicleTimelineData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // Flash the row when the vehicle moves on or changes status.
  const [moved, setMoved] = useState(false);
  const last = useRef(`${v.current_station}|${v.status}`);
  useEffect(() => {
    const now = `${v.current_station}|${v.status}`;
    if (now === last.current) return;
    last.current = now;
    setMoved(true);
    const t = setTimeout(() => setMoved(false), 1200);
    return () => clearTimeout(t);
  }, [v.current_station, v.status]);
  const isLive = live;
  useEffect(() => {
    if (!open) return;
    let live = true;
    const load = () => getVehicleTimeline(plant, v.unit_id, runId)
      .then((d) => { if (live) { setDetail(d); setErr(null); } })
      .catch((e) => live && setErr(String(e?.message ?? e)));
    load();
    // While open, follow the vehicle: new stations, AI findings and decisions appear live.
    const id = setInterval(load, isLive ? 5_000 : IDLE_MS);
    return () => { live = false; clearInterval(id); };
  }, [open, plant, v.unit_id, runId, isLive]);
  const badge = STATUS_BADGE[v.status];
  return (
    <li className={cn("border transition-colors duration-700", open ? "border-primary" : "border-line",
      moved && "bg-info/8")}>
      <button type="button" onClick={onToggle} aria-expanded={open}
        className="grid w-full grid-cols-[6rem_4rem_minmax(0,1fr)_auto_1.25rem] items-center gap-3 px-3 py-2 text-left hover:bg-surface-2 md:grid-cols-[6rem_4rem_minmax(0,1fr)_8rem_8rem_auto_1.25rem]">
        <span className="text-numeric text-xs font-bold text-ink">{v.unit_id}</span>
        <span className="text-2xs uppercase text-ink-dim">{v.variant ?? "—"}</span>
        <span className="min-w-0">
          <span className="block truncate text-xs text-ink">
            {v.inspected ? "Finished line" : `Current: S${v.current_station}`}{" "}
            <span className="text-ink-faint">{v.inspected ? "" : v.current_station_name}</span>
          </span>
          {v.progress != null && (
            <span className="mt-1 block h-1 bg-surface-3" aria-hidden>
              <span className={cn("block h-1 transition-[width] duration-500", v.inspected ? "bg-ok" : "bg-primary")}
                style={{ width: `${Math.round(v.progress * 100)}%` }} />
            </span>
          )}
        </span>
        <span className="hidden text-2xs text-ink-faint md:inline">
          Entered {v.entered_at ? String(v.entered_at).slice(11, 16) : "—"}
          {v.eta && ` · ETA ${String(v.eta).slice(11, 16)}`}
        </span>
        <span className="text-numeric hidden text-2xs text-ink-dim md:inline">
          Risk {v.risk != null ? `${Math.round(v.risk * 100)}%` : "—"}
        </span>
        <Badge variant={badge.variant}>{v.status === "completed" && v.defect_found ? "DEFECT FOUND" : badge.label}</Badge>
        <IconChevronDown className={cn("size-3.5 text-ink-faint transition-transform duration-200", open && "rotate-180")} />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.2 }} className="overflow-hidden">
            {detail ? <VehicleDetails data={detail} /> : err ? (
              <p className="px-3 py-3 text-xs text-danger-fg">{err}</p>
            ) : <div className="p-3"><Skeleton className="h-24 w-full" /></div>}
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  );
}
