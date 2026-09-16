"use client";

// Compact horizontal step visual for GET /api/alerts/escalation-policy's `levels`.
// Not a full-page illustration -- three small linked boxes, dense and monospace like
// everything else in the control room.

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
    <div className="flex flex-wrap items-center gap-1.5 font-mono text-[11px]">
      {levels.map((l, i) => {
        const active = l.level === currentLevel;
        return (
          <div key={l.level} className="flex items-center gap-1.5">
            <div
              className={`rounded border px-2 py-1 ${
                active
                  ? "border-signal-red bg-signal-red/10 text-signal-red"
                  : "border-bg-surface text-txt-muted"
              }`}
            >
              <div className="text-[9px] uppercase tracking-widest">{labelFor(l.name)}</div>
              <div className={active ? "text-signal-red" : "text-txt-primary"}>
                {l.response_window_min != null ? `${l.response_window_min} min` : "no SLA"}
              </div>
            </div>
            {i < levels.length - 1 && <span className="text-txt-muted">→</span>}
          </div>
        );
      })}
    </div>
  );
}
