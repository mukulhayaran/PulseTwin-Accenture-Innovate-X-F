import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { theme } from "@/lib/theme";

// Two figure primitives, previously duplicated as local `Kpi` in app/manager and
// app/leadership and as local `Stat` in app/supervisor.
//
//   <Kpi>  — a bordered tile in the headline row
//   <Stat> — an unbordered figure inside a panel body
//
// `icon`, `unit`, `delta` and `spark` are additive (ADR-047): every existing call
// site passes label/value/sub only and renders exactly as before.

type Tone = "default" | "ok" | "warn" | "danger" | "info" | "ai";

const TONES: Record<Tone, string> = {
  default: "text-ink",
  ok: "text-ok-fg",
  warn: "text-warn-fg",
  danger: "text-danger-fg",
  info: "text-info-fg",
  ai: "text-ai-fg",
};

const MARKS: Record<Tone, string> = {
  default: theme.inkFaint,
  ok: theme.ok,
  warn: theme.warn,
  danger: theme.danger,
  info: theme.info,
  ai: theme.ai,
};

const LABEL = "text-2xs font-bold uppercase tracking-[0.115em] text-ink-faint";

export interface Delta {
  /** Already formatted — "+6%", "-2.1 pp". The caller owns the unit. */
  value: string;
  direction: "up" | "down" | "flat";
  /** Which way is good. An "up" throughput is ok; an "up" defect rate is not. */
  tone?: Extract<Tone, "ok" | "danger" | "warn" | "default">;
}

/**
 * A bare inline sparkline. Deliberately not Recharts: a 22-point trend line inside a
 * KPI tile does not need an axis, a tooltip, a responsive container or a 40 KB
 * dependency on the critical path, and five of these per row made the headline row
 * the slowest thing on the page.
 */
function Sparkline({ points, stroke }: { points: number[]; stroke: string }) {
  if (points.length < 2) return null;
  const w = 72;
  const h = 26;
  const min = Math.min(...points);
  const max = Math.max(...points);
  // A flat series would divide by zero and collapse the line onto y=0; draw it
  // through the middle instead, which is what "no change" should look like.
  const span = max - min || 1;
  const d = points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * w;
      const y = h - ((p - min) / span) * (h - 2) - 1;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      aria-hidden
      className="shrink-0 overflow-visible"
    >
      <path d={d} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

const ARROW: Record<Delta["direction"], string> = { up: "▲", down: "▼", flat: "—" };

function DeltaChip({ delta }: { delta: Delta }) {
  return (
    <span
      className={cn(
        "text-numeric inline-flex items-center gap-1 text-2xs font-bold",
        TONES[delta.tone ?? "default"]
      )}
    >
      <span aria-hidden className="text-[0.6em] leading-none">
        {ARROW[delta.direction]}
      </span>
      {delta.value}
    </span>
  );
}

export function Kpi({
  label,
  value,
  unit,
  sub,
  icon,
  delta,
  spark,
  tone = "default",
  accent,
  className,
}: {
  label: string;
  value: ReactNode;
  /** Rendered after the figure at body weight — "units/hr", "%". */
  unit?: ReactNode;
  sub?: ReactNode;
  /** A small mark before the label. Keep it monochrome; tone carries the signal. */
  icon?: ReactNode;
  delta?: Delta;
  /** Trend behind the figure. Values only — no axis, no scale, no claim. */
  spark?: number[];
  tone?: Tone;
  /** Left accent rule — marks the one tile that carries the argument. */
  accent?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "panel-surface relative min-w-0 overflow-hidden px-5 py-4",
        // A near-invisible top-down wash; the hover is a hairline change, never a
        // lifted card (ADR-047: cards carry no shadow).
        "accent-draw bg-[linear-gradient(180deg,#ffffff,#f9fbfe)]",
        "transition-[border-color,box-shadow,transform] duration-200 hover:border-primary/25 motion-safe:hover:-translate-y-0.5",
        className
      )}
    >
      {accent && (
        <span
          aria-hidden
          className={cn(
            "absolute inset-y-0 left-0 w-1",
            tone === "default" ? "bg-primary" : "bg-current",
            TONES[tone]
          )}
        />
      )}

      <div className={cn(LABEL, "flex items-center gap-2")}>
        {icon && (
          <span className={cn("shrink-0", tone === "default" ? "text-ink-faint" : TONES[tone])}>
            {icon}
          </span>
        )}
        <span className="truncate">{label}</span>
      </div>

      <div className="mt-2 flex items-end justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-baseline gap-1.5">
            <span className={cn("text-numeric value-in truncate text-lg font-bold sm:text-2xl", TONES[tone])}>
              {value}
            </span>
            {unit && <span className="shrink-0 text-xs text-ink-faint">{unit}</span>}
          </div>
          {delta && (
            <div className="mt-1">
              <DeltaChip delta={delta} />
            </div>
          )}
        </div>
        {spark && spark.length > 1 && <Sparkline points={spark} stroke={MARKS[tone]} />}
      </div>

      {sub && <div className="mt-2 truncate text-2xs text-ink-faint">{sub}</div>}
    </div>
  );
}

export function Stat({
  label,
  value,
  sub,
  tone = "default",
  className,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: Tone;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <div className={LABEL}>{label}</div>
      <div className={cn("text-numeric mt-1 text-lg font-bold", TONES[tone])}>{value}</div>
      {sub && <div className="text-2xs leading-tight text-ink-faint">{sub}</div>}
    </div>
  );
}

// A label/value pair on one line, for dense fact grids.
export function Fact({
  label,
  value,
  note,
  tone = "default",
  accent,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  tone?: Tone;
  accent?: boolean;
}) {
  return (
    <div className={cn("min-w-0", accent && "border-l-2 border-ai pl-3")}>
      <div className="text-2xs text-ink-faint">{label}</div>
      <div className={cn("text-numeric text-lg font-bold", TONES[tone])}>{value}</div>
      {note && <div className="text-2xs leading-tight text-ink-faint">{note}</div>}
    </div>
  );
}
