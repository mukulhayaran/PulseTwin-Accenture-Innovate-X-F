"use client";

// The agents, on /manager and /ops -- deliberately NOT the copilot. The copilot is a
// conversation; an agent is a job. Each card here owns one agent: its own Run button, its
// own in-flight run, its own verified result. Nothing is threaded between them, and each
// run opens its own server-side thread with a non-"ask" intent, so none of them appears in
// the copilot's conversation list.
//
// Same run contract as CopilotThread: start, then poll the ~400-byte phase document at
// 1Hz while active and not at all when idle; the finding renders only after
// genai/verifier.py has graded it.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bottleneck, TriageIntent, TriageRun,
  getBottleneck, getTriageAvailability, getTriageRun, startTriage,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { Badge, Button, Panel } from "@/components/ui";
import FindingView, { STATUS_VARIANT, VerdictStrip } from "./FindingView";
import PhaseTrail from "./PhaseTrail";
import AgentTrail from "./AgentTrail";

const POLL_MS = 1000;
const ACTIVE = new Set(["queued", "running"]);

interface AgentDef {
  intent: TriageIntent;
  title: string;
  hint: string;
  needsStation?: boolean;
  /** Started automatically by the live watch (ADR-051); proposes, never executes. */
  watch?: boolean;
}

// `hint` restates genai/intents.py's guidance for each plan in one line -- what the run
// will actually do, not a sales line.
const AGENTS: AgentDef[] = [
  { intent: "incident", title: "Incident agent", hint: "Reliability, production and risk specialists investigate in parallel, then one verified synthesis." },
  { intent: "whatif", title: "What-if agent", hint: "Baseline vs projected on the simulation grid for the live constraint — or says it cannot.", needsStation: true },
  { intent: "shift_intel", title: "Shift-intelligence agent", hint: "Top three sources of loss this shift, ranked and qualified." },
  { intent: "handover", title: "Handover agent", hint: "What changed, what is open, and what the next shift must watch." },
  // ADR-051: normally started by the live watch the moment it fires; runnable here too.
  // Each PROPOSES an action -- decided in the Proposals inbox, never executed by the agent.
  { intent: "starvation", watch: true, title: "Starvation agent", hint: "Reads the watch's stall evidence, tests a feed buffer on the grid, proposes a ticket and a buffer change." },
  { intent: "paint_env", watch: true, title: "Paint environment agent", hint: "Reads the zone humidity excess, cites the paint SOP, proposes an HVAC setpoint and a resequence." },
  { intent: "tool_wear", watch: true, title: "Tool degradation agent", hint: "Reads the torque / weld-current drift, proposes a maintenance ticket and a load shift to an adjacent station." },
];

export default function AgentPanel({
  plant,
  onAction,
}: {
  plant: string;
  onAction?: (msg: string) => void;
}) {
  const [phases, setPhases] = useState<string[]>([]);
  const [bn, setBn] = useState<Bottleneck | null>(null);

  useEffect(() => {
    getTriageAvailability()
      .then((a) => setPhases(a.phase_order ?? []))
      .catch(() => setPhases([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    getBottleneck(plant)
      .then((b) => !cancelled && setBn(b))
      .catch(() => !cancelled && setBn(null));
    return () => {
      cancelled = true;
    };
  }, [plant]);

  // Structured, from the live constraint -- never parsed out of text (CLAUDE.md §1).
  const station = bn?.current_station ?? null;

  return (
    <Panel
      title="Agents"
      note="Each agent is a separate job over live line data. Run one, and its result appears on its own card once every number has been verified."
    >
      {[
        { label: "On demand", cols: "xl:grid-cols-2", agents: AGENTS.filter((a) => !a.watch) },
        { label: "Watch agents · started automatically when the live watch fires, and each proposes an action for approval",
          cols: "xl:grid-cols-3", agents: AGENTS.filter((a) => a.watch) },
      ].map((group) => (
        <div key={group.label} className="mb-4 last:mb-0">
          <div className="mb-2 text-2xs font-bold uppercase tracking-[0.14em] text-ink-faint">
            {group.label}
          </div>
          <div className={cn("grid grid-cols-1 gap-px border border-line bg-line", group.cols)}>
            {group.agents.map((a) => (
              // key on plant: a plant switch clears every card -- runs are plant-scoped.
              <AgentCard
                key={`${plant}:${a.intent}`}
                agent={a}
                plant={plant}
                station={station}
                phases={phases}
                onAction={onAction}
              />
            ))}
          </div>
        </div>
      ))}
    </Panel>
  );
}

function AgentCard({
  agent,
  plant,
  station,
  phases,
  onAction,
}: {
  agent: AgentDef;
  plant: string;
  station: number | null;
  phases: string[];
  onAction?: (msg: string) => void;
}) {
  const [run, setRun] = useState<TriageRun | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const poller = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stop = useCallback(() => {
    if (poller.current) clearTimeout(poller.current);
    poller.current = null;
  }, []);
  useEffect(() => stop, [stop]);

  const active = run != null && ACTIVE.has(run.status);
  const blocked = agent.needsStation && station == null;
  const label = agent.needsStation && station != null ? `${agent.title} · S${station}` : agent.title;

  const poll = useCallback(
    (runId: string) => {
      const tick = async () => {
        try {
          const r = await getTriageRun(runId);
          setRun((prev) => ({ ...prev, ...r }));
          if (ACTIVE.has(r.status)) {
            poller.current = setTimeout(tick, POLL_MS);
          } else {
            onAction?.(`${agent.title} ${r.status.replace(/_/g, " ")}`);
          }
        } catch (e) {
          setErr(String(e));
        }
      };
      poller.current = setTimeout(tick, POLL_MS);
    },
    [agent.title, onAction]
  );

  const start = async () => {
    if (active || blocked) return;
    stop();
    setErr(null);
    try {
      const r = await startTriage({
        plant,
        intent: agent.intent,
        stationId: agent.needsStation ? station : null,
      });
      setRun({ run_id: r.run_id, status: "queued", intent: r.intent,
               mode: r.mode as "agent" | "deterministic" });
      onAction?.(`${agent.title} started (${r.mode}, acting as ${r.acting_role})`);
      poll(r.run_id);
    } catch (e) {
      setErr(String(e));
    }
  };

  return (
    <div className="flex min-w-0 flex-col bg-canvas p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-bold text-ink">{label}</h3>
          <p className="mt-1 text-2xs leading-relaxed text-ink-faint">{agent.hint}</p>
        </div>
        <Button
          variant={run ? "outline" : "primary"}
          size="xs"
          onClick={start}
          disabled={active || blocked}
          className="shrink-0"
        >
          {active ? "Running…" : run ? "Run again" : "Run agent"}
        </Button>
      </div>

      {err && (
        <p className="mt-3 border border-danger/30 bg-danger/[0.06] px-2.5 py-1.5 text-2xs text-danger-fg">
          {err}
        </p>
      )}

      {run && (
        <div className="mt-3 border-t border-line pt-3">
          {active && (
            <div className="mb-2">
              <Badge variant={STATUS_VARIANT[run.status] ?? "neutral"} dot>
                {run.status}
              </Badge>
            </div>
          )}
          {agent.intent === "incident" &&
            (run.progress?.agents?.length || run.agents_used?.length) && (
              <AgentTrail agents={run.progress?.agents ?? run.agents_used ?? []} done={!active} />
            )}
          {phases.length > 0 && (
            <div className="mb-3">
              <PhaseTrail phases={phases} progress={run.progress} tools={run.tool_ledger} done={!active} />
            </div>
          )}
          {!active && (
            <div className="scroll-fade-b max-h-[28rem] overflow-y-auto pr-1">
              <VerdictStrip run={run} />
              <div className="mt-3">
                <FindingView run={run} />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
