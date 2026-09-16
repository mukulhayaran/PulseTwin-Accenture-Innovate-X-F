"use client";

import { useEffect, useState } from "react";
import {
  Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

// GET /api/system/ingestion-lag exposes only the current lag reading -- there is no
// server-side history. This keeps a small client-side rolling buffer (capped at 60 points)
// so a trend is visible across polls. The parent page should mount this with `key={plant}`
// so the buffer resets on a plant switch instead of mixing two lines' lag together.

interface Props {
  lag: any | null;
}

const AXIS = { stroke: "#6b7280", fontSize: 10, fontFamily: "monospace" };

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
      <Panel title="INGESTION LAG">
        <div className="text-txt-muted text-[11px] py-6 text-center leading-relaxed">
          No live ingestion telemetry yet — ingestion/ has not run against this plant.
        </div>
      </Panel>
    );
  }

  const threshold = lag.threshold_s ?? 5;
  const behind = lag.status === "behind" || (lag.current_lag_s != null && lag.current_lag_s > threshold);

  return (
    <Panel title="INGESTION LAG" tone={behind ? "border-signal-amber/50" : undefined}>
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 mb-2 text-[11px]">
        <span>
          <span className="text-txt-muted">CURRENT</span>{" "}
          <span className={behind ? "text-signal-amber font-semibold" : "text-signal-green font-semibold"}>
            {lag.current_lag_s != null ? `${lag.current_lag_s.toFixed(2)}s` : "—"}
          </span>
        </span>
        <span>
          <span className="text-txt-muted">MEAN</span> {lag.mean_lag_s != null ? `${lag.mean_lag_s.toFixed(2)}s` : "—"}
        </span>
        <span>
          <span className="text-txt-muted">THRESHOLD</span> {threshold.toFixed(1)}s
        </span>
        <span className="text-txt-muted">{lag.samples} samples</span>
      </div>

      {series.length > 1 ? (
        <ResponsiveContainer width="100%" height={140}>
          <AreaChart data={series}>
            <CartesianGrid stroke="#1f2937" vertical={false} />
            <XAxis dataKey="t" tick={AXIS} interval={Math.max(1, Math.floor(series.length / 6))} />
            <YAxis tick={AXIS} width={30} />
            <Tooltip contentStyle={{ background: "#111827", border: "1px solid #1f2937", fontSize: 11 }} />
            <ReferenceLine y={threshold} stroke="#f59e0b" strokeDasharray="4 3" />
            <Area
              type="monotone" dataKey="lag" isAnimationActive={false}
              stroke={behind ? "#f59e0b" : "#3b82f6"} fill={behind ? "#f59e0b22" : "#3b82f622"}
            />
          </AreaChart>
        </ResponsiveContainer>
      ) : (
        <div className="text-txt-muted text-[10px] py-4 text-center">Accumulating samples…</div>
      )}
    </Panel>
  );
}

function Panel({ title, tone, children }: { title: string; tone?: string; children: React.ReactNode }) {
  return (
    <div className={`bg-bg-card border rounded p-3 ${tone ?? "border-bg-surface"}`}>
      <h2 className="text-txt-muted tracking-widest text-[11px] mb-2">{title}</h2>
      {children}
    </div>
  );
}
