"use client";

// The landing's station ticker (ADR-075): every station the twin mirrors, read from
// /api/config/stations -- never a hardcoded list (CLAUDE.md §1), never placeholder
// names. Renders nothing until the API answers, and nothing if it cannot. The dot is
// the station's sensing tier in the provenance colours: green measured, amber inferred.

import { useEffect, useState } from "react";
import { getStations, type StationCfg } from "@/lib/api";
import { Marquee } from "@/components/magicui/marquee";

export function StationTicker({ plant = "plant_a" }: { plant?: string }) {
  const [stations, setStations] = useState<StationCfg[] | null>(null);

  useEffect(() => {
    let live = true;
    getStations(plant)
      .then((r) => live && setStations([...r.stations].sort((a, b) => a.position - b.position)))
      .catch(() => live && setStations(null));
    return () => {
      live = false;
    };
  }, [plant]);

  if (!stations?.length) return null;
  const inferred = stations.filter((s) => s.sensor_tier === "manual").length;

  return (
    <section aria-label="Stations the twin mirrors" className="border-b border-line bg-canvas">
      <div className="mx-auto flex w-full max-w-[1280px] items-center gap-6 px-5 py-3 sm:px-8">
        <p className="hidden shrink-0 text-xs text-ink-dim md:block">
          <span className="text-numeric font-bold text-ink">{stations.length}</span> stations mirrored
          {inferred > 0 && (
            <>
              {" · "}
              <span className="text-numeric font-bold text-warn-fg">{inferred}</span> inferred
            </>
          )}
        </p>
        <Marquee
          pauseOnHover
          repeat={2}
          className="min-w-0 flex-1 [--duration:90s] [--gap:2rem] [mask-image:linear-gradient(90deg,transparent,#000_6%,#000_94%,transparent)]"
        >
          {stations.map((s) => (
            <span key={s.station_id} className="flex shrink-0 items-center gap-2 text-xs">
              <span
                aria-hidden
                className={s.sensor_tier === "manual" ? "size-1.5 rounded-full bg-warn" : "size-1.5 rounded-full bg-ok"}
              />
              <span className="text-numeric font-bold text-ink">S{s.station_id}</span>
              <span className="text-ink-dim">{s.station_name}</span>
              {s.sensor_tier === "manual" && <span className="sr-only">(inferred, no sensors)</span>}
            </span>
          ))}
        </Marquee>
      </div>
    </section>
  );
}
