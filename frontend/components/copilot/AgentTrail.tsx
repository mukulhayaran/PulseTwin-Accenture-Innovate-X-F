"use client";

// The specialist roster for an orchestrated `incident` run (ADR-045): Reliability,
// Production and Risk each get their own bounded tool-calling loop, reconciled by a
// synthesizer. This renders which of those is pending/running/done -- NOT a second phase
// trail. PhaseTrail.tsx (below this, in CopilotThread.tsx) still shows the per-tool
// activity; this shows who is doing it.
//
// Same "prefer the live heartbeat, fall back to the persisted record" pattern
// PhaseTrail.tsx already uses for its own tool trail -- a finished run still renders its
// roster from `agents_used` once the UI has switched from polling to reading the ledger.

import { motion, useReducedMotion } from "motion/react";
import { TriageAgentStatus } from "@/lib/api";
import Badge from "@/components/ui/Badge";
import { cn } from "@/lib/cn";

const VARIANT: Record<TriageAgentStatus["status"], "neutral" | "ai" | "ok" | "warn" | "danger"> = {
  pending: "neutral",
  running: "ai",
  ok: "ok",
  partial: "warn",
  error: "danger",
};

export default function AgentTrail({
  agents,
  done,
}: {
  agents: TriageAgentStatus[];
  done?: boolean;
}) {
  const reduce = useReducedMotion();
  if (!agents.length) return null;

  return (
    <ol className="mb-2 flex flex-wrap items-center gap-1.5">
      {agents.map((a) => {
        const active = !done && a.status === "running";
        return (
          <li key={a.key}>
            <Badge variant={VARIANT[a.status]} className={cn(active && "relative")}>
              {active && !reduce && (
                <motion.span
                  aria-hidden
                  className="inline-block size-1.5 rounded-full bg-current"
                  animate={{ opacity: [1, 0.25, 1] }}
                  transition={{ duration: 1.1, repeat: Infinity }}
                />
              )}
              {a.label}
              {a.tool_calls > 0 && (
                <span className="text-numeric normal-case tracking-normal opacity-70">
                  {" "}
                  {a.tool_calls}
                </span>
              )}
            </Badge>
          </li>
        );
      })}
    </ol>
  );
}
