"use client";

import type { ReactNode } from "react";
import { ResponsiveContainer } from "recharts";
import { cn } from "@/lib/cn";
import { theme } from "@/lib/theme";

// Shared chart chrome. The three Recharts sites (app/manager, app/leadership,
// components/ops/IngestionLagChart) each had their own axis constants and used the
// DEFAULT Recharts tooltip -- a white box in a sans-serif face on a #0a0c11 page,
// which was the most obviously unstyled element in the product.

export function ChartFrame({
  height,
  children,
  className,
}: {
  height: number;
  children: React.ReactElement;
  className?: string;
}) {
  return (
    <div className={cn("w-full", className)} style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        {children}
      </ResponsiveContainer>
    </div>
  );
}

interface TooltipRow {
  name?: string | number;
  value?: string | number;
  color?: string;
  dataKey?: string | number;
  payload?: Record<string, unknown>;
}

/**
 * Recharts custom tooltip. Pass as `content={<ChartTooltip />}`, or with a
 * `rows` mapper when the payload needs reshaping (e.g. the value bridge, where the
 * transparent stacked base must not be shown as a series).
 */
export function ChartTooltip({
  active,
  payload,
  label,
  title,
  rows,
  footer,
}: {
  active?: boolean;
  payload?: TooltipRow[];
  label?: string | number;
  /** Overrides the default header, which is the axis label. */
  title?: (label: string | number | undefined, payload: TooltipRow[]) => ReactNode;
  /** Overrides the default one-row-per-series body. */
  rows?: (payload: TooltipRow[]) => ReactNode;
  footer?: (payload: TooltipRow[]) => ReactNode;
}) {
  if (!active || !payload?.length) return null;

  return (
    <div className="pointer-events-none min-w-36 max-w-72 rounded-lg border border-line-strong bg-surface/95 px-2.5 py-2 shadow-pop backdrop-blur-sm">
      <div className="mb-1.5 text-2xs font-medium uppercase tracking-[0.12em] text-ink-faint">
        {title ? title(label, payload) : label}
      </div>
      {rows ? (
        rows(payload)
      ) : (
        <div className="space-y-0.5">
          {payload.map((p, i) => (
            <div key={i} className="flex items-center justify-between gap-4 text-xs">
              <span className="flex items-center gap-1.5 text-ink-dim">
                <span
                  className="size-1.5 shrink-0 rounded-full"
                  style={{ background: p.color ?? theme.inkDim }}
                />
                {p.name}
              </span>
              <span className="text-numeric font-semibold text-ink">{p.value}</span>
            </div>
          ))}
        </div>
      )}
      {footer && (
        <div className="mt-1.5 border-t border-line pt-1.5 text-2xs leading-relaxed text-ink-faint">
          {footer(payload)}
        </div>
      )}
    </div>
  );
}

/** Swatch legend. Recharts' built-in legend cannot be styled to match. */
export function ChartLegend({
  items,
  className,
}: {
  items: { color: string; label: string; shape?: "dot" | "line" }[];
  className?: string;
}) {
  return (
    <div className={cn("mt-2 flex flex-wrap gap-x-4 gap-y-1", className)}>
      {items.map((i) => (
        <span key={i.label} className="flex items-center gap-1.5 text-2xs text-ink-faint">
          {i.shape === "line" ? (
            <span className="h-0.5 w-3 rounded-full" style={{ background: i.color }} />
          ) : (
            <span className="size-2 rounded-full" style={{ background: i.color }} />
          )}
          {i.label}
        </span>
      ))}
    </div>
  );
}
