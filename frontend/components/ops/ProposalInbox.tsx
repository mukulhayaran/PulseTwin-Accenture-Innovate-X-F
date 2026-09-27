"use client";

// The human half of ADR-051: watch fires -> agent investigates -> agent PROPOSES ->
// a person decides here -> the twin applies -> the watch confirms recovery.
//
// Every proposal shows the MEASURED evidence that made the watch fire, the agent's
// verified finding, and only the actions the verifier served (an action resting on a
// redacted number never reaches this list). Approve is manager-and-above here and again
// on the server; an agent has no tool that can reach these endpoints.

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActionProposal, ProposalStatus, approveProposal, getRole, listProposals,
  rejectProposal, roleAtLeast,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { Badge, Button, Panel } from "@/components/ui";
import { ActionRow, ACTION_LABEL, describeParams } from "@/components/copilot/FindingView";
import { ForecastHeader, ImpactStrip, impactScale } from "@/components/ops/TwinForecast";
import ExperimentComparison from "@/components/agents/ExperimentComparison";
import OutcomeCard from "@/components/agents/OutcomeCard";

const POLL_MS = 4000;

const DETECTOR_LABEL: Record<ActionProposal["detector"], string> = {
  starvation: "Starvation risk",
  paint_env: "Paint environment",
  tool_wear: "Tool degradation",
};

const STATUS_TONE: Record<ProposalStatus, "ok" | "warn" | "danger" | "info" | "neutral" | "ai"> = {
  INVESTIGATING: "info",
  SIMULATING: "info",
  PROPOSED: "ai",
  NO_ACTION: "neutral",
  AGENT_FAILED: "danger",
  NO_AGENT: "neutral",
  APPROVED: "warn",
  APPLIED: "info",
  EFFECTIVE: "ok",
  REJECTED: "neutral",
};

const STATUS_TEXT: Record<ProposalStatus, string> = {
  INVESTIGATING: "agent investigating",
  SIMULATING: "twin simulating",
  PROPOSED: "awaiting decision",
  NO_ACTION: "no action warranted",
  AGENT_FAILED: "agent failed",
  NO_AGENT: "no agent run",
  APPROVED: "approved - twin not running",
  APPLIED: "applied - awaiting recovery",
  EFFECTIVE: "effective - recovered",
  REJECTED: "rejected",
};

// Evidence keys that are identity, not measurement -- shown in the header instead.
const HIDDEN_EVIDENCE = new Set(["station_id", "station_name"]);

function formatEvidence(value: number | string | number[] | null): string {
  if (value == null) return "-";
  // down_stations (ADR-059) is a list; React would otherwise render [4, 15] as "415".
  if (Array.isArray(value)) return value.length ? value.map((s) => `S${s}`).join(", ") : "none";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  return value;
}

const DECISION_SUMMARY: Record<ProposalStatus, {
  title: string;
  detail: string;
  tone: "ok" | "warn" | "danger" | "info" | "neutral" | "ai";
}> = {
  INVESTIGATING: {
    title: "Investigation in progress",
    detail: "No manager approval is requested yet.",
    tone: "info",
  },
  SIMULATING: {
    title: "Testing possible actions",
    detail: "No manager approval is requested yet.",
    tone: "info",
  },
  PROPOSED: {
    title: "Manager approval required",
    detail: "Review the evidence and choose an action below, or reject the proposal.",
    tone: "ai",
  },
  NO_ACTION: {
    title: "Final result - no approval required",
    detail: "The investigation found no verified action worth applying.",
    tone: "neutral",
  },
  AGENT_FAILED: {
    title: "Final result - no approval required",
    detail: "The investigation failed, so no action was offered for approval.",
    tone: "danger",
  },
  NO_AGENT: {
    title: "Final result - no approval required",
    detail: "No agent result was produced for this watch event.",
    tone: "neutral",
  },
  APPROVED: {
    title: "Manager approved",
    detail: "The twin is not running, so the approved action has not been applied.",
    tone: "warn",
  },
  APPLIED: {
    title: "Manager approved - monitoring result",
    detail: "The action was applied to the simulated twin and is being evaluated.",
    tone: "info",
  },
  EFFECTIVE: {
    title: "Final result - action effective",
    detail: "The simulated twin recovered after the manager-approved action.",
    tone: "ok",
  },
  REJECTED: {
    title: "Final result - rejected by manager",
    detail: "The proposed action was not applied.",
    tone: "neutral",
  },
};

function DecisionSummary({ proposal }: { proposal: ActionProposal }) {
  const summary = DECISION_SUMMARY[proposal.status];
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 border border-line bg-surface-2 px-2.5 py-2">
      <Badge variant={summary.tone} dot={proposal.status === "PROPOSED"}>
        {summary.title}
      </Badge>
      <span className="text-2xs leading-relaxed text-ink-dim">{summary.detail}</span>
    </div>
  );
}

const TERMINAL_STATUSES = new Set<ProposalStatus>([
  "NO_ACTION", "AGENT_FAILED", "NO_AGENT", "EFFECTIVE", "REJECTED",
]);

function incidentKey(proposal: ActionProposal) {
  return `${proposal.detector}:${proposal.station_id}:${proposal.fired_at}`;
}

function proposalTime(proposal: ActionProposal) {
  return Date.parse(proposal.created_at || proposal.fired_at) || 0;
}

function displayFindingSummary(proposal: ActionProposal) {
  const summary = proposal.finding?.summary;
  if (!summary || proposal.detector !== "starvation" || proposal.trigger_evidence.stalled_state !== "blocked") {
    return summary;
  }
  return summary.replace(
    `S${proposal.station_id} is stalled (blocked) and cycling slower than takt, so the station after it will draw its buffer dry.`,
    `S${proposal.station_id} is blocked and cannot release work downstream. This points to downstream congestion or backpressure, not missing upstream feed.`,
  );
}

function ProposalCard({
  proposal: p,
  canDecide,
  busy,
  onDecide,
  archived = false,
}: {
  proposal: ActionProposal;
  canDecide: boolean;
  busy: string | null;
  onDecide: (proposal: ActionProposal, actionId: string | null) => void;
  archived?: boolean;
}) {
  const evidence = Object.entries(p.trigger_evidence).filter(([k]) => !HIDDEN_EVIDENCE.has(k));
  const name = p.trigger_evidence.station_name;
  const decidable = p.status === "PROPOSED" && !archived;
  const scale = impactScale(p.counterfactual);
  const conflictingExperiment = p.status === "NO_ACTION"
    ? p.experiments?.find((experiment) => experiment.verdict === "improved")
    : undefined;
  const findingSummary = displayFindingSummary(p);

  return (
    <li className="border border-line bg-canvas p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-bold text-ink">{DETECTOR_LABEL[p.detector]}</span>
        <span className="text-numeric text-2xs text-ink-dim">
          S{p.station_id}{name ? ` - ${name}` : ""}
        </span>
        <span className="flex-1" />
        <Badge variant={STATUS_TONE[p.status]} dot={p.status === "INVESTIGATING" || p.status === "SIMULATING"}>
          {STATUS_TEXT[p.status]}
        </Badge>
      </div>
      <div className="text-numeric mt-0.5 text-2xs text-ink-faint">
        fired {p.fired_at.replace("T", " ").slice(0, 19)} | {p.proposal_id}
        {p.agent_mode ? ` | ${p.agent_mode === "agent" ? "AI agent" : "deterministic fallback"}` : ""}
      </div>
      {archived && !TERMINAL_STATUSES.has(p.status) ? (
        <div className="mt-2 border border-line bg-surface-2 px-2.5 py-2 text-2xs text-ink-dim">
          <span className="font-bold text-ink">Earlier run - retained for audit.</span>{" "}
          Use the newest record for this watch event; no decision is accepted here.
        </div>
      ) : (
        <DecisionSummary proposal={p} />
      )}

      {conflictingExperiment && (
        <div className="mt-2 border border-warn/40 bg-warn/[0.08] px-2.5 py-2 text-2xs leading-relaxed text-warn-fg">
          <span className="font-bold">Result needs review.</span>{" "}
          Experiment {conflictingExperiment.experiment_id} found an improvement
          {conflictingExperiment.best_label ? ` (${conflictingExperiment.best_label})` : ""},
          but this record finished with no proposed action.
        </div>
      )}

      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 border-t border-line pt-2 sm:grid-cols-3">
        {evidence.map(([k, v]) => (
          <div key={k} className="min-w-0">
            <dt className="truncate text-2xs text-ink-faint">{k.replace(/_/g, " ")}</dt>
            <dd className="text-numeric text-xs text-ink">{formatEvidence(v)}</dd>
          </div>
        ))}
      </dl>

      {findingSummary && (
        <p className="mt-2 text-2xs leading-relaxed text-ink-dim">{findingSummary}</p>
      )}

      <ForecastHeader cf={p.counterfactual} nothing={p.do_nothing_forecast} />

      {(p.experiments?.length ?? 0) > 0 && (
        <details className="mt-2 border-t border-line pt-2">
          <summary className="cursor-pointer text-2xs text-ink-dim">
            <span className="font-bold text-ink">Agent simulation experiment</span>{" "}
            {p.experiments![0].experiment_id} | {p.experiments![0].status}
            {p.experiments![0].verdict ? ` | ${p.experiments![0].verdict.replace(/_/g, " ")}` : ""}
            {p.experiments![0].best_label ? ` | best: ${p.experiments![0].best_label}` : ""}
          </summary>
          <div className="mt-2">
            <ExperimentComparison experimentId={p.experiments![0].experiment_id} />
          </div>
        </details>
      )}

      {p.outcome && (
        <div className="mt-2">
          <OutcomeCard outcome={p.outcome} />
        </div>
      )}

      {p.selected_action ? (
        <p className="mt-2 text-2xs text-ink-dim">
          <span className="font-bold text-ink">{ACTION_LABEL[p.selected_action.action_type]}</span>{" "}
          {describeParams(p.selected_action.params)} - decided by {p.decided_by_role}
          {p.effective_at ? `; recovery confirmed by the watch at ${p.effective_at.replace("T", " ").slice(0, 19)}` : ""}.
        </p>
      ) : (
        p.proposed_actions.length > 0 && (
          <ul className="mt-2 space-y-1.5">
            {p.proposed_actions.map((action) => (
              <ActionRow
                key={action.id}
                action={action}
                trailing={
                  decidable && (
                    <Button
                      variant="primary"
                      size="xs"
                      disabled={!canDecide || busy === p.proposal_id}
                      onClick={() => onDecide(p, action.id)}
                      title={canDecide ? undefined : "Requires the manager role or above"}
                      className="shrink-0"
                    >
                      Approve
                    </Button>
                  )
                }
              >
                <ImpactStrip arm={action.simulated_impact} scale={scale} />
              </ActionRow>
            ))}
          </ul>
        )
      )}

      {!archived && ["PROPOSED", "SIMULATING", "NO_ACTION", "AGENT_FAILED", "NO_AGENT", "INVESTIGATING"].includes(p.status) && (
        <div className="mt-2 flex justify-end">
          <Button
            variant="ghost"
            size="xs"
            disabled={!canDecide || busy === p.proposal_id}
            onClick={() => onDecide(p, null)}
            className={cn(!canDecide && "opacity-50")}
          >
            {decidable ? "Reject" : "Dismiss"}
          </Button>
        </div>
      )}
    </li>
  );
}

export default function ProposalInbox({
  plant,
  onAction,
}: {
  plant: string;
  onAction?: (msg: string) => void;
}) {
  const [proposals, setProposals] = useState<ActionProposal[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const canDecide = roleAtLeast(getRole(), "manager");

  const queue = useMemo(() => {
    const ordered = [...(proposals ?? [])].sort((a, b) => proposalTime(b) - proposalTime(a));
    const newest: ActionProposal[] = [];
    const earlierRuns: ActionProposal[] = [];
    const seen = new Set<string>();

    for (const proposal of ordered) {
      const key = incidentKey(proposal);
      if (seen.has(key)) earlierRuns.push(proposal);
      else {
        seen.add(key);
        newest.push(proposal);
      }
    }

    const current = newest
      .filter((proposal) => !TERMINAL_STATUSES.has(proposal.status))
      .sort((a, b) => {
        const attentionA = a.status === "PROPOSED" ? 1 : 0;
        const attentionB = b.status === "PROPOSED" ? 1 : 0;
        return attentionB - attentionA || proposalTime(b) - proposalTime(a);
      });
    const history = [
      ...newest.filter((proposal) => TERMINAL_STATUSES.has(proposal.status)),
      ...earlierRuns,
    ].sort((a, b) => proposalTime(b) - proposalTime(a));

    return { current, history };
  }, [proposals]);

  const load = useCallback(() => {
    listProposals(plant, 20)
      .then((r) => setProposals(r.proposals))
      .catch((e) => setErr(String(e)));
  }, [plant]);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const decide = async (p: ActionProposal, actionId: string | null) => {
    setBusy(p.proposal_id);
    setErr(null);
    try {
      if (actionId) {
        const r = await approveProposal(p.proposal_id, actionId);
        onAction?.(r.applied_to_twin ? "Approved and applied to the twin" : "Approved (twin not running)");
      } else {
        await rejectProposal(p.proposal_id, "Rejected from the proposals inbox");
        onAction?.("Proposal rejected");
      }
      load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
    }
  };

  const pending = queue.current.filter((proposal) => proposal.status === "PROPOSED").length;

  return (
    <Panel
      title="Proposals"
      note="When a simulation is running, PulseTwin watches the live event stream automatically. If a watch threshold is crossed, the matching agent starts, investigates and places any verified action here for manager approval. Approved actions affect the simulated twin only."
      actions={pending > 0 ? <Badge variant="ai" dot>{pending} awaiting decision</Badge> : undefined}
    >
      {err && (
        <p className="mb-3 border border-danger/30 bg-danger/[0.06] px-2.5 py-1.5 text-2xs text-danger-fg">{err}</p>
      )}
      {proposals === null ? (
        <p className="py-6 text-center text-2xs text-ink-faint">Loading proposals...</p>
      ) : proposals.length === 0 ? (
        <div className="py-8 text-center">
          <p className="text-xs text-ink-dim">No proposals yet.</p>
          <p className="mx-auto mt-1 max-w-lg text-2xs leading-relaxed text-ink-faint">
            Start a live simulation on the Supervisor view and inject high humidity, a
            station breakdown or torque degradation. The watch fires, an agent investigates,
            and its proposed action appears here for a decision.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {queue.current.length > 0 ? (
            <>
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-2xs font-bold uppercase tracking-[0.14em] text-ink-faint">
                  Manager queue
                </h3>
                <span className="text-2xs text-ink-faint">Newest automatic investigation per watch event</span>
              </div>
              <ul className="space-y-3">
                {queue.current.map((proposal) => (
                  <ProposalCard
                    key={proposal.proposal_id}
                    proposal={proposal}
                    canDecide={canDecide}
                    busy={busy}
                    onDecide={decide}
                  />
                ))}
              </ul>
            </>
          ) : (
            <div className="border border-line bg-surface-2 px-3 py-4 text-center">
              <p className="text-xs font-bold text-ink">No manager decision is waiting.</p>
              <p className="mt-1 text-2xs text-ink-faint">
                During a simulation, watches continue monitoring automatically. An agent starts
                only when a sustained condition crosses its threshold. Completed investigations
                and results that need no approval are available in History.
              </p>
            </div>
          )}

          {queue.history.length > 0 && (
            <details className="border border-line bg-surface-2">
              <summary className="cursor-pointer px-3 py-2.5 text-xs font-bold text-ink">
                History ({queue.history.length})
                <span className="ml-2 font-normal text-ink-faint">
                  completed, failed and earlier duplicate runs
                </span>
              </summary>
              <ul className="scroll-fade-b max-h-[34rem] space-y-3 overflow-y-auto border-t border-line p-3">
                {queue.history.map((proposal) => (
                  <ProposalCard
                    key={proposal.proposal_id}
                    proposal={proposal}
                    canDecide={canDecide}
                    busy={busy}
                    onDecide={decide}
                    archived
                  />
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </Panel>
  );
}
