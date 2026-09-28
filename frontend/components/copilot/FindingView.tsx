"use client";

// The one renderer for a verified finding.
//
// Extracted from the old ops TriageConsole so the copilot page and the dock are the
// SAME implementation. Two renderers for one contract is how a redaction marker ends up
// styled as ordinary text on the surface nobody checked.
//
// It deliberately leads with the VERDICT and puts the narrative second. If a viewer's
// takeaway is "nice LLM demo" this component has failed; the intended takeaway is "they
// made the model prove every number and discarded the ones it could not".

import { ActionType, FindingDecision, ProposedAction, TriageClaim, TriageRun } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Badge, Tooltip } from "@/components/ui";

export const STATUS_VARIANT: Record<
  string,
  "ok" | "warn" | "danger" | "info" | "neutral"
> = {
  verified: "ok",
  verified_with_redactions: "warn",
  rejected: "danger",
  failed: "danger",
  running: "info",
  queued: "info",
  unknown: "neutral",
};

export function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-1.5 text-2xs font-bold uppercase tracking-[0.14em] text-ink-faint">
      {children}
    </div>
  );
}

export const ACTION_LABEL: Record<ActionType, string> = {
  increase_feed_buffer: "Increase feed buffer",
  rebalance_cycle: "Rebalance cycle time",
  shift_weld_load: "Shift weld load",
  maintenance_ticket: "Maintenance ticket",
  hvac_setpoint: "HVAC setpoint",
  resequence_paint_queue: "Resequence paint queue",
};

/** "S12 | +4 feed slots" -- the params in floor language, never raw JSON. */
export function describeParams(params: ProposedAction["params"]): string {
  const p = params;
  const parts: string[] = [];
  if (p.from_station != null && p.to_station != null) parts.push(`S${p.from_station} -> S${p.to_station}`);
  else if (p.station_id != null) parts.push(`S${p.station_id}`);
  if (p.buffer_delta != null) parts.push(`${Number(p.buffer_delta) > 0 ? "+" : ""}${p.buffer_delta} feed slots`);
  if (p.cycle_delta_pct != null) parts.push(`${p.cycle_delta_pct}% cycle time`);
  if (p.target_humidity_pct != null) parts.push(`target ${p.target_humidity_pct}% RH`);
  if (p.signal != null) parts.push(String(p.signal));
  return parts.join(" | ");
}

export function ActionRow({
  action, trailing, children,
}: { action: ProposedAction; trailing?: React.ReactNode; children?: React.ReactNode }) {
  const params = describeParams(action.params);
  return (
    <li className="border border-ai/30 bg-ai/[0.05] px-2.5 py-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <span className="text-xs font-bold text-ink">{ACTION_LABEL[action.action_type] ?? action.action_type}</span>
          {params && <span className="text-numeric ml-2 text-2xs text-ink-dim">{params}</span>}
        </div>
        {trailing}
      </div>
      {action.rationale && (
        <p className="mt-1 text-2xs leading-relaxed text-ink-dim">{action.rationale}</p>
      )}
      <div className="text-numeric mt-1 text-2xs text-ink-faint">
        rests on {action.claim_ids.join(", ")}
      </div>
      {children}
    </li>
  );
}

export function ClaimRow({ claim }: { claim: TriageClaim }) {
  // A redacted claim keeps its slot rather than disappearing: the reader should see that
  // something was removed and why, not a shorter list that looks complete.
  const redacted = claim.text.startsWith("[claim removed");
  return (
    <li
      className={cn(
        "rounded-md border px-2.5 py-1.5",
        redacted ? "border-danger/30 bg-danger/[0.05]" : "border-line bg-surface-2"
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <span className={cn("min-w-0 text-xs", redacted ? "text-danger-fg" : "text-ink")}>
          {claim.text}
        </span>
        {!redacted && claim.provenance === "inferred" && (
          <Badge variant="warn">Inferred</Badge>
        )}
      </div>
      {!redacted && (
        <div className="text-numeric mt-1 flex flex-wrap items-center gap-x-3 text-2xs text-ink-faint">
          <span>
            {claim.citation.call_id}
            {claim.citation.pointer}
          </span>
          {claim.ci_low != null && claim.ci_high != null && (
            <span>
              CI {claim.ci_low.toFixed(2)}–{claim.ci_high.toFixed(2)}
            </span>
          )}
          {claim.source && <span className="truncate">src: {claim.source}</span>}
        </div>
      )}
    </li>
  );
}

const DECISION: Record<FindingDecision, { text: string; tone: "ok" | "warn" | "neutral" | "danger" }> = {
  recommend: { text: "Recommends a change", tone: "ok" },
  insufficient_evidence: { text: "Stopped: insufficient evidence", tone: "neutral" },
  unsupported_intervention: { text: "Stopped: intervention not supported", tone: "neutral" },
  simulator_unavailable: { text: "Stopped: simulator unavailable", tone: "warn" },
  no_improvement: { text: "Stopped: nothing improved the objective", tone: "neutral" },
  too_uncertain: { text: "Stopped: results too uncertain", tone: "warn" },
  engineering_review: { text: "Needs engineering review", tone: "warn" },
};

// Which tool a claim's number came from decides which of the four kinds it is.
const SIM_TOOLS = new Set(["run_simulation_experiment", "get_simulation_experiment",
                           "compare_simulation_interventions"]);

function claimKind(claim: TriageClaim, toolOf: Map<string, string>):
  "observed" | "projection" | "grid" | "inferred" {
  const tool = toolOf.get(claim.citation.call_id) ?? "";
  if (SIM_TOOLS.has(tool)) return "projection";
  if (tool === "simulate_whatif") return "grid";
  return claim.provenance === "measured" ? "observed" : "inferred";
}

const KIND_LABEL = {
  observed: "Observed facts · measured",
  inferred: "Inferred relationships",
  projection: "Simulation projections · fresh simulation",
  grid: "Estimates · precomputed grid, interpolated",
} as const;

export function VerdictStrip({ run }: { run: TriageRun }) {
  const ver = run.verification;
  if (!ver) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant={STATUS_VARIANT[run.status] ?? "neutral"} dot>
        {run.status.replace(/_/g, " ")}
      </Badge>
      <Tooltip content="Every numeric claim was re-checked against the exact tool results the model was shown. Unverifiable claims are removed, not flagged.">
        <span className="text-numeric cursor-help text-xs text-ink-dim">
          {ver.claims_verified}/{ver.claims_total} claims verified
        </span>
      </Tooltip>
      {run.latency_s != null && (
        <span className="text-numeric text-2xs text-ink-faint">
          {run.latency_s.toFixed(1)}s
        </span>
      )}
      {run.cost_usd != null && run.cost_usd > 0 && (
        <Tooltip content={run.cost_source ?? "provider rate card"}>
          <span className="text-numeric cursor-help text-2xs text-ink-faint">
            ${run.cost_usd.toFixed(3)}
          </span>
        </Tooltip>
      )}
      {run.capability_profile && run.capability_profile !== "L0" && (
        <Tooltip content="The provider rejected part of the request shape, so it was degraded one rung at a time. The finding still passed the identical verifier — a degraded run is shown as degraded rather than silently.">
          <Badge variant="warn">degraded {run.capability_profile}</Badge>
        </Tooltip>
      )}
      {ver.carried_values_used != null && ver.carried_values_used > 0 && (
        <Tooltip content="This turn restated values established earlier in the conversation. Citations never cross turns — only a value already verified in this thread may be repeated in prose, and the earlier evidence is kept permanently.">
          <Badge variant="info">
            {ver.carried_values_used} carried
          </Badge>
        </Tooltip>
      )}
    </div>
  );
}

export default function FindingView({ run }: { run: TriageRun }) {
  const ver = run.verification;

  if (run.status === "rejected") {
    return (
      <div className="rounded-lg border border-danger/35 bg-danger/[0.07] p-3">
        <p className="mb-1 text-xs font-bold text-danger-fg">
          Finding discarded — it could not be verified
        </p>
        <p className="text-numeric text-2xs text-ink-dim">
          {ver?.reject_reason || run.error}
        </p>
        <p className="mt-2 text-2xs leading-relaxed text-ink-faint">
          The agent produced an answer whose numbers could not all be traced to the tool
          results it was shown, so none of it is served. The evidence it read is listed
          above.
        </p>
      </div>
    );
  }

  if (run.status === "failed") {
    return (
      <div className="rounded-lg border border-danger/35 bg-danger/[0.07] p-3">
        <p className="mb-1 text-xs font-bold text-danger-fg">Run failed</p>
        <p className="text-numeric text-2xs text-ink-dim">{run.error || "unknown"}</p>
      </div>
    );
  }

  if (!run.finding) return null;

  return (
    <div className="space-y-4">
      <p className="text-xs leading-relaxed text-ink-dim">{run.finding.summary}</p>

      {run.finding.decision && DECISION[run.finding.decision] && (
        <Badge variant={DECISION[run.finding.decision].tone}>
          {DECISION[run.finding.decision].text}
        </Badge>
      )}

      {(run.finding.hypotheses?.length ?? 0) > 0 && (
        <div>
          <SectionLabel>Hypotheses tested</SectionLabel>
          <ul className="space-y-1">
            {run.finding.hypotheses!.map((h) => (
              <li key={h.id} className="flex items-start gap-2 text-xs text-ink">
                <Badge variant={h.status === "supported" ? "ok" : h.status === "rejected" ? "neutral" : "warn"}>
                  {h.status}
                </Badge>
                <span className="min-w-0">
                  {h.statement}
                  {h.claim_ids.length > 0 && (
                    <span className="text-numeric ml-1 text-2xs text-ink-faint">({h.claim_ids.join(", ")})</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {run.finding.claims.length > 0 && (() => {
        const toolOf = new Map((run.tool_ledger ?? []).map((t) => [t.call_id, t.tool_name]));
        const groups = (["observed", "inferred", "projection", "grid"] as const)
          .map((k) => [k, run.finding!.claims.filter((c) => claimKind(c, toolOf) === k)] as const)
          .filter(([, cs]) => cs.length > 0);
        return groups.map(([k, cs]) => (
          <div key={k}>
            <SectionLabel>{KIND_LABEL[k]}</SectionLabel>
            <ul className="space-y-1.5">
              {cs.map((c) => (
                <ClaimRow key={c.id} claim={c} />
              ))}
            </ul>
          </div>
        ));
      })()}

      {(run.finding.proposed_actions?.length ?? 0) > 0 && (
        <div>
          <SectionLabel>Proposed actions · a person decides</SectionLabel>
          <ul className="space-y-1.5">
            {run.finding.proposed_actions!.map((a) => (
              <ActionRow key={a.id} action={a} />
            ))}
          </ul>
        </div>
      )}

      {(run.finding.knowledge_citations?.length ?? 0) > 0 && (
        <div>
          <SectionLabel>Retrieved sources</SectionLabel>
          <ul className="space-y-1.5">
            {run.finding.knowledge_citations!.map((k) => (
              <li key={k.id} className="border border-line bg-surface-2 px-2.5 py-2">
                <div className="text-numeric mb-1 text-2xs font-bold text-primary">
                  {k.doc_id} · §{k.section}
                </div>
                <p className="text-xs leading-relaxed text-ink">{k.quoted_text}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <SectionLabel>
          {run.mode === "deterministic" ? "Recommendation · deterministic fallback" : "Recommendation · AI agent"}
        </SectionLabel>
        <p className="text-xs leading-relaxed text-ink">{run.finding.recommendation}</p>
      </div>

      {run.finding.unanswered.length > 0 && (
        <div className="rounded-lg border border-warn/30 bg-warn/[0.06] p-3">
          <SectionLabel>Could not determine</SectionLabel>
          <ul className="space-y-1">
            {run.finding.unanswered.map((u) => (
              <li key={u} className="text-2xs leading-relaxed text-ink-dim">
                {u}
              </li>
            ))}
          </ul>
        </div>
      )}

      {run.finding.alternatives_considered.length > 0 && (
        <details className="group">
          <summary className="cursor-pointer list-none text-2xs uppercase tracking-[0.14em] text-ink-faint">
            Why not something else?
          </summary>
          <ul className="mt-1.5 space-y-1">
            {run.finding.alternatives_considered.map((a) => (
              <li key={a} className="text-2xs leading-relaxed text-ink-dim">
                {a}
              </li>
            ))}
          </ul>
        </details>
      )}

      {ver && ver.prose_issues.length > 0 && (
        <p className="text-2xs leading-relaxed text-ink-faint">
          {ver.prose_issues.length} sentence
          {ver.prose_issues.length > 1 ? "s were" : " was"} removed for carrying an
          uncited figure.
        </p>
      )}
    </div>
  );
}
