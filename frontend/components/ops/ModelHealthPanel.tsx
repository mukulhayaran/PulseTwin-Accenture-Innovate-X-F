"use client";

// Renders GET /api/model/health verbatim. Note: that endpoint (serve/routers/model_health.py)
// does not itself carry pr_auc -- only /api/model/registry's history[].metrics does. The ops
// page fetches the registry once and passes the current champion's metrics down as
// `championMetrics` so this panel can show a real PR-AUC rather than a fabricated one.

import { cn } from "@/lib/cn";
import { Badge, Button, Panel, Skeleton, Tooltip } from "@/components/ui";

interface Props {
  health: any | null;
  championMetrics?: any | null;
  hasChallenger?: boolean;
  onViewComparison?: () => void;
}

const STATUS_VARIANT: Record<string, "ok" | "warn" | "danger"> = {
  healthy: "ok",
  warning: "warn",
  degraded: "danger",
};

export default function ModelHealthPanel({
  health, championMetrics, hasChallenger, onViewComparison,
}: Props) {
  if (!health) {
    return (
      <Panel title="Model health">
        <div className="space-y-3">
          <Skeleton className="h-5 w-56" />
          <div className="grid grid-cols-2 gap-4">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="space-y-1.5">
                <Skeleton className="h-2.5 w-16" />
                <Skeleton className="h-4 w-24" />
              </div>
            ))}
          </div>
        </div>
      </Panel>
    );
  }

  const overall = health.drift?.overall_status ?? "healthy";
  const variant = STATUS_VARIANT[overall] ?? "neutral";
  const cm = health.current_model ?? {};
  const cal = health.drift?.calibration ?? {};
  const fb = health.fallback ?? {};

  return (
    <Panel
      title="Model health"
      actions={<Badge variant={variant} dot>{overall}</Badge>}
    >
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="text-numeric min-w-0 truncate text-sm font-bold text-ink">
          {cm.id ?? "no champion registered"}
        </span>
        {cm.status && <Badge variant="info">{cm.status}</Badge>}
      </div>

      <div className="mb-4 grid grid-cols-2 gap-4">
        <MiniFact label="Age" value={cm.age_days != null ? `${cm.age_days}d` : "—"} />
        <MiniFact
          label="PR-AUC"
          value={championMetrics?.pr_auc != null ? championMetrics.pr_auc.toFixed(3) : "—"}
        />
        <MiniFact
          label="Calibration"
          value={cal.max_deviation_pct != null ? `${cal.max_deviation_pct.toFixed(1)}pp max dev` : "—"}
          tone={cal.status === "degraded" ? "danger" : "ok"}
        />
        <MiniFact label="Training data" value={cm.training_data_range ?? "—"} />
      </div>

      <div className="space-y-1.5 rounded-lg border border-line bg-surface-2 p-3 text-2xs">
        <div className="flex items-center justify-between gap-2">
          <span className="uppercase tracking-[0.14em] text-ink-faint">Fallback</span>
          <span className="flex items-center gap-2">
            <span className="text-ink-dim">SPC rules</span>
            <Badge variant={fb.status === "active" ? "warn" : "neutral"}>
              {(fb.status ?? "—").toString()}
            </Badge>
          </span>
        </div>
        <Tooltip content="Measured in shadow against the same labels — published as-is, see ADR-035.">
          <div className="flex cursor-help items-center justify-between gap-2">
            <span className="text-ink-faint">Shadow precision / recall</span>
            <span className="text-numeric text-ink-dim">
              {fb.shadow_precision != null ? `${(fb.shadow_precision * 100).toFixed(0)}%` : "—"}
              {" / "}
              {fb.shadow_recall != null ? `${(fb.shadow_recall * 100).toFixed(0)}%` : "—"}
            </span>
          </div>
        </Tooltip>
      </div>

      <p
        className={cn(
          "mt-3 text-xs leading-relaxed",
          overall === "healthy" ? "text-ok-fg" : "text-warn-fg"
        )}
      >
        {health.recommendation}
      </p>

      {hasChallenger && (
        <Button variant="ai" size="xs" className="mt-3" onClick={onViewComparison}>
          View comparison report ↓
        </Button>
      )}
    </Panel>
  );
}

function MiniFact({
  label, value, tone,
}: {
  label: string; value: string; tone?: "ok" | "danger";
}) {
  return (
    <div className="min-w-0">
      <div className="text-2xs uppercase tracking-[0.14em] text-ink-faint">{label}</div>
      <div
        className={cn(
          "text-numeric truncate text-xs font-bold",
          tone === "danger" ? "text-danger-fg" : tone === "ok" ? "text-ok-fg" : "text-ink"
        )}
      >
        {value}
      </div>
    </div>
  );
}
