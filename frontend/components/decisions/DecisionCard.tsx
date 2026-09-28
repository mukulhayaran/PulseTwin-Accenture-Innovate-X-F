"use client";

// One Approval Request (ADR-071): the problem, the AI's conclusion, what it touches, what
// is recommended and why it needs a manager -- then Approve / Modify / Reject / Request
// Further Investigation. Approve and Modify hand the action to the Action Gateway at once
// (approve + execute, both server-checked), so the manager sees the work-order reference.

import { useState } from "react";
import {
  type DecisionCardData, type InvestigationFocus, approveRecommendation, executeAction,
  modifyRecommendation, rejectRecommendation, requestMoreInvestigation,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { Badge, Button } from "@/components/ui";

const REJECT_REASONS = ["Insufficient evidence", "Downtime unacceptable", "Already scheduled maintenance",
                        "Operational constraint", "Alternative action preferred", "Other"];
type Mode = null | "modify" | "reject" | "investigate";

export default function DecisionCard({ d, onEvidence, onDone }: {
  d: DecisionCardData; onEvidence: () => void; onDone: (message: string) => void;
}) {
  const [mode, setMode] = useState<Mode>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [reason, setReason] = useState(REJECT_REASONS[0]);
  const [windowId, setWindowId] = useState(d.schedule_windows[0]?.window_id ?? "");
  const [priority, setPriority] = useState<"low" | "normal" | "high">("normal");
  const [scope, setScope] = useState("");
  const [focus, setFocus] = useState<InvestigationFocus[]>([]);

  async function run(fn: () => Promise<string>) {
    setBusy(true); setErr(null);
    try { onDone(await fn()); } catch (e: any) { setErr(e?.message ?? String(e)); } finally { setBusy(false); }
  }
  const approve = () => run(async () => {
    const r = await approveRecommendation(d.recommendation_id, d.action.option_id, note.trim() || undefined);
    const ex = r.action ? await executeAction(r.action.action_id) : null;
    return ex ? `Approved. ${d.action.adapter.toUpperCase()} workflow created — reference ${ex.action.external_reference}.`
              : "Approved.";
  });
  const modify = () => run(async () => {
    const r = await modifyRecommendation(d.recommendation_id, d.action.option_id, windowId || null, note.trim(),
                                         { priority, description: scope.trim() || undefined });
    const ex = r.action ? await executeAction(r.action.action_id) : null;
    return `Approved with changes.${ex ? ` Reference ${ex.action.external_reference}.` : ""}`;
  });
  const reject = () => run(async () => {
    await rejectRecommendation(d.recommendation_id, `${reason}${note.trim() ? `: ${note.trim()}` : ""}`);
    return "Rejected. The reason is saved and will be shown for similar cases.";
  });
  const investigate = () => run(async () => {
    await requestMoreInvestigation(d.recommendation_id, note.trim() || "Investigate further before deciding", focus);
    return "Sent back to the AI. It returns here only if a decision is still needed.";
  });

  const imp = d.expected_impact;
  return (
    <article className="panel-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-numeric text-2xs font-bold text-ink-faint">{d.incident_id}</div>
          <h3 className="mt-0.5 text-base font-bold text-ink">{d.title}</h3>
          <p className="mt-0.5 text-xs text-ai-fg">
            AI: {d.conclusion ?? "cause unresolved"}
            {d.confidence != null && ` · ${Math.round(d.confidence * 100)}% confidence`}
          </p>
        </div>
        <Badge variant={d.severity === "high" ? "danger" : "warn"}>{d.severity}</Badge>
      </div>
      {/* The five things a manager decides on (LOOP criterion L); the rest is one click away. */}
      <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2.5 text-xs md:grid-cols-2">
        <Row k="Problem" v={d.problem} />
        <Row k="Recommendation" v={d.action.title} />
        <Row k="Expected impact" v={imp ? `${imp.mean >= 0 ? "+" : ""}${imp.mean.toFixed(2)} ${imp.metric}` : "Not simulated"}
          hint={imp ? `digital-twin test · 95% range ${imp.ci_low?.toFixed(2)} … ${imp.ci_high?.toFixed(2)}` : undefined} />
        <Row k="Risk" v={`${d.risk} · ${d.downtime_min} min downtime`} />
        <div className="md:col-span-2"><Row k="Why your approval is required" v={d.approval.reason} /></div>
      </dl>
      <details className="mt-2 text-xs">
        <summary className="label-upper cursor-pointer text-2xs text-ink-dim hover:text-primary">Details</summary>
        <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-2 md:grid-cols-2">
          <Row k="Affected production" v={d.affected.count != null ? `${d.affected.count} vehicles` : "—"} hint={d.affected.basis} />
          <Row k="Estimated cost" v={d.cost ? `≈ $${Math.round(d.cost.value_usd).toLocaleString()}` : "—"} hint="estimated from sourced rates" />
          <Row k="Timing" v={d.recommended_timing ?? d.schedule ?? "—"} />
          <Row k="Downtime basis" v={d.downtime_basis} />
          <div className="md:col-span-2"><Row k="Policy" v={d.approval.details.join(" · ") || "—"} /></div>
        </dl>
      </details>

      {mode && (
        <div className="mt-3 space-y-2 border border-line bg-surface-2 p-3">
          {mode === "modify" && (
            <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
              <Field label="Maintenance window">
                <select value={windowId} onChange={(e) => setWindowId(e.target.value)} className="h-8 w-full border border-line bg-canvas px-1.5 text-xs">
                  <option value="">Keep as recommended</option>
                  {d.schedule_windows.map((w) => <option key={w.window_id} value={w.window_id}>{w.name} — {w.start.slice(0, 16).replace("T", " ")}</option>)}
                </select>
              </Field>
              <Field label="Priority">
                <select value={priority} onChange={(e) => setPriority(e.target.value as any)} className="h-8 w-full border border-line bg-canvas px-1.5 text-xs">
                  <option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option>
                </select>
              </Field>
              <Field label="Scope / instructions">
                <input value={scope} onChange={(e) => setScope(e.target.value)} maxLength={300}
                  placeholder="e.g. inspect drive belt only" className="h-8 w-full border border-line bg-canvas px-2 text-xs" />
              </Field>
            </div>
          )}
          {mode === "reject" && (
            <Field label="Reason">
              <select value={reason} onChange={(e) => setReason(e.target.value)} className="h-8 w-full border border-line bg-canvas px-1.5 text-xs">
                {REJECT_REASONS.map((r) => <option key={r}>{r}</option>)}
              </select>
            </Field>
          )}
          {mode === "investigate" && (
            <div className="flex flex-wrap gap-3 text-xs text-ink">
              {([["more_evidence", "Collect more evidence first"], ["supplier_lot", "Check material / supplier variation"]] as const).map(([k, label]) => (
                <label key={k} className="flex items-center gap-1.5">
                  <input type="checkbox" checked={focus.includes(k)}
                    onChange={(e) => setFocus((f) => (e.target.checked ? [...f, k] : f.filter((x) => x !== k)))} />
                  {label}
                </label>
              ))}
            </div>
          )}
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500}
            placeholder={mode === "investigate" ? "What should the AI look into? e.g. can this wait until Sunday's window?"
                         : mode === "reject" ? "Details (saved as memory for similar cases)" : "Note (required, saved with the decision)"}
            className="h-8 w-full border border-line bg-canvas px-2 text-xs" />
          <div className="flex gap-2">
            {mode === "modify" && <Button size="sm" variant="primary" disabled={busy || note.trim().length < 3} onClick={modify}>Approve with changes</Button>}
            {mode === "reject" && <Button size="sm" variant="danger" disabled={busy} onClick={reject}>Confirm rejection</Button>}
            {mode === "investigate" && <Button size="sm" variant="primary" disabled={busy} onClick={investigate}>Send back to the AI</Button>}
            <Button size="sm" variant="ghost" onClick={() => setMode(null)}>Cancel</Button>
          </div>
        </div>
      )}
      {err && <p className="mt-2 text-xs text-danger-fg">{err}</p>}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="primary" disabled={busy} onClick={approve}>Approve</Button>
        <Button size="sm" variant="outline" onClick={onEvidence}>Explain</Button>
        <span className="flex-1" />
        <Button size="xs" variant="ghost" onClick={() => setMode(mode === "modify" ? null : "modify")}>Modify</Button>
        <Button size="xs" variant="ghost" onClick={() => setMode(mode === "reject" ? null : "reject")}>Reject</Button>
        <Button size="xs" variant="ghost" onClick={() => setMode(mode === "investigate" ? null : "investigate")}>Request more evidence</Button>
      </div>
    </article>
  );
}

function Row({ k, v, hint }: { k: string; v: string; hint?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-2xs font-bold uppercase tracking-[0.12em] text-ink-faint">{k}</dt>
      <dd className={cn("text-ink")} title={hint}>{v}{hint && <span className="block text-2xs text-ink-faint">{hint}</span>}</dd>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-0.5 block text-2xs font-bold uppercase tracking-[0.12em] text-ink-faint">{label}</span>
      {children}
    </label>
  );
}
