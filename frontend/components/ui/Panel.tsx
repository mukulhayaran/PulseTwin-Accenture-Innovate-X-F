import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

// The single panel shell. Before this existed there were EIGHT local `function Panel`
// definitions (app/leadership, app/manager, and six components/ops/*), each slightly
// different, plus the raw string "bg-bg-card border border-bg-surface rounded p-3"
// pasted in 13 places. A header rule and real padding separate chrome from content,
// which is most of what makes the dense body read as deliberate rather than cramped.
//
// BMW (ADR-047): white plate, 1px hairline, square corners, NO shadow -- depth is
// colour-block contrast, never elevation. Body padding is 20px: BMW specifies 24px
// for an editorial model card, but these are dense dashboard panels (its own
// "configurator inventory" density tier), so this takes the step below on the same
// 8px scale rather than inventing a value off it.

type Tone = "default" | "warn" | "danger" | "ok" | "info" | "ai";

const TONE_RING: Record<Tone, string> = {
  default: "",
  warn: "border-warn/45",
  danger: "border-danger/45",
  ok: "border-ok/45",
  info: "border-info/45",
  ai: "border-ai/45",
};

const TONE_TITLE: Record<Tone, string> = {
  default: "text-ink-faint",
  warn: "text-warn-fg",
  danger: "text-danger-fg",
  ok: "text-ok-fg",
  info: "text-info-fg",
  ai: "text-ai-fg",
};

export interface PanelProps {
  title?: ReactNode;
  /** Right-aligned method note. Says how a number was derived, not what it means. */
  note?: ReactNode;
  /** Controls, rendered after the note. */
  actions?: ReactNode;
  tone?: Tone;
  children: ReactNode;
  className?: string;
  /** Charts want their own padding; pass "p-0" or "p-2". */
  bodyClassName?: string;
}

export default function Panel({
  title,
  note,
  actions,
  tone = "default",
  children,
  className,
  bodyClassName,
}: PanelProps) {
  const hasHeader = Boolean(title || note || actions);

  return (
    <section className={cn("panel-surface flex min-w-0 flex-col hover:border-[rgb(15_23_42/0.14)]", TONE_RING[tone], className)}>
      {hasHeader && (
        <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-line bg-[linear-gradient(180deg,#fbfcfe,#ffffff)] px-5 py-3">
          {title && (
            <h2
              className={cn(
                "text-xs font-bold uppercase tracking-[0.115em]",
                TONE_TITLE[tone]
              )}
            >
              {title}
            </h2>
          )}
          <div className="flex items-baseline gap-3">
            {note && (
              <span className="text-2xs leading-snug text-ink-faint text-right">{note}</span>
            )}
            {actions}
          </div>
        </header>
      )}
      <div className={cn("min-w-0 flex-1 p-5", bodyClassName)}>{children}</div>
    </section>
  );
}
