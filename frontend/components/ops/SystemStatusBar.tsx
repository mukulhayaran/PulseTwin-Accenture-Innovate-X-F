"use client";

// Slim top-of-page status strip for the ops console. Renders GET /api/system/health
// as-is -- no derived "is this actually fine" judgment beyond the fields the backend
// already computed (status, connected, dropout count).

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
      <div className="flex items-center gap-2 border border-bg-surface rounded px-3 py-2 bg-bg-card text-[11px] text-txt-muted">
        <span className="inline-block h-2 w-2 rounded-full bg-txt-muted/60 animate-pulse" />
        Loading system status…
      </div>
    );
  }

  const operational = system.status === "operational";
  const db = system.database ?? {};
  const ingestion = system.ingestion ?? {};
  const dropoutCount = ingestion.sensor_dropouts_active ?? 0;

  return (
    <div
      className={`flex flex-wrap items-center gap-x-6 gap-y-1.5 border rounded px-3 py-2 bg-bg-card text-[11px] ${
        operational ? "border-bg-surface" : "border-signal-red/50"
      }`}
    >
      <span className="flex items-center gap-1.5">
        <span
          className={`inline-block h-2 w-2 rounded-full ${
            operational ? "bg-signal-green" : "bg-signal-red animate-pulse"
          }`}
        />
        <span className={`font-semibold tracking-widest ${operational ? "text-signal-green" : "text-signal-red"}`}>
          {operational ? "OPERATIONAL" : "DEGRADED"}
        </span>
      </span>

      <span className="text-txt-muted">
        UPTIME <span className="text-txt-primary">{formatUptime(system.uptime_seconds)}</span>
      </span>

      <span className="text-txt-muted">
        DB{" "}
        <span className={db.connected ? "text-signal-green" : "text-signal-red"}>●</span>{" "}
        <span className="text-txt-primary">{db.type ?? "—"}</span>
        {db.latency_ms != null && <> · {db.latency_ms.toFixed(1)}ms</>}
        {db.connections_active != null && db.connections_max != null && (
          <> · {db.connections_active}/{db.connections_max} conns</>
        )}
      </span>

      <span className="text-txt-muted">
        PREDICTIONS/HR <span className="text-txt-primary">{system.predictions_last_hour ?? "—"}</span>
      </span>

      {dropoutCount > 0 && (
        <span className="text-signal-amber">
          {dropoutCount} sensor dropout{dropoutCount > 1 ? "s" : ""}
        </span>
      )}

      {system.last_prediction_at && (
        <span className="text-txt-muted ml-auto">
          last prediction {String(system.last_prediction_at).slice(11, 19)}
        </span>
      )}
    </div>
  );
}
