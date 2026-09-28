"use client";

// Where an event came from, always visible (ADR-071, contract 1). A measured reading and an
// AI inference NEVER share a badge: SENSOR is green, INFERRED keeps the project's amber
// "inferred" colour, AI is the prediction violet.

import { Badge, Tooltip } from "@/components/ui";
import type { EventSource } from "@/lib/api";

const SOURCES: Record<EventSource, { label: string; variant: any; tip: string }> = {
  SENSOR: { label: "SENSOR", variant: "ok", tip: "A direct measurement from a station sensor." },
  INFERRED: { label: "INFERRED", variant: "warn", tip: "Estimated by a soft sensor -- NOT a direct measurement." },
  MANUAL: { label: "MANUAL", variant: "info", tip: "Recorded by a person: a checklist, an acknowledgement or a floor decision." },
  AI: { label: "AI", variant: "ai", tip: "An AI inference or investigation finding -- a conclusion, not a measurement." },
  SIMULATION: { label: "SIMULATION", variant: "ai", tip: "A digital-twin simulation result." },
  SYSTEM: { label: "SYSTEM", variant: "neutral", tip: "Recorded by the line or an enterprise system (station passage, work order)." },
  VERIFIED: { label: "VERIFIED", variant: "ok", tip: "Confirmed by inspection or by a measured outcome." },
  MANAGER: { label: "MANAGER", variant: "solid", tip: "A manager's decision." },
};

export default function EventSourceBadge({ source }: { source: EventSource }) {
  const s = SOURCES[source] ?? SOURCES.SYSTEM;
  return (
    <Tooltip content={s.tip}>
      <span className="inline-flex w-[5.5rem] shrink-0"><Badge variant={s.variant}>{s.label}</Badge></span>
    </Tooltip>
  );
}
