"use client";

// Compact horizontal step visual for GET /api/alerts/escalation-policy's `levels`.
// Not a full-page illustration -- linked chips, dense like everything else in the view.

import { cn } from "@/lib/cn";

interface Level {
  level: number;
  name: string;
  response_window_min: number | null;
}

function labelFor(name: string): string {
  return name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function EscalationChain({
  levels,
  currentLevel,
}: {
  levels: Level[];
  currentLevel?: number;
}) {
  if (!levels || levels.length === 0) return null;

  return (
    <ol className="flex flex-wrap items-stretch gap-1.5">
      {levels.map((l, i) => {
        const active = l.level === currentLevel;
        const passed = currentLevel != null && l.level < currentLevel;
        return (
          <li key={l.level} className="flex items-center gap-1.5">
            <div
              className={cn(
                "rounded-lg border px-2.5 py-1.5 transition-colors",
                active
                  ? "border-danger/50 bg-danger/[0.09]"
                  : passed
                    ? "border-line-strong bg-surface-2"
                    : "border-line bg-surface-2/40"
              )}
            >
              <div
                className={cn(
                  "text-2xs uppercase tracking-[0.12em]",
                  active ? "text-danger-fg" : "text-ink-faint"
                )}
              >
                {labelFor(l.name)}
              </div>
              <div
                className={cn(
                  "text-numeric text-xs font-medium",
                  active ? "text-danger-fg" : "text-ink-dim"
                )}
              >
                {l.response_window_min != null ? `${l.response_window_min} min` : "no SLA"}
              </div>
            </div>
            {i < levels.length - 1 && (
              <span aria-hidden className="text-ink-faint/60">
                →
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
