"use client";

// Sits below the line map on the Supervisor view. Sourced from a 10s poll of
// GET /api/system/sensor-dropouts (see app/supervisor/page.tsx) -- this is a real infra
// signal, not a simulated one, so it stays live even if the replay clock is paused.
//
// NOT /api/system/health: that endpoint requires `manager` and this page pins itself to
// `supervisor`, which returned a silent 403 on every poll (QA bug #33).

import { AnimatePresence, motion } from "motion/react";

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
  return (
    <AnimatePresence>
      {dropouts?.length > 0 && (
        <motion.div
          role="alert"
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto" }}
          exit={{ opacity: 0, height: 0 }}
          transition={{ duration: 0.24, ease: [0.2, 0.8, 0.25, 1] }}
          className="overflow-hidden"
        >
          <div className="rounded-lg border border-warn/45 bg-warn/[0.08] p-3">
            <div className="mb-2 flex items-center gap-2">
              <motion.span
                className="size-2 shrink-0 rounded-full bg-warn"
                animate={{ opacity: [1, 0.3, 1] }}
                transition={{ duration: 1.5, repeat: Infinity }}
              />
              <span className="text-xs font-semibold text-warn-fg">
                {dropouts.length} sensor dropout{dropouts.length > 1 ? "s" : ""} active
              </span>
              <span className="text-2xs text-ink-faint">
                affected stations fall back to inferred values
              </span>
            </div>
            <ul className="space-y-1">
              {dropouts.map((d) => (
                <li
                  key={`${d.station_id}-${d.sensor}`}
                  className="text-numeric flex flex-wrap items-baseline gap-x-2 text-2xs text-ink-dim"
                >
                  <span className="text-ink">S{d.station_id}</span>
                  <span>{d.sensor}</span>
                  <span className="text-warn-fg">down {fmtDuration(d.duration_s)}</span>
                  <span className="text-ink-faint">since {d.since}</span>
                </li>
              ))}
            </ul>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
