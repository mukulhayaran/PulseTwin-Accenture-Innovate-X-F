import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

// Two figure primitives, previously duplicated as local `Kpi` in app/manager and
// app/leadership and as local `Stat` in app/supervisor.
//
//   <Kpi>  — a bordered tile in the headline row
//   <Stat> — an unbordered figure inside a panel body

type Tone = "default" | "ok" | "warn" | "danger" | "info" | "ai";

const TONES: Record<Tone, string> = {
  default: "text-ink",
  ok: "text-ok-fg",
  warn: "text-warn-fg",
  danger: "text-danger-fg",
  info: "text-info-fg",
  ai: "text-ai-fg",
};

const LABEL = "text-2xs font-medium uppercase tracking-[0.14em] text-ink-faint";

export function Kpi({
  label,
  value,
  sub,
  tone = "default",
  accent,
  className,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: Tone;
  /** Left accent rule — marks the one tile that carries the argument. */
  accent?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "panel-surface relative min-w-0 overflow-hidden px-4 py-3",
        "transition-colors duration-200 hover:border-line-strong",
        className
      )}
    >
      {accent && (
        <span
          aria-hidden
          className={cn("absolute inset-y-0 left-0 w-0.5", tone === "default" ? "bg-info" : "bg-current", TONES[tone])}
        />
      )}
      <div className={LABEL}>{label}</div>
      <div className={cn("text-numeric mt-1 truncate text-2xl font-semibold", TONES[tone])}>
        {value}
      </div>
      {sub && <div className="mt-0.5 truncate text-2xs text-ink-faint">{sub}</div>}
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
      <div className={cn("text-numeric mt-0.5 text-lg font-semibold", TONES[tone])}>{value}</div>
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
    <div className={cn("min-w-0", accent && "border-l-2 border-ai pl-2.5")}>
      <div className="text-2xs text-ink-faint">{label}</div>
      <div className={cn("text-numeric text-lg font-semibold", TONES[tone])}>{value}</div>
      {note && <div className="text-2xs leading-tight text-ink-faint">{note}</div>}
    </div>
  );
}
