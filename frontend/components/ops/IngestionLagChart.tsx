"use client";

import { useEffect, useState } from "react";
import { Area, AreaChart, CartesianGrid, ReferenceLine, Tooltip as RTooltip, XAxis, YAxis } from "recharts";
import { axisProps, gridProps, theme } from "@/lib/theme";
import { cn } from "@/lib/cn";
import { Badge, ChartFrame, ChartTooltip, Panel } from "@/components/ui";

// GET /api/system/ingestion-lag exposes only the current lag reading -- there is no
// server-side history. This keeps a small client-side rolling buffer (capped at 60 points)
// so a trend is visible across polls. The parent page should mount this with `key={plant}`
// so the buffer resets on a plant switch instead of mixing two lines' lag together.

interface Props {
  lag: any | null;
}

export default function IngestionLagChart({ lag }: Props) {
  const [series, setSeries] = useState<{ t: string; lag: number }[]>([]);

  useEffect(() => {
    if (!lag || !lag.samples || lag.current_lag_s == null) return;
    setSeries((prev) =>
      [...prev, { t: new Date().toLocaleTimeString("en-GB", { hour12: false }), lag: lag.current_lag_s }].slice(-60)
    );
  }, [lag]);

  if (!lag || !lag.samples) {
    return (
      <Panel title="Ingestion lag">
        <div className="flex flex-col items-center gap-2 py-10 text-center">
          <span className="flex size-8 items-center justify-center rounded-full border border-line bg-surface-2 text-ink-faint">
            ⌁
          </span>
          <p className="max-w-56 text-2xs leading-relaxed text-ink-faint">
            No live ingestion telemetry yet — <span className="text-numeric">ingestion/</span> has
            not run against this plant.
          </p>
        </div>
      </Panel>
    );
  }

  const threshold = lag.threshold_s ?? 5;
  const behind = lag.status === "behind" || (lag.current_lag_s != null && lag.current_lag_s > threshold);

  return (
    <Panel
      title="Ingestion lag"
      tone={behind ? "warn" : "default"}
      actions={<Badge variant={behind ? "warn" : "ok"} dot>{behind ? "Behind" : "Keeping up"}</Badge>}
    >
      <div className="mb-3 flex flex-wrap items-baseline gap-x-5 gap-y-1.5">
        <Reading
          label="Current"
          value={lag.current_lag_s != null ? `${lag.current_lag_s.toFixed(2)}s` : "—"}
          tone={behind ? "warn" : "ok"}
        />
        <Reading label="Mean" value={lag.mean_lag_s != null ? `${lag.mean_lag_s.toFixed(2)}s` : "—"} />
        <Reading label="Threshold" value={`${threshold.toFixed(1)}s`} />
        <span className="text-numeric text-2xs text-ink-faint">{lag.samples} samples</span>
      </div>

      {series.length > 1 ? (
        <ChartFrame height={140}>
          <AreaChart data={series} margin={{ top: 4, right: 6, left: -12, bottom: 0 }}>
            <defs>
              <linearGradient id="lagFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={behind ? theme.warn : theme.info} stopOpacity={0.35} />
                <stop offset="100%" stopColor={behind ? theme.warn : theme.info} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid {...gridProps} />
            <XAxis dataKey="t" {...axisProps} interval={Math.max(1, Math.floor(series.length / 5))} />
            <YAxis {...axisProps} width={34} />
            <RTooltip
              cursor={{ stroke: theme.lineStrong, strokeDasharray: "3 3" }}
              content={
                <ChartTooltip
                  rows={(p) => (
                    <div className="flex items-center justify-between gap-4 text-xs">
                      <span className="text-on-dark-soft">Lag</span>
                      <span className="text-numeric font-bold text-on-dark">
                        {Number(p[0]?.value).toFixed(2)}s
                      </span>
                    </div>
                  )}
                />
              }
            />
            <ReferenceLine
              y={threshold}
              stroke={theme.warn}
              strokeDasharray="4 3"
              label={{ value: "threshold", fill: theme.inkFaint, fontSize: 9, position: "insideTopRight" }}
            />
            <Area
              type="monotone" dataKey="lag" isAnimationActive={false} strokeWidth={1.5}
              stroke={behind ? theme.warn : theme.info} fill="url(#lagFill)"
            />
          </AreaChart>
        </ChartFrame>
      ) : (
        <div className="py-6 text-center text-2xs text-ink-faint">Accumulating samples…</div>
      )}
    </Panel>
  );
}

function Reading({
  label, value, tone,
}: {
  label: string; value: string; tone?: "ok" | "warn";
}) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-2xs uppercase tracking-[0.14em] text-ink-faint">{label}</span>
      <span
        className={cn(
          "text-numeric text-xs font-bold",
          tone === "warn" ? "text-warn-fg" : tone === "ok" ? "text-ok-fg" : "text-ink-dim"
        )}
      >
        {value}
      </span>
    </span>
  );
}
