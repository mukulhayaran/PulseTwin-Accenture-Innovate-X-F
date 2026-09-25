"use client";

// The human half of ADR-051: watch fires -> agent investigates -> agent PROPOSES ->
// a person decides here -> the twin applies -> the watch confirms recovery.
//
// Every proposal shows the MEASURED evidence that made the watch fire, the agent's
// verified finding, and only the actions the verifier served (an action resting on a
// redacted number never reaches this list). Approve is manager-and-above here and again
// on the server; an agent has no tool that can reach these endpoints.

import { useCallback, useEffect, useState } from "react";
import {
  ActionProposal, ProposalStatus, approveProposal, getRole, listProposals,
  rejectProposal, roleAtLeast,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { Badge, Button, Panel } from "@/components/ui";
import { ActionRow, ACTION_LABEL, describeParams } from "@/components/copilot/FindingView";

const POLL_MS = 4000;

const DETECTOR_LABEL: Record<ActionProposal["detector"], string> = {
  starvation: "Starvation risk",
  paint_env: "Paint environment",
  tool_wear: "Tool degradation",
};

const STATUS_TONE: Record<ProposalStatus, "ok" | "warn" | "danger" | "info" | "neutral" | "ai"> = {
  INVESTIGATING: "info",
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
  PROPOSED: "awaiting decision",
  NO_ACTION: "no action warranted",
  AGENT_FAILED: "agent failed",
  NO_AGENT: "no agent run",
  APPROVED: "approved · twin not running",
  APPLIED: "applied · awaiting recovery",
  EFFECTIVE: "effective · recovered",
  REJECTED: "rejected",
};

// Evidence keys that are identity, not measurement -- shown in the header instead.
const HIDDEN_EVIDENCE = new Set(["station_id", "station_name"]);

function formatEvidence(value: number | string | null): string {
  if (value == null) return "—";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  return value;
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

  const pending = proposals?.filter((p) => p.status === "PROPOSED").length ?? 0;

  return (
    <Panel
      title="Proposals"
      note="The live watch fires, an agent investigates and proposes; a person decides here. Approved actions apply to the simulated twin only, never to real equipment."
      actions={pending > 0 ? <Badge variant="ai" dot>{pending} awaiting decision</Badge> : undefined}
    >
      {err && (
        <p className="mb-3 border border-danger/30 bg-danger/[0.06] px-2.5 py-1.5 text-2xs text-danger-fg">{err}</p>
      )}
      {proposals === null ? (
        <p className="py-6 text-center text-2xs text-ink-faint">Loading proposals…</p>
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
        <ul className="scroll-fade-b max-h-[40rem] space-y-3 overflow-y-auto pr-1">
          {proposals.map((p) => {
            const evidence = Object.entries(p.trigger_evidence).filter(([k]) => !HIDDEN_EVIDENCE.has(k));
            const name = p.trigger_evidence.station_name;
            const decidable = p.status === "PROPOSED";
            return (
              <li key={p.proposal_id} className="border border-line bg-canvas p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-bold text-ink">{DETECTOR_LABEL[p.detector]}</span>
                  <span className="text-numeric text-2xs text-ink-dim">
                    S{p.station_id}{name ? ` · ${name}` : ""}
                  </span>
                  <span className="flex-1" />
                  <Badge variant={STATUS_TONE[p.status]} dot={p.status === "INVESTIGATING"}>
                    {STATUS_TEXT[p.status]}
                  </Badge>
                </div>
                <div className="text-numeric mt-0.5 text-2xs text-ink-faint">
                  fired {p.fired_at.replace("T", " ").slice(0, 19)} · {p.proposal_id}
                  {p.agent_mode ? ` · ${p.agent_mode} agent` : ""}
                </div>

                <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 border-t border-line pt-2 sm:grid-cols-3">
                  {evidence.map(([k, v]) => (
                    <div key={k} className="min-w-0">
                      <dt className="truncate text-2xs text-ink-faint">{k.replace(/_/g, " ")}</dt>
                      <dd className="text-numeric text-xs text-ink">{formatEvidence(v)}</dd>
                    </div>
                  ))}
                </dl>

                {p.finding?.summary && (
                  <p className="mt-2 text-2xs leading-relaxed text-ink-dim">{p.finding.summary}</p>
                )}

                {p.selected_action ? (
                  <p className="mt-2 text-2xs text-ink-dim">
                    <span className="font-bold text-ink">{ACTION_LABEL[p.selected_action.action_type]}</span>{" "}
                    {describeParams(p.selected_action.params)} — decided by {p.decided_by_role}
                    {p.effective_at ? `; recovery confirmed by the watch at ${p.effective_at.replace("T", " ").slice(0, 19)}` : ""}.
                  </p>
                ) : (
                  p.proposed_actions.length > 0 && (
                    <ul className="mt-2 space-y-1.5">
                      {p.proposed_actions.map((a) => (
                        <ActionRow
                          key={a.id}
                          action={a}
                          trailing={
                            decidable && (
                              <Button
                                variant="primary"
                                size="xs"
                                disabled={!canDecide || busy === p.proposal_id}
                                onClick={() => decide(p, a.id)}
                                title={canDecide ? undefined : "Requires the manager role or above"}
                                className="shrink-0"
                              >
                                Approve
                              </Button>
                            )
                          }
                        />
                      ))}
                    </ul>
                  )
                )}

                {["PROPOSED", "NO_ACTION", "AGENT_FAILED", "NO_AGENT", "INVESTIGATING"].includes(p.status) && (
                  <div className="mt-2 flex justify-end">
                    <Button
                      variant="ghost"
                      size="xs"
                      disabled={!canDecide || busy === p.proposal_id}
                      onClick={() => decide(p, null)}
                      className={cn(!canDecide && "opacity-50")}
                    >
                      {decidable ? "Reject" : "Dismiss"}
                    </Button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
