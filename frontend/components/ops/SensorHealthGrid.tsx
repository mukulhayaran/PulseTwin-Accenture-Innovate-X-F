"use client";

// Station x sensor grid. There is no persisted per-sensor reading history exposed by any
// endpoint (ingestion/data_quality.py's DataQualityTracker lives in the ingestion
// process's memory, not a queryable table -- see ADR-033's note on what was scoped out of
// this round), so a click shows the one real fact the API has for that cell -- current
// status and, for a dropout, how long -- rather than a fabricated sparkline.

import { useState } from "react";
import type { StationCfg } from "@/lib/api";

interface Dropout { station_id: number; sensor: string; since: string; duration_s: number }

interface Props {
  stations: StationCfg[];
  dropouts: Dropout[];
}

interface Selected { stationId: number; stationName: string; sensor: string }

export default function SensorHealthGrid({ stations, dropouts }: Props) {
  const [selected, setSelected] = useState<Selected | null>(null);

  if (!stations || stations.length === 0) {
    return (
      <Panel title="SENSOR HEALTH">
        <div className="text-txt-muted text-[11px] py-6 text-center">Loading station config…</div>
      </Panel>
    );
  }

  const dropoutFor = (stationId: number, sensor: string) =>
    dropouts.find((d) => d.station_id === stationId && d.sensor === sensor);

  const ordered = [...stations].sort((a, b) => a.position - b.position);

  return (
    <Panel title="SENSOR HEALTH" note={`${dropouts.length} dropout${dropouts.length === 1 ? "" : "s"} active`}>
      <div className="max-h-72 overflow-y-auto pr-1 space-y-1">
        {ordered.map((s) => (
          <div key={s.station_id} className="flex items-center gap-2 text-[11px]">
            <span className="w-32 shrink-0 truncate text-txt-muted">
              S{s.station_id} {s.station_name}
            </span>
            {s.sensor_tier === "manual" ? (
              <span
                className="rounded border border-bg-surface px-1.5 py-0.5 text-txt-muted"
                title="Manual station -- no continuous sensor expected"
              >
                MANUAL
              </span>
            ) : (
              <div className="flex flex-wrap gap-1">
                {s.sensors.map((sensor) => {
                  const down = dropoutFor(s.station_id, sensor);
                  const isSelected = selected?.stationId === s.station_id && selected.sensor === sensor;
                  return (
                    <button
                      key={sensor}
                      onClick={() => setSelected({ stationId: s.station_id, stationName: s.station_name, sensor })}
                      className={`rounded border px-1.5 py-0.5 ${
                        down
                          ? "border-signal-amber bg-signal-amber/10 text-signal-amber"
                          : "border-signal-green/40 bg-signal-green/5 text-signal-green"
                      } ${isSelected ? "ring-1 ring-signal-blue" : ""}`}
                      title={sensor}
                    >
                      {sensor.replace(/_.*$/, "")}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        ))}
      </div>

      {selected && (
        <div className="mt-3 border-t border-bg-surface pt-2 text-[11px]">
          {(() => {
            const down = dropoutFor(selected.stationId, selected.sensor);
            return down ? (
              <div className="text-signal-amber">
                S{selected.stationId} {selected.sensor} — DOWN since {down.since} (
                {Math.round(down.duration_s)}s)
              </div>
            ) : (
              <div className="text-signal-green">
                S{selected.stationId} {selected.sensor} — receiving data normally
              </div>
            );
          })()}
        </div>
      )}
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
