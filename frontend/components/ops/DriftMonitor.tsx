"use client";

// The backend (serve/routers/model_health.py) only ever exposes the LATEST psi/calibration
// snapshot -- there is no drift-history endpoint. Rather than fabricate a 7-day heatmap with
// invented daily values, this renders an honest "current PSI status" view: the worst shifted
// feature (if any), a shifted/total summary bar, and the latest calibration reading.

import { motion } from "motion/react";
import { cn } from "@/lib/cn";
import { Badge, Panel, Skeleton, Tooltip } from "@/components/ui";

interface Props {
  psiSummary: any | null | undefined;
  calibration: any | null | undefined;
  sensorCoverage?: any | null;
}

function bandFor(psi: number) {
  if (psi > 0.2) return { label: "alert", variant: "danger" as const, text: "text-danger-fg" };
  if (psi > 0.1) return { label: "warning", variant: "warn" as const, text: "text-warn-fg" };
  return { label: "stable", variant: "ok" as const, text: "text-ok-fg" };
}

export default function DriftMonitor({ psiSummary, calibration, sensorCoverage }: Props) {
  if (!psiSummary || !calibration) {
    return (
      <Panel title="Drift monitor">
        <div className="space-y-3">
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-3 w-2/3" />
        </div>
      </Panel>
    );
  }

  const shifted = psiSummary.features_shifted ?? 0;
  const total = psiSummary.features_total ?? 0;
  const pct = total > 0 ? (shifted / total) * 100 : 0;
  const worst = psiSummary.worst_feature;
  const band = worst ? bandFor(worst.psi) : null;

  return (
    <Panel title="Drift monitor" note="current snapshot — mlops/drift_detector.py runs periodically">
      <div className="mb-4">
        <div className="mb-2 flex items-baseline justify-between gap-2">
          <Tooltip content="Population Stability Index per feature. Warning band > 0.1, alert > 0.2 — both counted here, matching the detector's own verdict.">
            <span className="cursor-help text-2xs font-bold uppercase tracking-[0.14em] text-ink-faint">
              Features shifted
            </span>
          </Tooltip>
          <span
            className={cn(
              "text-numeric text-xs font-bold",
              shifted > 0 ? "text-warn-fg" : "text-ok-fg"
            )}
          >
            {shifted} / {total}
          </span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-surface-3">
          <motion.div
            className={cn(
              "h-full rounded-full",
              shifted > total / 2 ? "bg-danger" : shifted > 0 ? "bg-warn" : "bg-ok"
            )}
            initial={{ width: 0 }}
            animate={{ width: `${pct}%` }}
            transition={{ duration: 0.5, ease: [0.2, 0.8, 0.25, 1] }}
          />
        </div>
      </div>

      {worst && band ? (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line bg-surface-2 px-3 py-2">
          <span className="text-numeric min-w-0 truncate text-xs text-ink">{worst.name}</span>
          <span className="flex items-center gap-2">
            <span className={cn("text-numeric text-xs font-bold", band.text)}>
              PSI {worst.psi.toFixed(3)}
            </span>
            <Badge variant={band.variant}>{worst.status}</Badge>
          </span>
        </div>
      ) : (
        <div className="mb-4 flex items-center gap-2 text-xs text-ok-fg">
          <span className="size-1.5 rounded-full bg-ok" />
          No features currently shifted beyond threshold.
        </div>
      )}

      <dl className="space-y-1.5 border-t border-line pt-3">
        <Row
          k="Calibration"
          v={
            <span className={calibration.status === "degraded" ? "text-danger-fg" : "text-ok-fg"}>
              {calibration.max_deviation_pct != null
                ? `${calibration.max_deviation_pct.toFixed(1)}pp max dev`
                : "—"}
              {" · "}
              {calibration.status ?? "—"}
            </span>
          }
        />
        {calibration.window_size != null && (
          <Row k="Window" v={`${calibration.window_size} predictions`} muted />
        )}
        {calibration.last_checked_at && (
          <Row
            k="Last checked"
            v={String(calibration.last_checked_at).slice(0, 19).replace("T", " ")}
            muted
          />
        )}
        {sensorCoverage && (
          <Row
            k="Sensor coverage"
            muted
            v={
              <span className={sensorCoverage.drift === "degraded" ? "text-warn-fg" : undefined}>
                {sensorCoverage.full != null ? `${(sensorCoverage.full * 100).toFixed(1)}%` : "—"}
                {" · "}
                {sensorCoverage.drift ?? "—"}
              </span>
            }
          />
        )}
      </dl>
    </Panel>
  );
}

function Row({ k, v, muted }: { k: string; v: React.ReactNode; muted?: boolean }) {
  return (
    <div className={cn("flex items-baseline justify-between gap-3", muted ? "text-2xs" : "text-xs")}>
      <dt className="text-ink-faint">{k}</dt>
      <dd className="text-numeric text-right text-ink-dim">{v}</dd>
    </div>
  );
}
