"use client";

// The activity trail: what the agent is doing, right now.
//
// This is the answer to "the panel shows a spinner for twenty seconds". The phases come
// from the BACKEND (ToolSpec.phase, mirrored in serve/routers/triage.py::PHASE_ORDER),
// not from a map in this file -- a UI-side map desynchronises the moment a tool is added
// and gives the deterministic path no labels at all.
//
// It shows only tool and phase STATUS. Never model prose: `openai/gpt-oss-120b` returns
// a `reasoning` field and the heartbeat filters it out by allowlist
// (genai/triage.py::_HEARTBEAT_KEYS), with a test asserting it never leaks.

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { TriageProgress, TriageToolEvent } from "@/lib/api";
import { cn } from "@/lib/cn";

export default function PhaseTrail({
  phases,
  progress,
  tools,
  done,
}: {
  phases: string[];
  progress?: TriageProgress | null;
  tools?: TriageToolEvent[];
  done?: boolean;
}) {
  const reduce = useReducedMotion();
  if (!phases.length) return null;

  // Which steps have been reached. Prefer the live index; fall back to the furthest
  // phase any recorded tool call reported, so a finished run still renders its trail
  // from the persisted ledger rather than collapsing to nothing.
  const fromTools = (tools ?? []).reduce((max, t) => {
    const i = t.phase ? phases.indexOf(t.phase) : -1;
    return i > max ? i : max;
  }, -1);
  const current = done
    ? phases.length - 1
    : Math.max(progress?.phase_index ?? -1, fromTools);

  return (
    <div>
      <ol className="flex flex-wrap items-center gap-x-1 gap-y-1.5">
        {phases.map((phase, i) => {
          const reached = i <= current;
          const active = !done && i === current;
          return (
            <li key={phase} className="flex items-center gap-1">
              <span
                className={cn(
                  "rounded-md border px-1.5 py-0.5 text-2xs transition-colors",
                  active
                    ? "border-ai/50 bg-ai/10 text-ai-fg"
                    : reached
                      ? "border-line-strong bg-surface-2 text-ink-dim"
                      : "border-line bg-surface/40 text-ink-faint/60"
                )}
              >
                {active && !reduce && (
                  <motion.span
                    aria-hidden
                    className="mr-1 inline-block h-1 w-1 rounded-full bg-ai-fg align-middle"
                    animate={{ opacity: [1, 0.25, 1] }}
                    transition={{ duration: 1.1, repeat: Infinity }}
                  />
                )}
                {phase}
              </span>
              {i < phases.length - 1 && (
                <span aria-hidden className="text-2xs text-ink-faint/50">
                  ›
                </span>
              )}
            </li>
          );
        })}
      </ol>

      {progress?.iteration != null && progress.max_iterations != null && !done && (
        <p className="text-numeric mt-1.5 text-2xs text-ink-faint">
          round {progress.iteration}/{progress.max_iterations}
          {progress.elapsed_s != null && ` · ${progress.elapsed_s.toFixed(1)}s`}
        </p>
      )}

      {tools && tools.length > 0 && (
        <ol className="mt-2 flex flex-wrap gap-1.5">
          <AnimatePresence initial={false}>
            {tools.map((t, i) => (
              <motion.li
                key={t.call_id || `pending-${t.tool_name}-${i}`}
                initial={reduce ? false : { opacity: 0, scale: 0.94 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ duration: 0.18 }}
                className={cn(
                  "text-numeric rounded-md border px-1.5 py-0.5 text-2xs",
                  t.status === "running"
                    ? "border-ai/40 bg-ai/[0.07] text-ai-fg"
                    : "border-line bg-surface-2 text-ink-dim"
                )}
              >
                {t.call_id && <span className="text-ink-faint">{t.call_id} </span>}
                {t.tool_name}
                {t.status === "running" && <span className="text-ink-faint"> …</span>}
              </motion.li>
            ))}
          </AnimatePresence>
        </ol>
      )}
    </div>
  );
}
