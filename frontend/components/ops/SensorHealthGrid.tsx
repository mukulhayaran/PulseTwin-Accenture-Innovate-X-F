"use client";

// Station x sensor grid. There is no persisted per-sensor reading history exposed by any
// endpoint (ingestion/data_quality.py's DataQualityTracker lives in the ingestion
// process's memory, not a queryable table -- see ADR-033's note on what was scoped out of
// this round), so a click shows the one real fact the API has for that cell -- current
// status and, for a dropout, how long -- rather than a fabricated sparkline.

import { useState } from "react";
import type { StationCfg } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Badge, Panel, Skeleton, Tooltip } from "@/components/ui";

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
      <Panel title="Sensor health">
        <div className="space-y-2">
          {Array.from({ length: 7 }, (_, i) => (
            <div key={i} className="flex gap-2">
              <Skeleton className="h-4 w-32 shrink-0" />
              <Skeleton className="h-4 flex-1" style={{ animationDelay: `${i * 30}ms` }} />
            </div>
          ))}
        </div>
      </Panel>
    );
  }

  const dropoutFor = (stationId: number, sensor: string) =>
    dropouts.find((d) => d.station_id === stationId && d.sensor === sensor);

  const ordered = [...stations].sort((a, b) => a.position - b.position);

  return (
    <Panel
      title="Sensor health"
      actions={
        <Badge variant={dropouts.length ? "warn" : "ok"} dot>
          {dropouts.length} dropout{dropouts.length === 1 ? "" : "s"}
        </Badge>
      }
    >
      <div className="scroll-fade-b max-h-72 space-y-1 overflow-y-auto pr-1">
        {ordered.map((s) => (
          <div key={s.station_id} className="flex items-center gap-2.5">
            <span className="text-numeric w-32 shrink-0 truncate text-2xs text-ink-faint">
              <span className="text-ink-dim">S{s.station_id}</span> {s.station_name}
            </span>

            {s.sensor_tier === "manual" ? (
              <Tooltip content="Manual station — no continuous sensor expected. Values here are inferred, never measured.">
                <span className="hatch-inferred cursor-help rounded-md border border-warn/35 px-1.5 py-0.5 text-2xs font-bold uppercase tracking-wider text-warn-fg">
                  Manual
                </span>
              </Tooltip>
            ) : (
              <div className="flex min-w-0 flex-wrap gap-1">
                {s.sensors.map((sensor) => {
                  const down = dropoutFor(s.station_id, sensor);
                  const isSelected =
                    selected?.stationId === s.station_id && selected.sensor === sensor;
                  return (
                    <Tooltip key={sensor} content={sensor.replace(/_/g, " ")}>
                      <button
                        onClick={() =>
                          setSelected({ stationId: s.station_id, stationName: s.station_name, sensor })
                        }
                        className={cn(
                          "rounded-md border px-1.5 py-0.5 text-2xs font-bold transition-colors duration-150",
                          down
                            ? "border-warn/50 bg-warn/12 text-warn-fg"
                            : "border-ok/30 bg-ok/[0.07] text-ok-fg hover:bg-ok/15",
                          isSelected && "ring-1 ring-info"
                        )}
                      >
                        {sensor.replace(/_.*$/, "")}
                      </button>
                    </Tooltip>
                  );
                })}
              </div>
            )}
          </div>
        ))}
      </div>

      {selected && (
        <div className="mt-3 border-t border-line pt-3 text-xs">
          {(() => {
            const down = dropoutFor(selected.stationId, selected.sensor);
            return down ? (
              <div className="text-warn-fg">
                <span className="text-numeric">
                  S{selected.stationId} {selected.sensor}
                </span>{" "}
                — down since {down.since} ({Math.round(down.duration_s)}s)
              </div>
            ) : (
              <div className="text-ok-fg">
                <span className="text-numeric">
                  S{selected.stationId} {selected.sensor}
                </span>{" "}
                — receiving data normally
              </div>
            );
          })()}
        </div>
      )}
    </Panel>
  );
}
