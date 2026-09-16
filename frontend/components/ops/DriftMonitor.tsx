"use client";

// The backend (serve/routers/model_health.py) only ever exposes the LATEST psi/calibration
// snapshot -- there is no drift-history endpoint. Rather than fabricate a 7-day heatmap with
// invented daily values, this renders an honest "current PSI status" view: the worst shifted
// feature (if any), a shifted/total summary bar, and the latest calibration reading.

interface Props {
  psiSummary: any | null | undefined;
  calibration: any | null | undefined;
  sensorCoverage?: any | null;
}

function dotFor(psi: number) {
  if (psi > 0.2) return { char: "●", cls: "text-signal-red" };
  if (psi > 0.1) return { char: "◐", cls: "text-signal-amber" };
  return { char: "○", cls: "text-signal-green" };
}

export default function DriftMonitor({ psiSummary, calibration, sensorCoverage }: Props) {
  if (!psiSummary || !calibration) {
    return (
      <Panel title="DRIFT MONITOR">
        <div className="text-txt-muted text-[11px] py-6 text-center">Loading drift status…</div>
      </Panel>
    );
  }

  const shifted = psiSummary.features_shifted ?? 0;
  const total = psiSummary.features_total ?? 0;
  const pct = total > 0 ? (shifted / total) * 100 : 0;
  const worst = psiSummary.worst_feature;

  return (
    <Panel title="DRIFT MONITOR" note="current snapshot — mlops/drift_detector.py runs periodically">
      <div className="mb-3">
        <div className="flex justify-between text-[11px] mb-1">
          <span className="text-txt-muted">FEATURES SHIFTED</span>
          <span className={shifted > 0 ? "text-signal-amber" : "text-signal-green"}>
            {shifted} / {total}
          </span>
        </div>
        <div className="h-1.5 bg-bg-surface rounded overflow-hidden">
          <div
            className={`h-full ${shifted > total / 2 ? "bg-signal-red" : shifted > 0 ? "bg-signal-amber" : "bg-signal-green"}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {worst ? (
        <div className="flex items-center justify-between text-[11px] mb-3 border border-bg-surface rounded px-2 py-1.5">
          <span>
            <span className={dotFor(worst.psi).cls}>{dotFor(worst.psi).char}</span>{" "}
            <span className="ml-1">{worst.name}</span>
          </span>
          <span className={dotFor(worst.psi).cls}>
            PSI {worst.psi.toFixed(3)} · {worst.status}
          </span>
        </div>
      ) : (
        <div className="text-signal-green text-[11px] mb-3">○ No features currently shifted beyond threshold.</div>
      )}

      <div className="border-t border-bg-surface pt-2 text-[11px] space-y-1">
        <div className="flex justify-between">
          <span className="text-txt-muted">CALIBRATION</span>
          <span className={calibration.status === "degraded" ? "text-signal-red" : "text-signal-green"}>
            {calibration.max_deviation_pct != null ? `${calibration.max_deviation_pct.toFixed(1)}pp max dev` : "—"}
            {" · "}
            {calibration.status ?? "—"}
          </span>
        </div>
        {calibration.window_size != null && (
          <div className="flex justify-between text-txt-muted text-[10px]">
            <span>window</span>
            <span>{calibration.window_size} predictions</span>
          </div>
        )}
        {calibration.last_checked_at && (
          <div className="flex justify-between text-txt-muted text-[10px]">
            <span>last checked</span>
            <span>{String(calibration.last_checked_at).slice(0, 19).replace("T", " ")}</span>
          </div>
        )}
        {sensorCoverage && (
          <div className="flex justify-between text-txt-muted text-[10px]">
            <span>sensor coverage</span>
            <span className={sensorCoverage.drift === "degraded" ? "text-signal-amber" : ""}>
              {sensorCoverage.full != null ? `${(sensorCoverage.full * 100).toFixed(1)}%` : "—"}
              {" · "}
              {sensorCoverage.drift ?? "—"}
            </span>
          </div>
        )}
      </div>
    </Panel>
  );
}

function Panel({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <div className="bg-bg-card border border-bg-surface rounded p-3">
      <div className="flex items-baseline justify-between mb-2 gap-2">
        <h2 className="text-txt-muted tracking-widest text-[11px]">{title}</h2>
        {note && <span className="text-[9px] text-txt-muted text-right">{note}</span>}
      </div>
      {children}
    </div>
  );
}
