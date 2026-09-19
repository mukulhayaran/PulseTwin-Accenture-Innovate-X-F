"use client";

// Slim top-of-page status strip for the ops console. Renders GET /api/system/health
// as-is -- no derived "is this actually fine" judgment beyond the fields the backend
// already computed (status, connected, dropout count).

import { cn } from "@/lib/cn";
import { Badge, Skeleton } from "@/components/ui";

interface Props {
  system: any | null;
}

function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export default function SystemStatusBar({ system }: Props) {
  if (!system) {
    return (
      <div className="panel-surface flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5">
        <Skeleton className="h-4 w-28" />
        <Skeleton className="h-3.5 w-24" />
        <Skeleton className="h-3.5 w-40" />
        <Skeleton className="h-3.5 w-32" />
      </div>
    );
  }

  const operational = system.status === "operational";
  const db = system.database ?? {};
  const ingestion = system.ingestion ?? {};
  const dropoutCount = ingestion.sensor_dropouts_active ?? 0;

  return (
    <div
      className={cn(
        "panel-surface flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5",
        !operational && "border-danger/45"
      )}
    >
      <Badge variant={operational ? "ok" : "danger"} dot>
        {operational ? "Operational" : "Degraded"}
      </Badge>

      <Item label="Uptime" value={formatUptime(system.uptime_seconds)} />

      <span className="flex items-center gap-1.5 text-2xs">
        <span className="uppercase tracking-[0.14em] text-ink-faint">DB</span>
        <span className={cn("size-1.5 rounded-full", db.connected ? "bg-ok" : "bg-danger")} />
        <span className="text-numeric text-ink-dim">
          {db.type ?? "—"}
          {db.latency_ms != null && ` · ${db.latency_ms.toFixed(1)}ms`}
          {db.connections_active != null && db.connections_max != null &&
            ` · ${db.connections_active}/${db.connections_max} conns`}
        </span>
      </span>

      <Item label="Predictions/hr" value={String(system.predictions_last_hour ?? "—")} />

      {dropoutCount > 0 && (
        <Badge variant="warn" dot>
          {dropoutCount} sensor dropout{dropoutCount > 1 ? "s" : ""}
        </Badge>
      )}

      {system.last_prediction_at && (
        <span className="text-numeric ml-auto text-2xs text-ink-faint">
          last prediction {String(system.last_prediction_at).slice(11, 19)}
        </span>
      )}
    </div>
  );
}

function Item({ label, value }: { label: string; value: string }) {
  return (
    <span className="flex items-center gap-1.5 text-2xs">
      <span className="uppercase tracking-[0.14em] text-ink-faint">{label}</span>
      <span className="text-numeric text-ink-dim">{value}</span>
    </span>
  );
}
