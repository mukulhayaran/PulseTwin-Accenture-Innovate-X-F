import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

// BMW `filter-chip` geometry (ADR-047): square, hairline, uppercase, weight 700.
// Tints are 8% rather than the dark build's 10% -- the same alpha over white makes
// a noticeably heavier block than it did over #111419.

type Variant = "neutral" | "solid" | "ok" | "warn" | "danger" | "info" | "ai";

const VARIANTS: Record<Variant, string> = {
  neutral: "border-line-strong bg-canvas text-ink-dim",
  // The `filter-chip-active` treatment: inverted to ink.
  solid: "border-ink bg-ink text-canvas",
  ok: "border-ok/40 bg-ok/8 text-ok-fg",
  warn: "border-warn/40 bg-warn/8 text-warn-fg",
  danger: "border-danger/40 bg-danger/8 text-danger-fg",
  info: "border-info/40 bg-info/8 text-info-fg",
  ai: "border-ai/40 bg-ai/8 text-ai-fg",
};

// Inside a dark band the tints invert: a 8% white-ground tint disappears, and the
// darkened `-fg` text goes muddy on navy.
const ON_DARK: Record<Variant, string> = {
  neutral: "border-line-on-dark bg-transparent text-on-dark-soft",
  solid: "border-on-dark bg-on-dark text-canvas",
  ok: "border-ok-on-dark/40 bg-ok-on-dark/15 text-ok-on-dark",
  warn: "border-warn-on-dark/40 bg-warn-on-dark/15 text-warn-on-dark",
  danger: "border-danger-on-dark/40 bg-danger-on-dark/15 text-danger-on-dark",
  info: "border-info-on-dark/40 bg-info-on-dark/15 text-info-on-dark",
  ai: "border-ai-on-dark/40 bg-ai-on-dark/15 text-ai-on-dark",
};

export default function Badge({
  children,
  variant = "neutral",
  dot = false,
  onDark = false,
  className,
}: {
  children: ReactNode;
  variant?: Variant;
  /** Leading status dot — use for live state, not for static labels. */
  dot?: boolean;
  /** Set when the badge sits inside a dark band. */
  onDark?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap border px-2 py-0.5",
        "text-2xs font-bold uppercase tracking-[0.1em]",
        (onDark ? ON_DARK : VARIANTS)[variant],
        className
      )}
    >
      {dot && <span className="size-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

// The provenance marker. CLAUDE.md §3: an inferred value may never render the same as
// a measured one. Pairs with the `hatch-inferred` utility on the surface behind it.
export function InferredBadge({ className }: { className?: string }) {
  return (
    <Badge variant="warn" className={cn("tracking-[0.115em]", className)}>
      Inferred
    </Badge>
  );
}
