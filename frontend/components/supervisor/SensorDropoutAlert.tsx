"use client";

// Sits below the line map on the Supervisor view. Sourced from a 10s poll of
// GET /api/system/health's `ingestion.dropout_details` (see app/supervisor/page.tsx) --
// this is a real infra signal, not a simulated one, so it stays live even if the
// replay clock is paused.

interface Dropout {
  station_id: number;
  sensor: string;
  since: string;
  duration_s: number;
}

function fmtDuration(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s % 60);
  return `${m}m${rem ? ` ${rem}s` : ""}`;
}

export default function SensorDropoutAlert({ dropouts }: { dropouts: Dropout[] }) {
  if (!dropouts || dropouts.length === 0) return null;

  return (
    <div className="animate-pulse rounded border-2 border-signal-amber bg-signal-amber/10 p-2 font-mono text-[11px]">
      <div className="font-semibold text-signal-amber">
        ⚠ {dropouts.length} sensor dropout{dropouts.length > 1 ? "s" : ""} active
      </div>
      <div className="mt-1 space-y-0.5 text-txt-primary">
        {dropouts.map((d) => (
          <div key={`${d.station_id}-${d.sensor}`}>
            S{d.station_id} {d.sensor} — down {fmtDuration(d.duration_s)}{" "}
            <span className="text-txt-muted">(since {d.since})</span>
          </div>
        ))}
      </div>
    </div>
  );
}
