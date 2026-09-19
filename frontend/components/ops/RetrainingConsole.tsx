"use client";

// Champion/challenger review. `model_registry` does not persist the compare() verdict
// (mlops/evaluation.py's PROMOTE/REJECT recommendation lives only in the ephemeral
// retrain-job status file, serve/routers/admin.py's `_jobs_dir()`), so:
//   - right after THIS session triggers a retrain, the full report (with the backend's
//     own recommendation) is shown, polled from /api/admin/retrain-status/{job_id};
//   - a challenger already sitting in the registry from an earlier run is shown as a
//     plain side-by-side metrics comparison, with no fabricated recommendation attached.
// "Reject" has no backend endpoint (there is nothing to undo -- an un-promoted challenger
// already has no effect on serving) so it just dismisses the review from this view.

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { getModelRegistry, getRetrainStatus, promoteModel, triggerRetrain } from "@/lib/api";
import { cn } from "@/lib/cn";
import RoleGate from "@/components/shared/RoleGate";
import { Badge, Button, Panel } from "@/components/ui";

interface Props {
  plant: string;
  onAction?: (message: string) => void;
}

function pull(metrics: any) {
  const thr = metrics?.thresholds ?? {};
  const reliability: any[] = metrics?.reliability ?? [];
  const maxDev = reliability.length
    ? Math.max(...reliability.map((b) => Math.abs(b.predicted - b.observed))) * 100
    : null;
  return {
    pr_auc: metrics?.pr_auc ?? null,
    precision_at_budget: thr.precision_at_budget ?? null,
    calibration_max_deviation_pp: maxDev,
  };
}

function fmt(v: number | null | undefined, digits = 3): string {
  return v == null ? "—" : v.toFixed(digits);
}

export default function RetrainingConsole({ plant, onAction }: Props) {
  const [history, setHistory] = useState<any[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<any | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [busy, setBusy] = useState(false);
  const poller = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = () => getModelRegistry(plant).then((r) => setHistory(r.history)).catch(() => setHistory([]));
  useEffect(() => { refresh(); setJobId(null); setJob(null); setDismissed(false); }, [plant]);

  useEffect(() => {
    if (!jobId) return;
    poller.current = setInterval(async () => {
      const s = await getRetrainStatus(jobId).catch(() => null);
      if (s) setJob(s);
      if (s?.status === "complete" || s?.status === "failed") {
        if (poller.current) clearInterval(poller.current);
        if (s.status === "complete") refresh();
      }
    }, 2000);
    return () => { if (poller.current) clearInterval(poller.current); };
  }, [jobId]);

  const champion = history.find((h) => h.status === "champion");
  const registryChallenger = history.find((h) => h.status === "challenger");

  const startRetrain = async () => {
    setBusy(true);
    try {
      const r = await triggerRetrain(plant);
      setJobId(r.job_id);
      setJob({ status: "running" });
      setDismissed(false);
    } finally { setBusy(false); }
  };

  const promote = async (modelId: string) => {
    setBusy(true);
    try {
      await promoteModel(modelId);
      onAction?.(`Model ${modelId} promoted`);
      setDismissed(true);
      refresh();
    } finally { setBusy(false); }
  };

  const report = job?.status === "complete" ? job.report : null;

  const actions = (modelId: string) => (
    <RoleGate allowed={["system"]}>
      <div className="flex gap-2 pt-1">
        <Button variant="ok" size="xs" disabled={busy} onClick={() => promote(modelId)}>
          Promote challenger
        </Button>
        <Button variant="ghost" size="xs" disabled={busy} onClick={() => setDismissed(true)}>
          Reject
        </Button>
      </div>
    </RoleGate>
  );

  return (
    <Panel
      title="Retraining console"
      actions={
        <RoleGate allowed={["system"]} fallback={null}>
          <Button
            variant="ai"
            size="xs"
            disabled={busy || job?.status === "running"}
            onClick={startRetrain}
          >
            Trigger retrain
          </Button>
        </RoleGate>
      }
    >
      {champion && (
        <div className="mb-3 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-2xs text-ink-faint">
          Champion
          <span className="text-numeric text-ink-dim">{champion.model_id}</span>
          {champion.promoted_at && <>· promoted {String(champion.promoted_at).slice(0, 10)}</>}
        </div>
      )}

      <AnimatePresence mode="wait">
        {report && !dismissed ? (
          <Fade key="report">
            <CompareTable
              rows={[
                { label: "PR-AUC", a: report.champion.pr_auc, b: report.challenger.pr_auc, d: report.improvement.pr_auc, good: report.criteria?.beats_pr_auc },
                { label: "P@budget", a: report.champion.precision_at_budget, b: report.challenger.precision_at_budget, d: report.improvement.precision_at_budget, good: report.criteria?.beats_precision_at_budget },
                { label: "Calibration pp", a: report.champion.calibration_max_deviation_pp, b: report.challenger.calibration_max_deviation_pp, d: report.improvement.calibration_max_deviation_pp, good: report.criteria?.beats_or_matches_calibration },
              ]}
            />
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="text-2xs text-ink-faint">Recommendation</span>
              <Badge variant={report.recommendation === "PROMOTE" ? "ok" : "warn"}>
                {report.recommendation}
              </Badge>
              {report.requires_human_approval && (
                <span className="text-2xs text-ink-faint">requires human approval</span>
              )}
            </div>
            <div className="mt-2">{actions(report.challenger.model_id)}</div>
          </Fade>
        ) : job?.status === "running" ? (
          <Fade key="running">
            <div className="flex items-center gap-2 py-3 text-xs text-info-fg">
              <motion.span
                className="size-2 rounded-full bg-info"
                animate={{ opacity: [1, 0.3, 1] }}
                transition={{ duration: 1.2, repeat: Infinity }}
              />
              Retraining in progress…
            </div>
          </Fade>
        ) : job?.status === "failed" ? (
          <Fade key="failed">
            <div className="rounded-lg border border-danger/35 bg-danger/[0.07] p-3 text-xs text-danger-fg">
              Retrain failed: {job.error ?? "unknown error"}
            </div>
          </Fade>
        ) : registryChallenger && !dismissed ? (
          <Fade key="registry">
            <div className="mb-2 text-2xs text-ink-faint">
              Unreviewed challenger from a previous run:{" "}
              <span className="text-numeric text-ink-dim">{registryChallenger.model_id}</span>
            </div>
            {(() => {
              const c = pull(champion?.metrics);
              const ch = pull(registryChallenger.metrics);
              return (
                <CompareTable
                  rows={[
                    { label: "PR-AUC", a: c.pr_auc, b: ch.pr_auc },
                    { label: "P@budget", a: c.precision_at_budget, b: ch.precision_at_budget },
                    { label: "Calibration pp", a: c.calibration_max_deviation_pp, b: ch.calibration_max_deviation_pp },
                  ]}
                />
              );
            })()}
            <div className="mt-2">{actions(registryChallenger.model_id)}</div>
          </Fade>
        ) : (
          <Fade key="idle">
            <div className="py-3 text-xs text-ink-faint">No pending retrain review.</div>
          </Fade>
        )}
      </AnimatePresence>
    </Panel>
  );
}

function Fade({ children }: { children: React.ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
    >
      {children}
    </motion.div>
  );
}

function CompareTable({
  rows,
}: {
  rows: { label: string; a: number | null; b: number | null; d?: number | null; good?: boolean }[];
}) {
  const hasDelta = rows.some((r) => r.d !== undefined);
  return (
    <table className="w-full border-collapse text-xs">
      <thead>
        <tr className="border-b border-line text-left">
          <th className="pb-1.5 pr-3 text-2xs font-medium uppercase tracking-[0.12em] text-ink-faint" />
          <th className="pb-1.5 pr-3 text-right text-2xs font-medium uppercase tracking-[0.12em] text-ink-faint">
            Champion
          </th>
          <th className="pb-1.5 pr-3 text-right text-2xs font-medium uppercase tracking-[0.12em] text-ink-faint">
            Challenger
          </th>
          {hasDelta && (
            <th className="pb-1.5 text-right text-2xs font-medium uppercase tracking-[0.12em] text-ink-faint">
              Δ
            </th>
          )}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.label} className="border-b border-line/60 last:border-0">
            <td className="py-1.5 pr-3 text-ink-faint">{r.label}</td>
            <td className="text-numeric py-1.5 pr-3 text-right text-ink-dim">{fmt(r.a)}</td>
            <td className="text-numeric py-1.5 pr-3 text-right text-ink">{fmt(r.b)}</td>
            {hasDelta && (
              <td
                className={cn(
                  "text-numeric py-1.5 text-right",
                  r.good == null ? "text-ink-dim" : r.good ? "text-ok-fg" : "text-danger-fg"
                )}
              >
                {r.d == null ? "—" : `${r.d > 0 ? "+" : ""}${r.d.toFixed(3)}`}
                {r.good != null && (r.good ? " ✓" : " ✗")}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
