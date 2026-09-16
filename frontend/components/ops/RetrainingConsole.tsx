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
import {
  getModelRegistry, getRetrainStatus, promoteModel, triggerRetrain,
} from "@/lib/api";
import RoleGate from "@/components/shared/RoleGate";

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

  return (
    <Panel title="RETRAINING CONSOLE">
      {champion && (
        <div className="text-[11px] text-txt-muted mb-2">
          Champion: <span className="text-txt-primary">{champion.model_id}</span>
          {champion.promoted_at && <> · promoted {String(champion.promoted_at).slice(0, 10)}</>}
        </div>
      )}

      {report && !dismissed ? (
        <div className="text-[11px] space-y-2">
          <table className="w-full border-collapse">
            <thead>
              <tr className="text-txt-muted text-left">
                <th className="font-normal pb-1"></th>
                <th className="font-normal pb-1">CHAMPION</th>
                <th className="font-normal pb-1">CHALLENGER</th>
                <th className="font-normal pb-1">Δ</th>
              </tr>
            </thead>
            <tbody>
              <Row label="PR-AUC" a={report.champion.pr_auc} b={report.challenger.pr_auc}
                   d={report.improvement.pr_auc} good={report.criteria?.beats_pr_auc} />
              <Row label="P@budget" a={report.champion.precision_at_budget} b={report.challenger.precision_at_budget}
                   d={report.improvement.precision_at_budget} good={report.criteria?.beats_precision_at_budget} />
              <Row label="Calibration pp" a={report.champion.calibration_max_deviation_pp}
                   b={report.challenger.calibration_max_deviation_pp}
                   d={report.improvement.calibration_max_deviation_pp}
                   good={report.criteria?.beats_or_matches_calibration} />
            </tbody>
          </table>

          <div className={report.recommendation === "PROMOTE" ? "text-signal-green" : "text-signal-amber"}>
            Recommendation: <span className="font-semibold">{report.recommendation}</span>
            {report.requires_human_approval && " (requires human approval)"}
          </div>

          <RoleGate allowed={["system"]}>
            <div className="flex gap-2 pt-1">
              <button
                disabled={busy}
                onClick={() => promote(report.challenger.model_id)}
                className="rounded border border-signal-green/50 text-signal-green px-2 py-1 hover:bg-signal-green/10 disabled:opacity-40"
              >
                Promote Challenger
              </button>
              <button
                disabled={busy}
                onClick={() => setDismissed(true)}
                className="rounded border border-bg-surface text-txt-muted px-2 py-1 hover:text-txt-primary"
              >
                Reject
              </button>
            </div>
          </RoleGate>
        </div>
      ) : job?.status === "running" ? (
        <div className="text-[11px] text-signal-blue py-2">Retraining in progress…</div>
      ) : job?.status === "failed" ? (
        <div className="text-[11px] text-signal-red py-2">Retrain failed: {job.error ?? "unknown error"}</div>
      ) : registryChallenger && !dismissed ? (
        <div className="text-[11px] space-y-2">
          <div className="text-txt-muted">
            Unreviewed challenger from a previous run: <span className="text-txt-primary">{registryChallenger.model_id}</span>
          </div>
          <table className="w-full border-collapse">
            <thead>
              <tr className="text-txt-muted text-left">
                <th className="font-normal pb-1"></th>
                <th className="font-normal pb-1">CHAMPION</th>
                <th className="font-normal pb-1">CHALLENGER</th>
              </tr>
            </thead>
            <tbody>
              {(() => {
                const c = pull(champion?.metrics);
                const ch = pull(registryChallenger.metrics);
                return (
                  <>
                    <Row label="PR-AUC" a={c.pr_auc} b={ch.pr_auc} />
                    <Row label="P@budget" a={c.precision_at_budget} b={ch.precision_at_budget} />
                    <Row label="Calibration pp" a={c.calibration_max_deviation_pp} b={ch.calibration_max_deviation_pp} />
                  </>
                );
              })()}
            </tbody>
          </table>
          <RoleGate allowed={["system"]}>
            <div className="flex gap-2 pt-1">
              <button
                disabled={busy}
                onClick={() => promote(registryChallenger.model_id)}
                className="rounded border border-signal-green/50 text-signal-green px-2 py-1 hover:bg-signal-green/10 disabled:opacity-40"
              >
                Promote Challenger
              </button>
              <button
                disabled={busy}
                onClick={() => setDismissed(true)}
                className="rounded border border-bg-surface text-txt-muted px-2 py-1 hover:text-txt-primary"
              >
                Reject
              </button>
            </div>
          </RoleGate>
        </div>
      ) : (
        <div className="text-[11px] text-txt-muted py-2">No pending retrain review.</div>
      )}

      <RoleGate allowed={["system"]} fallback={null}>
        <button
          disabled={busy || job?.status === "running"}
          onClick={startRetrain}
          className="mt-3 text-[10px] px-2 py-1 border border-signal-purple/50 text-signal-purple rounded hover:bg-signal-purple/10 disabled:opacity-40"
        >
          Trigger Retrain
        </button>
      </RoleGate>
    </Panel>
  );
}

function Row({ label, a, b, d, good }: {
  label: string; a: number | null; b: number | null; d?: number | null; good?: boolean;
}) {
  const tone = good == null ? "" : good ? "text-signal-green" : "text-signal-red";
  return (
    <tr className="border-t border-bg-surface">
      <td className="py-1 text-txt-muted">{label}</td>
      <td className="py-1">{fmt(a)}</td>
      <td className="py-1">{fmt(b)}</td>
      {d !== undefined && (
        <td className={`py-1 ${tone}`}>
          {d == null ? "—" : `${d > 0 ? "+" : ""}${d.toFixed(3)}`} {good != null && (good ? "✓" : "✗")}
        </td>
      )}
    </tr>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-bg-card border border-bg-surface rounded p-3">
      <h2 className="text-txt-muted tracking-widest text-[11px] mb-2">{title}</h2>
      {children}
    </div>
  );
}
