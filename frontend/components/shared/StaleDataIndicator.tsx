"use client";

// Tiny inline freshness dot for any live value on any dashboard. Deliberately has no
// opinion on WHAT timestamp it's given -- a caller must pass a real wall-clock ISO
// timestamp (e.g. when a value was last received/computed), never simulated replay
// time, or the age computed here is meaningless.

import { useEffect, useState } from "react";

interface StaleDataIndicatorProps {
  timestamp: string | null;
}

const FRESH_MS = 30_000;
const WARN_MS = 5 * 60_000;

function fmtAge(ageMs: number): string {
  if (ageMs < 1_000) return "now";
  if (ageMs < 60_000) return `${Math.round(ageMs / 1_000)}s`;
  if (ageMs < 3_600_000) return `${Math.round(ageMs / 60_000)}m`;
  return `${Math.round(ageMs / 3_600_000)}h`;
}

export default function StaleDataIndicator({ timestamp }: StaleDataIndicatorProps) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  if (!timestamp) {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] text-txt-muted" title="no data yet">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-txt-muted/60" />
        —
      </span>
    );
  }

  const ageMs = Math.max(0, now - new Date(timestamp).getTime());
  const color =
    ageMs < FRESH_MS ? "bg-signal-green" : ageMs < WARN_MS ? "bg-signal-amber" : "bg-signal-red";

  return (
    <span
      className="inline-flex items-center gap-1 text-[10px] text-txt-muted"
      title={`last updated ${fmtAge(ageMs)} ago`}
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${color}`} />
      {fmtAge(ageMs)}
    </span>
  );
}
