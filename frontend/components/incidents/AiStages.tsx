"use client";

// What the AI is doing, as stages (LOOP criterion G). Every status comes from the
// incident's persisted record (serve/incident_story.py::stages); nothing here advances on
// a timer -- the track moves when the backend state moves.

import type { IncidentStage } from "@/lib/api";
import { cn } from "@/lib/cn";

const MARK: Record<IncidentStage["status"], string> = { done: "✓", active: "●", pending: "○", skipped: "–" };

export default function AiStages({ stages }: { stages: IncidentStage[] }) {
  return (
    <ol className="grid grid-cols-2 gap-px bg-line sm:grid-cols-4 xl:grid-cols-7" aria-label="AI progress">
      {stages.map((s) => (
        <li key={s.key} className={cn("bg-surface px-2.5 py-2", s.status === "active" && "bg-ai/8")}
          aria-current={s.status === "active" ? "step" : undefined}>
          <div className="flex items-center gap-1.5">
            <span aria-hidden className={cn("text-numeric w-3 text-center text-xs font-bold",
              s.status === "done" ? "text-ok-fg" : s.status === "active" ? "animate-pulse text-ai-fg"
                : "text-ink-faint")}>{MARK[s.status]}</span>
            <span className={cn("text-2xs font-bold uppercase tracking-[0.1em]",
              s.status === "pending" || s.status === "skipped" ? "text-ink-faint" : "text-ink")}>{s.label}</span>
          </div>
          {s.detail && <div className="mt-0.5 truncate pl-[1.125rem] text-2xs text-ink-faint" title={s.detail}>{s.detail}</div>}
        </li>
      ))}
    </ol>
  );
}
