"use client";

// The agentic triage panel.
//
// It deliberately leads with the VERIFICATION VERDICT and the tool ledger, and puts the
// narrative second. If a viewer's takeaway is "nice LLM demo" this panel has failed; the
// intended takeaway is "they made the model prove every number and discarded the ones it
// could not" -- the same intellectual move as the oracle ceiling.
//
// Lives on /ops rather than /manager because CLAUDE.md §7 puts role-gated UI where a
// gate can actually fire: dropping the "Viewing as" selector to supervisor makes the
// agent report what it could not read instead of estimating it, which is the single
// most persuasive thing this feature does.

import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  Bottleneck, TriageClaim, TriageRun,
  getBottleneck, getTriageAvailability, getTriageRun, listTriageRuns, startTriage,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { Badge, Button, Panel, Skeleton, Tooltip } from "@/components/ui";

const POLL_MS = 2000;

const STATUS_VARIANT: Record<string, "ok" | "warn" | "danger" | "info" | "neutral"> = {
  verified: "ok",
  verified_with_redactions: "warn",
  rejected: "danger",
  failed: "danger",
  running: "info",
  queued: "info",
  unknown: "neutral",
};

export default function TriageConsole({
  plant,
  onAction,
}: {
  plant: string;
  onAction?: (msg: string) => void;
}) {
  const [question, setQuestion] = useState("");
  const [run, setRun] = useState<TriageRun | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"agent" | "deterministic" | null>(null);
  const [bn, setBn] = useState<Bottleneck | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const poller = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    getTriageAvailability().then((a) => setMode(a.mode)).catch(() => setMode(null));
  }, []);

  // Preset questions are GENERATED from the live constraint, never typed as literals:
  // CLAUDE.md §1 bans hardcoded station IDs outside configs/*.yaml, and a preset reading
  // "S12 -> S19" would be wrong the moment the plant switches to plant_b.
  useEffect(() => {
    setRun(null);
    setRunId(null);
    setErr(null);
    getBottleneck(plant).then(setBn).catch(() => setBn(null));
    listTriageRuns(plant, 1)
      .then((r) => { if (r.runs[0]) { setRunId(r.runs[0].run_id); } })
      .catch(() => undefined);
  }, [plant]);

  const presets = bn
    ? [
        bn.previous_station != null
          ? `The constraint moved from S${bn.previous_station} to S${bn.current_station}. Investigate and recommend.`
          : `S${bn.current_station} is the current constraint. Investigate why and recommend a change.`,
        `Is S${bn.current_station} constrained by an infrastructure problem or a process one?`,
      ]
    : [];

  const poll = useCallback(async (id: string) => {
    try {
      const r = await getTriageRun(id);
      setRun(r);
      if (!["queued", "running"].includes(r.status) && poller.current) {
        clearInterval(poller.current);
        poller.current = null;
        setBusy(false);
      }
    } catch {
      /* transient failure must not kill the panel */
    }
  }, []);

  useEffect(() => {
    if (!runId) return;
    poll(runId);
    poller.current = setInterval(() => poll(runId), POLL_MS);
    return () => { if (poller.current) clearInterval(poller.current); };
  }, [runId, poll]);

  const launch = async (q: string) => {
    if (!q.trim()) return;
    setBusy(true);
    setErr(null);
    setRun(null);
    try {
      const r = await startTriage({ plant, question: q.trim() });
      setRunId(r.run_id);
      onAction?.(`Triage started (${r.mode}, acting as ${r.acting_role})`);
    } catch (e) {
      setErr(String(e));
      setBusy(false);
    }
  };

  const ver = run?.verification;
  const running = run?.status === "queued" || run?.status === "running" || busy;

  return (
    <Panel
      title="Agentic triage"
      note="An agent investigates through the read-only API. Every number it returns is re-checked against the tool results it saw."
      actions={
        mode ? (
          <Badge variant={mode === "agent" ? "ai" : "neutral"} dot>
            {mode === "agent" ? "Agent" : "Deterministic"}
          </Badge>
        ) : null
      }
    >
      {/* ------------------------------------------------------------ ask */}
      <div className="mb-4 space-y-2">
        <div className="flex flex-wrap gap-2">
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") launch(question); }}
            maxLength={500}
            placeholder={presets[0] ?? "Ask about the line…"}
            aria-label="Triage question"
            className={cn(
              "min-w-0 flex-1 rounded-md border border-line bg-surface-2 px-2.5 py-1.5",
              "text-xs text-ink placeholder:text-ink-faint",
              "transition-colors hover:border-line-strong focus:border-info/50"
            )}
          />
          <Button variant="primary" size="sm" disabled={running}
                  onClick={() => launch(question || presets[0] || "")}>
            {running ? "Investigating…" : "Investigate"}
          </Button>
        </div>
        {presets.length > 0 && !question && (
          <div className="flex flex-wrap gap-1.5">
            {presets.map((p) => (
              <button key={p} onClick={() => setQuestion(p)}
                      className="rounded-md border border-line bg-surface-2 px-2 py-0.5 text-2xs text-ink-faint transition-colors hover:border-line-strong hover:text-ink-dim">
                {p.length > 62 ? `${p.slice(0, 60)}…` : p}
              </button>
            ))}
          </div>
        )}
        {err && <p className="text-2xs text-danger-fg">{err}</p>}
      </div>

      {/* --------------------------------------------------- tool ledger */}
      {run?.tool_ledger && run.tool_ledger.length > 0 && (
        <div className="mb-4">
          <SectionLabel>Tools called</SectionLabel>
          <ol className="flex flex-wrap gap-1.5">
            <AnimatePresence initial={false}>
              {run.tool_ledger.map((t) => (
                <motion.li
                  key={t.call_id}
                  initial={{ opacity: 0, scale: 0.94 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ duration: 0.18 }}
                  className="text-numeric rounded-md border border-line bg-surface-2 px-1.5 py-0.5 text-2xs text-ink-dim"
                >
                  <span className="text-ink-faint">{t.call_id}</span> {t.tool_name}
                </motion.li>
              ))}
            </AnimatePresence>
          </ol>
        </div>
      )}

      {running && !run?.finding && (
        <div className="space-y-2">
          <Skeleton className="h-3 w-2/3" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-4/5" />
        </div>
      )}

      {/* ------------------------------------------------ verification verdict */}
      {ver && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <Badge variant={STATUS_VARIANT[run!.status] ?? "neutral"} dot>
            {run!.status.replace(/_/g, " ")}
          </Badge>
          <Tooltip content="Every numeric claim was re-checked against the exact tool results the model was shown. Unverifiable claims are removed, not flagged.">
            <span className="text-numeric cursor-help text-xs text-ink-dim">
              {ver.claims_verified}/{ver.claims_total} claims verified
            </span>
          </Tooltip>
          {run!.latency_s != null && (
            <span className="text-numeric text-2xs text-ink-faint">
              {run!.latency_s.toFixed(1)}s
            </span>
          )}
          {run!.cost_usd != null && run!.cost_usd > 0 && (
            <Tooltip content={run!.cost_source ?? "provider rate card"}>
              <span className="text-numeric cursor-help text-2xs text-ink-faint">
                ${run!.cost_usd.toFixed(3)}
              </span>
            </Tooltip>
          )}
        </div>
      )}

      {/* --------------------------------------------------------- rejected */}
      {run?.status === "rejected" && (
        <div className="rounded-lg border border-danger/35 bg-danger/[0.07] p-3">
          <p className="mb-1 text-xs font-bold text-danger-fg">
            Finding discarded — it could not be verified
          </p>
          <p className="text-numeric text-2xs text-ink-dim">
            {ver?.reject_reason || run.error}
          </p>
          <p className="mt-2 text-2xs leading-relaxed text-ink-faint">
            The agent produced an answer whose numbers could not all be traced to the
            tool results it was shown, so none of it is served. The evidence it read is
            listed above.
          </p>
        </div>
      )}

      {/* ---------------------------------------------------------- finding */}
      {run?.finding && run.status !== "rejected" && (
        <div className="space-y-4">
          <p className="text-xs leading-relaxed text-ink-dim">{run.finding.summary}</p>

          {run.finding.claims.length > 0 && (
            <div>
              <SectionLabel>Claims</SectionLabel>
              <ul className="space-y-1.5">
                {run.finding.claims.map((c) => (
                  <ClaimRow key={c.id} claim={c} />
                ))}
              </ul>
            </div>
          )}

          <div>
            <SectionLabel>Recommendation</SectionLabel>
            <p className="text-xs leading-relaxed text-ink">{run.finding.recommendation}</p>
          </div>

          {run.finding.unanswered.length > 0 && (
            <div className="rounded-lg border border-warn/30 bg-warn/[0.06] p-3">
              <SectionLabel>Could not determine</SectionLabel>
              <ul className="space-y-1">
                {run.finding.unanswered.map((u) => (
                  <li key={u} className="text-2xs leading-relaxed text-ink-dim">{u}</li>
                ))}
              </ul>
            </div>
          )}

          {ver && ver.prose_issues.length > 0 && (
            <p className="text-2xs leading-relaxed text-ink-faint">
              {ver.prose_issues.length} sentence
              {ver.prose_issues.length > 1 ? "s were" : " was"} removed for carrying an
              uncited figure.
            </p>
          )}
        </div>
      )}

      {!run && !running && (
        <p className="py-6 text-center text-2xs text-ink-faint">
          No triage run yet for this plant.
        </p>
      )}
    </Panel>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-1.5 text-2xs font-bold uppercase tracking-[0.14em] text-ink-faint">
      {children}
    </div>
  );
}

function ClaimRow({ claim }: { claim: TriageClaim }) {
  const redacted = claim.text.startsWith("[claim removed");
  return (
    <li
      className={cn(
        "rounded-md border px-2.5 py-1.5",
        redacted
          ? "border-danger/30 bg-danger/[0.05]"
          : "border-line bg-surface-2"
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
          <span>{claim.citation.call_id}{claim.citation.pointer}</span>
          {claim.ci_low != null && claim.ci_high != null && (
            <span>CI {claim.ci_low.toFixed(2)}–{claim.ci_high.toFixed(2)}</span>
          )}
          {claim.source && <span className="truncate">src: {claim.source}</span>}
        </div>
      )}
    </li>
  );
}
