"use client";

// One vehicle, expanded: the facts, then collapsible sections -- newest first, empty
// sections hidden. Every event is labelled with its source (EventSourceBadge).

import { useMemo, useState } from "react";
import type { TimelineEvent, VehicleTimelineData } from "@/lib/api";
import { cn } from "@/lib/cn";
import { IconChevronDown } from "@/components/ui";
import VehicleEvent from "@/components/vehicles/VehicleEvent";

const SECTIONS: { key: string; title: string; pick: (e: TimelineEvent) => boolean; open?: boolean }[] = [
  { key: "journey", title: "Vehicle Journey", open: true,
    pick: (e) => e.category === "journey" || e.category === "ai" || e.source === "VERIFIED" },
  { key: "process", title: "Process & Sensor Events", pick: (e) => e.category === "process" },
  { key: "quality", title: "Quality Checks", pick: (e) => e.category === "quality" },
  { key: "ai", title: "AI Observations", pick: (e) => e.source === "AI" },
  { key: "incident", title: "Incident History", pick: (e) => Boolean(e.investigation) },
  { key: "manual", title: "Manual Inspections", pick: (e) => e.category === "manual" },
];
const SHOW = 12;

export default function VehicleDetails({ data }: { data: VehicleTimelineData }) {
  const v = data.vehicle;
  // Newest first: what matters now is on top.
  const events = useMemo(() => [...data.events].sort((a, b) => sortKey(b) - sortKey(a)), [data.events]);
  return (
    <div className="space-y-3 border-t border-line bg-surface px-3 py-3">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <Fact k="Variant" v={(v.variant ?? "—").toUpperCase()} />
        <Fact k="Current station" v={v.current_station != null ? `S${v.current_station} ${v.current_station_name ?? ""}` : "—"} />
        <Fact k="Entered line" v={v.entered_at ? String(v.entered_at).slice(11, 16) : "—"} />
        <Fact k="Defect risk (model)" v={v.risk != null ? `${Math.round(v.risk * 100)}%` : "not scored yet"} />
        <Fact k="Quality status" v={v.quality_status} />
        <Fact k="Lot" v={v.lot_id ?? "—"} />
      </div>
      {SECTIONS.map((s) => {
        const rows = events.filter(s.pick);
        return rows.length ? <Accordion key={s.key} title={s.title} rows={rows} defaultOpen={s.open} /> : null;
      })}
    </div>
  );
}

function sortKey(e: TimelineEvent) {
  const t = Date.parse(e.ts);
  // Simulated and real clocks differ by months; real-time follow-ups are later by nature.
  return (Number.isNaN(t) ? 0 : t) + (e.clock === "wall" ? 1e14 : 0);
}

function Accordion({ title, rows, defaultOpen }: { title: string; rows: TimelineEvent[]; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(Boolean(defaultOpen));
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, SHOW);
  return (
    <section className="border border-line">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="flex w-full items-center justify-between px-3 py-2 text-left hover:bg-surface-2">
        <span className="text-2xs font-bold uppercase tracking-[0.12em] text-ink">
          {title} <span className="font-normal text-ink-faint">({rows.length})</span>
        </span>
        <IconChevronDown className={cn("size-3.5 text-ink-faint transition-transform duration-200", open && "rotate-180")} />
      </button>
      {open && (
        <div className="px-3 pb-2">
          <ul className="border-l border-line pl-1">{shown.map((e, i) => <VehicleEvent key={i} e={e} />)}</ul>
          {rows.length > SHOW && (
            <button type="button" className="label-upper mt-1 text-2xs text-ink-dim hover:text-primary"
              onClick={() => setAll((a) => !a)}>
              {all ? "Show fewer" : `Show all ${rows.length}`}
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function Fact({ k, v }: { k: string; v: string }) {
  return (
    <div className="min-w-0">
      <div className="text-2xs font-bold uppercase tracking-[0.12em] text-ink-faint">{k}</div>
      <div className="text-numeric mt-0.5 truncate text-xs font-bold text-ink">{v}</div>
    </div>
  );
}
