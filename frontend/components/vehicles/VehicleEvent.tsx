"use client";

// One line of a vehicle's timeline. An AI investigation line expands into the Incident
// Commander's STRUCTURED record -- observation, evidence, hypotheses, current state,
// recommendation and what followed. No reasoning text beyond what was persisted.

import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import type { InvestigationCard, TimelineEvent } from "@/lib/api";
import { cn } from "@/lib/cn";
import { IconChevronDown } from "@/components/ui";
import EventSourceBadge from "@/components/vehicles/EventSourceBadge";

export function eventTime(e: { ts: string; clock: string }) {
  if (!e.ts) return "—";
  return e.clock === "wall"
    ? new Date(e.ts).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
    : String(e.ts).slice(11, 19);
}

const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

export default function VehicleEvent({ e }: { e: TimelineEvent }) {
  const [open, setOpen] = useState(false);
  const expandable = Boolean(e.investigation);
  return (
    <li className="relative pl-4">
      <span aria-hidden className="absolute left-0 top-2 h-1.5 w-1.5 rounded-full bg-line-strong" />
      <button
        type="button"
        disabled={!expandable}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={expandable ? open : undefined}
        className={cn("flex w-full items-start gap-2 py-1 text-left", expandable && "hover:bg-surface-2")}
      >
        <span className="text-numeric w-16 shrink-0 pt-0.5 text-2xs text-ink-faint"
          title={e.clock === "wall" ? "real time" : "simulated line time"}>{eventTime(e)}</span>
        <EventSourceBadge source={e.source} />
        <span className={cn("min-w-0 flex-1 text-xs", e.flagged ? "font-bold text-warn-fg" : "text-ink")}>
          {e.text}
        </span>
        {expandable && (
          <IconChevronDown className={cn("mt-0.5 size-3.5 shrink-0 text-ink-faint transition-transform duration-200",
            open && "rotate-180")} />
        )}
      </button>
      <AnimatePresence initial={false}>
        {open && e.investigation && (
          <motion.div
            initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <AIInvestigation card={e.investigation} />
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  );
}

export function AIInvestigation({ card }: { card: InvestigationCard }) {
  const running = card.investigation_status && !["completed", "failed"].includes(card.investigation_status);
  return (
    <div className="my-1.5 ml-[5.5rem] space-y-2.5 border-l-2 border-ai/40 bg-surface-2 px-3 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-2xs font-bold uppercase tracking-[0.12em] text-ai-fg">
          AI investigation · {card.incident_id}
        </span>
        <span className={cn("text-2xs font-bold", running ? "text-info-fg" : "text-ok-fg")}>
          {running ? "● Investigating" : "✓ Investigation complete"}
        </span>
      </div>
      <Section title="Observation"><p className="text-xs text-ink">{card.observation}</p></Section>
      {card.evidence.length > 0 && (
        <Section title="Evidence">
          <ul className="space-y-0.5">
            {card.evidence.map((ev, i) => (
              <li key={i} className={cn("text-xs", ev.supports ? "text-ink" : "text-ink-dim")}>
                <span className={ev.supports ? "text-ok-fg" : "text-danger-fg"}>{ev.supports ? "✓" : "✗"}</span>{" "}
                {ev.text} <span className="text-2xs text-ink-faint">({ev.provenance})</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
      {card.hypotheses.length > 0 && (
        <Section title="Competing explanations">
          <ul className="space-y-1">
            {card.hypotheses.map((h) => (
              <li key={h.title} className="grid grid-cols-[minmax(0,1fr)_5rem_2.5rem] items-center gap-2 text-xs">
                <span className="truncate text-ink">{h.title}</span>
                <span className="h-1.5 bg-surface-3"><span className="block h-1.5 bg-ai" style={{ width: `${Math.max(2, h.confidence * 100)}%` }} /></span>
                <span className="text-numeric text-right text-ink-dim">{pct(h.confidence)}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
      {card.now && <Section title={running ? "Now" : "Conclusion"}><p className="text-xs text-ink">{card.now}</p></Section>}
      {card.recommendation?.action && (
        <Section title="Recommendation">
          <p className="text-xs text-ink">
            {card.recommendation.action}{" "}
            <span className="text-ink-faint">({card.recommendation.status.replace(/_/g, " ")})</span>
          </p>
        </Section>
      )}
      {card.follow_ups.length > 0 && (
        <Section title="What followed">
          <ul className="space-y-1">
            {card.follow_ups.map((f, i) => (
              <li key={i} className="flex items-start gap-2">
                <span className="text-numeric w-12 shrink-0 text-2xs text-ink-faint">{eventTime(f)}</span>
                <EventSourceBadge source={f.source} />
                <span className="text-xs text-ink">{f.text}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-0.5 text-2xs font-bold uppercase tracking-[0.12em] text-ink-faint">{title}</div>
      {children}
    </div>
  );
}
