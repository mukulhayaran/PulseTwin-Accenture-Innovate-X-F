"use client";

// The conversation. One component, used by /copilot and by the dock.
//
// WHY IT POLLS RATHER THAN STREAMS. The only thing worth streaming from a language model
// is its text, and streaming this model's text would render numbers on screen BEFORE
// genai/verifier.py has run -- a viewer would watch "$4.2M annual saving" type itself out
// and then vanish when it failed verification. That is the exact opposite of this
// feature's posture, so the run is non-streaming by design and what gets polled is PHASE
// STATUS: a ~400-byte document that changes maybe fifteen times per run. Polling renders
// that indistinguishably from a push, without an unauthenticated SSE endpoint on a
// single-worker server (EventSource cannot send X-PulseTwin-Role).

import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  StartTriageArgs, TriageIntent, TriageRun,
  getTriageAvailability, getTriageRun, getTriageThread, startTriage,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { Badge, Skeleton } from "@/components/ui";
import FindingView, { SectionLabel, VerdictStrip } from "./FindingView";
import PhaseTrail from "./PhaseTrail";

// 1Hz while a run is in flight, and NOT AT ALL when idle. The old console polled a flat
// 2s and kept polling after the run finished until the component unmounted.
const POLL_ACTIVE_MS = 1000;

const ACTIVE = new Set(["queued", "running"]);

export default function CopilotThread({
  plant,
  threadId,
  onThreadChange,
  onAction,
  onTurnComplete,
  compact,
  children,
}: {
  plant: string;
  threadId: string | null;
  onThreadChange: (id: string | null) => void;
  onAction?: (msg: string) => void;
  // Fires when a turn leaves queued/running, so a parent (the trust panel) can show the
  // verdict of the turn that actually just happened.
  onTurnComplete?: (run: TriageRun) => void;
  compact?: boolean;
  // The composer is passed in so the page and the dock can lay it out differently while
  // sharing every bit of conversation state.
  children?: (ask: (a: Omit<StartTriageArgs, "plant">) => void, busy: boolean) => React.ReactNode;
}) {
  const [turns, setTurns] = useState<TriageRun[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [phases, setPhases] = useState<string[]>([]);
  const [mode, setMode] = useState<"agent" | "deterministic" | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const poller = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reduce = useReducedMotion();
  const bottom = useRef<HTMLDivElement | null>(null);

  const stopPolling = useCallback(() => {
    if (poller.current) {
      clearTimeout(poller.current);
      poller.current = null;
    }
  }, []);

  useEffect(() => {
    getTriageAvailability()
      .then((a) => {
        setMode(a.mode);
        setPhases(a.phase_order ?? []);
      })
      .catch(() => setMode(null));
  }, []);

  // Load an existing conversation when the thread id changes (deep link, or the dock
  // adopting the thread the page was on).
  useEffect(() => {
    if (!threadId) {
      setTurns([]);
      return;
    }
    let cancelled = false;
    getTriageThread(threadId)
      .then((t) => {
        if (cancelled) return;
        setTurns(t.turns);
        if (t.phase_order?.length) setPhases(t.phase_order);
        const live = t.turns.find((x) => ACTIVE.has(x.status));
        if (live) setActiveRunId(live.run_id);
      })
      .catch(() => {
        if (!cancelled) onThreadChange(null);
      });
    return () => {
      cancelled = true;
    };
  }, [threadId, onThreadChange]);

  // Poll the in-flight turn. Self-rescheduling rather than setInterval so the cadence can
  // change with the run's state and so a slow response cannot stack up requests.
  useEffect(() => {
    if (!activeRunId) return;
    let cancelled = false;

    const tick = async () => {
      try {
        const run = await getTriageRun(activeRunId);
        if (cancelled) return;
        setTurns((prev) => {
          const i = prev.findIndex((t) => t.run_id === run.run_id);
          if (i === -1) return [...prev, run];
          const next = [...prev];
          next[i] = { ...next[i], ...run };
          return next;
        });
        if (!ACTIVE.has(run.status)) {
          setActiveRunId(null);
          setBusy(false);
          onAction?.(`Triage ${run.status.replace(/_/g, " ")}`);
          onTurnComplete?.(run);
          return;
        }
        poller.current = setTimeout(tick, POLL_ACTIVE_MS);
      } catch (e) {
        if (cancelled) return;
        setErr(String(e));
        setActiveRunId(null);
        setBusy(false);
      }
    };

    poller.current = setTimeout(tick, POLL_ACTIVE_MS);
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [activeRunId, onAction, onTurnComplete, stopPolling]);

  // Clear on unmount as well, in every path -- the old console leaked its interval when
  // startTriage threw.
  useEffect(() => stopPolling, [stopPolling]);

  useEffect(() => {
    if (!compact) bottom.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth" });
  }, [turns.length, compact, reduce]);

  const ask = useCallback(
    async (args: Omit<StartTriageArgs, "plant">) => {
      if (busy) return;
      setBusy(true);
      setErr(null);
      try {
        const r = await startTriage({ ...args, plant, threadId });
        if (!threadId) onThreadChange(r.thread_id);
        // Optimistically place the turn so the operator's own words appear instantly,
        // before the worker has written anything.
        setTurns((prev) => [
          ...prev,
          {
            run_id: r.run_id,
            status: "queued",
            question: args.question ?? undefined,
            intent: r.intent,
            turn_index: r.turn_index,
            mode: r.mode as "agent" | "deterministic",
          },
        ]);
        setActiveRunId(r.run_id);
        onAction?.(`Triage started (${r.mode}, acting as ${r.acting_role})`);
      } catch (e) {
        setErr(String(e));
        setBusy(false);
      }
    },
    [busy, plant, threadId, onThreadChange, onAction]
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        className={cn(
          "min-h-0 flex-1 space-y-3 overflow-y-auto pr-1",
          compact ? "max-h-[52vh]" : ""
        )}
      >
        {turns.length === 0 && (
          <div className="py-8 text-center">
            <p className="text-xs text-ink-dim">
              Ask about the line, or run a named action.
            </p>
            <p className="mt-1 text-2xs text-ink-faint">
              {mode === "agent"
                ? "An agent investigates through the read-only API, then every number it returns is re-checked against the tool results it saw."
                : "No provider credential is configured, so a deterministic rule-based path answers instead — through the same tools and the same verifier."}
            </p>
          </div>
        )}

        <AnimatePresence initial={false}>
          {turns.map((turn) => (
            <motion.div
              key={turn.run_id}
              initial={reduce ? false : { opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2 }}
              className="space-y-2"
            >
              <Turn turn={turn} phases={phases} />
            </motion.div>
          ))}
        </AnimatePresence>

        {err && (
          <p className="rounded-md border border-danger/30 bg-danger/[0.06] px-2.5 py-1.5 text-2xs text-danger-fg">
            {err}
          </p>
        )}
        <div ref={bottom} />
      </div>

      {children?.(ask, busy)}
    </div>
  );
}

function Turn({ turn, phases }: { turn: TriageRun; phases: string[] }) {
  const active = ACTIVE.has(turn.status);
  const label = turn.intent && turn.intent !== "ask" ? turn.intent.replace(/_/g, " ") : null;

  return (
    <div className="space-y-2">
      {/* the operator's turn */}
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg rounded-br-sm border border-line-strong bg-surface-2 px-3 py-1.5">
          <p className="text-xs text-ink">{turn.question}</p>
          {label && (
            <div className="mt-1">
              <Badge variant="info">{label}</Badge>
            </div>
          )}
        </div>
      </div>

      {/* the agent's turn */}
      <div className="rounded-lg border border-line bg-surface/60 p-3">
        {phases.length > 0 && (
          <div className="mb-3">
            <PhaseTrail
              phases={phases}
              progress={turn.progress}
              tools={turn.tool_ledger}
              done={!active}
            />
          </div>
        )}

        {!active && <VerdictStrip run={turn} />}

        {active && !turn.finding && (
          <div className="mt-3 space-y-2">
            <Skeleton className="h-3 w-2/3" />
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-4/5" />
          </div>
        )}

        {!active && (
          <div className="mt-3">
            <FindingView run={turn} />
          </div>
        )}
      </div>
    </div>
  );
}

export { SectionLabel };
export type { TriageIntent };
