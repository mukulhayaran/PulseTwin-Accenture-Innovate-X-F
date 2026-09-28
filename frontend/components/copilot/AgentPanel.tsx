"use client";

// The agents, on /supervisor, /manager and /ops -- deliberately NOT the copilot. The copilot is a
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
  Bottleneck, ExperimentListItem, TriageIntent, TriageRun,
  getBottleneck, getRole, getSession, getTriageAvailability, getTriageRun,
  listSimulationExperiments, startTriage,
} from "@/lib/api";
import LifecycleStrip, { runLifecycle } from "@/components/agents/LifecycleStrip";
import ExperimentComparison from "@/components/agents/ExperimentComparison";
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
}

// `hint` restates genai/intents.py's guidance for each plan in one line -- what the run
// will actually do, not a sales line.
const AGENTS: AgentDef[] = [
  // Specialists run ONE AFTER ANOTHER (deterministic call ids, one ledger) -- not in parallel.
  { intent: "incident", title: "Incident agent", hint: "Reliability, production and risk specialists in sequence, a simulation planner that runs a fresh experiment, then one verified synthesis." },
  { intent: "whatif", title: "What-if agent", hint: "A fresh paired simulation of more than one change at the live constraint; the precomputed grid only as a labelled fallback.", needsStation: true },
  { intent: "handover", title: "Handover agent", hint: "What changed, what was applied or awaits approval, outcomes not yet mature, and what to watch." },
  // A watch firing opens an Incident Commander incident (ADR-069); the ADR-051 watch agents
  // were retired (ADR-073). Shift intelligence was removed -- it re-read the same shift
  // summary as Handover.
];

export default function AgentPanel({
  plant,
  onAction,
  intents,
}: {
  plant: string;
  onAction?: (msg: string) => void;
  /** Which agents this view offers. Omitted = all of them (/ops). The API's own gate
   *  (serve/routers/triage.py SUPERVISOR_INTENTS) decides what a role may actually run. */
  intents?: TriageIntent[];
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
  const shown = intents ? AGENTS.filter((a) => intents.includes(a.intent)) : AGENTS;

  return (
    <Panel
      title="Agents"
      note="Each agent is a separate job over live line data. Run one, and its result appears on its own card once every number has been verified."
    >
      <div className={cn("grid grid-cols-1 gap-px border border-line bg-line",
                         shown.length > 1 && "xl:grid-cols-2")}>
        {shown.map((a) => (
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
  const [experiments, setExperiments] = useState<ExperimentListItem[]>([]);
  const poller = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stop = useCallback(() => {
    if (poller.current) clearTimeout(poller.current);
    poller.current = null;
  }, []);
  useEffect(() => stop, [stop]);

  const active = run != null && ACTIVE.has(run.status);

  // ADR-064: the experiments this run started, found by its id -- re-read while it runs
  // (a new experiment can appear mid-run) and once more when it finishes.
  const runId = run?.run_id;
  useEffect(() => {
    // The experiment list is manager-gated; a supervisor reads the finding, not the table.
    if (!runId || getRole() === "supervisor") return;
    let cancelled = false;
    const read = () =>
      listSimulationExperiments(plant, { originRunId: runId })
        .then((r) => !cancelled && setExperiments(r.experiments))
        .catch(() => undefined);
    read();
    const id = active ? setInterval(read, 3000) : null;
    return () => {
      cancelled = true;
      if (id) clearInterval(id);
    };
  }, [plant, runId, active]);
  // /supervisor is public, and the API starts floor agents only for a signed-in caller.
  const signedIn = getSession() != null;
  const blocked = (agent.needsStation && station == null) || !signedIn;
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
      setExperiments([]);
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
          {!signedIn && (
            <p className="mt-1 text-2xs font-bold text-ink-dim">Sign in to run this agent.</p>
          )}
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
          <div className="mb-2 flex flex-wrap items-center gap-2">
            {run.mode && (
              <Badge variant={run.mode === "agent" ? "ai" : "neutral"}>
                {run.mode === "agent" ? "AI agent" : "Deterministic fallback"}
              </Badge>
            )}
            {active && (
              <Badge variant={STATUS_VARIANT[run.status] ?? "neutral"} dot>
                {run.status}
              </Badge>
            )}
          </div>
          <div className="mb-3">
            <LifecycleStrip lifecycle={runLifecycle(run, experiments)} />
          </div>
          {agent.intent === "incident" &&
            (run.progress?.agents?.length || run.agents_used?.length) && (
              <AgentTrail agents={run.progress?.agents ?? run.agents_used ?? []} done={!active} />
            )}
          {phases.length > 0 && (
            <div className="mb-3">
              <PhaseTrail phases={phases} progress={run.progress} tools={run.tool_ledger} done={!active} />
            </div>
          )}
          {experiments.map((e) => (
            <details key={e.experiment_id} className="mb-3" open={!active}>
              <summary className="cursor-pointer text-2xs text-ink-dim">
                <span className="font-bold text-ink">Simulation experiment</span>{" "}
                {e.experiment_id} · S{e.station_id} · {e.seed_count} seeds · {e.status}
                {e.verdict ? ` · ${e.verdict.replace(/_/g, " ")}` : ""}
              </summary>
              <div className="mt-2">
                <ExperimentComparison experimentId={e.experiment_id} />
              </div>
            </details>
          ))}
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
