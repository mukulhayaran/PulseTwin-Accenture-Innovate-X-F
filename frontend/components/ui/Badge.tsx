import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

type Variant = "neutral" | "ok" | "warn" | "danger" | "info" | "ai";

const VARIANTS: Record<Variant, string> = {
  neutral: "border-line-strong bg-surface-2 text-ink-dim",
  ok: "border-ok/30 bg-ok/10 text-ok-fg",
  warn: "border-warn/30 bg-warn/10 text-warn-fg",
  danger: "border-danger/30 bg-danger/10 text-danger-fg",
  info: "border-info/30 bg-info/10 text-info-fg",
  ai: "border-ai/30 bg-ai/10 text-ai-fg",
};

export default function Badge({
  children,
  variant = "neutral",
  dot = false,
  className,
}: {
  children: ReactNode;
  variant?: Variant;
  /** Leading status dot — use for live state, not for static labels. */
  dot?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border px-1.5 py-0.5",
        "text-2xs font-medium uppercase tracking-wider",
        VARIANTS[variant],
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
    <Badge variant="warn" className={cn("tracking-[0.12em]", className)}>
      Inferred
    </Badge>
  );
}
