"use client";

// Renders GET /api/model/health verbatim. Note: that endpoint (serve/routers/model_health.py)
// does not itself carry pr_auc -- only /api/model/registry's history[].metrics does. The ops
// page fetches the registry once and passes the current champion's metrics down as
// `championMetrics` so this panel can show a real PR-AUC rather than a fabricated one.

interface Props {
  health: any | null;
  championMetrics?: any | null;
  hasChallenger?: boolean;
  onViewComparison?: () => void;
}

const STATUS_DOT: Record<string, string> = {
  healthy: "bg-signal-green",
  warning: "bg-signal-amber",
  degraded: "bg-signal-red",
};

export default function ModelHealthPanel({ health, championMetrics, hasChallenger, onViewComparison }: Props) {
  if (!health) {
    return (
      <Panel title="MODEL HEALTH">
        <div className="text-txt-muted text-[11px] py-6 text-center">Loading model health…</div>
      </Panel>
    );
  }

  const overall = health.drift?.overall_status ?? "healthy";
  const dot = STATUS_DOT[overall] ?? "bg-txt-muted";
  const cm = health.current_model ?? {};
  const cal = health.drift?.calibration ?? {};
  const fb = health.fallback ?? {};

  return (
    <Panel title="MODEL HEALTH">
      <div className="flex items-center gap-2 mb-3">
        <span className={`inline-block h-2.5 w-2.5 rounded-full ${dot}`} title={overall} />
        <span className="text-[13px] font-semibold truncate">{cm.id ?? "no champion registered"}</span>
        {cm.status && <Badge>{cm.status}</Badge>}
      </div>

      <div className="grid grid-cols-2 gap-3 mb-3 text-[11px]">
        <Fact k="AGE" v={cm.age_days != null ? `${cm.age_days}d` : "—"} />
        <Fact k="PR-AUC"
              v={championMetrics?.pr_auc != null ? championMetrics.pr_auc.toFixed(3) : "—"} />
        <Fact k="CALIBRATION"
              v={cal.max_deviation_pct != null ? `${cal.max_deviation_pct.toFixed(1)}pp max dev` : "—"}
              tone={cal.status === "degraded" ? "text-signal-red" : "text-signal-green"} />
        <Fact k="TRAINING DATA" v={cm.training_data_range ?? "—"} />
      </div>

      <div className="border-t border-bg-surface pt-2 mb-2 text-[11px] leading-relaxed">
        <span className="text-txt-muted">Fallback:</span> SPC rules ·{" "}
        <span className="text-txt-muted">Status:</span>{" "}
        <span className={fb.status === "active" ? "text-signal-amber font-semibold" : "text-txt-muted"}>
          {(fb.status ?? "—").toString().toUpperCase()}
        </span>{" "}
        · <span className="text-txt-muted">Shadow precision:</span>{" "}
        {fb.shadow_precision != null ? `${(fb.shadow_precision * 100).toFixed(0)}%` : "—"}{" "}
        · <span className="text-txt-muted">recall:</span>{" "}
        {fb.shadow_recall != null ? `${(fb.shadow_recall * 100).toFixed(0)}%` : "—"}
      </div>

      <div className={`text-[11px] ${overall === "healthy" ? "text-signal-green" : "text-signal-amber"}`}>
        {health.recommendation}
      </div>

      {hasChallenger && (
        <button
          onClick={onViewComparison}
          className="mt-2 text-[10px] px-2 py-1 border border-signal-purple/50 text-signal-purple rounded hover:bg-signal-purple/10"
        >
          View comparison report ↓
        </button>
      )}
    </Panel>
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

function Fact({ k, v, tone }: { k: string; v: string; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-txt-muted text-[10px]">{k}</div>
      <div className={`font-semibold truncate ${tone ?? ""}`}>{v}</div>
    </div>
  );
}

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[9px] px-1.5 py-0.5 rounded border border-bg-surface text-txt-muted uppercase tracking-widest shrink-0">
      {children}
    </span>
  );
}
