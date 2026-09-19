"use client";

import { motion } from "motion/react";
import { cn } from "@/lib/cn";

// Replaces the loose button rows used for speed (1x/5x/10x/50x) and the leadership
// scenario switch. A shared layoutId slides the selection between options instead of
// repainting a border, which is the difference between "toggle" and "control".

export interface SegmentedControlProps<T extends string | number> {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  /** Distinguishes the sliding indicator per instance — must be unique on the page. */
  name: string;
  "aria-label"?: string;
  className?: string;
}

export default function SegmentedControl<T extends string | number>({
  value,
  options,
  onChange,
  name,
  className,
  ...rest
}: SegmentedControlProps<T>) {
  return (
    <div
      role="radiogroup"
      aria-label={rest["aria-label"]}
      className={cn(
        "inline-flex shrink-0 items-center gap-0.5 rounded-lg border border-line bg-surface p-0.5",
        className
      )}
    >
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={String(o.value)}
            role="radio"
            aria-checked={active}
            onClick={() => onChange(o.value)}
            className={cn(
              "relative rounded-md px-2.5 py-1 text-2xs font-medium transition-colors duration-150",
              active ? "text-ink" : "text-ink-faint hover:text-ink-dim"
            )}
          >
            {active && (
              <motion.span
                layoutId={`segmented-${name}`}
                className="absolute inset-0 rounded-md border border-line-strong bg-surface-3"
                transition={{ type: "spring", stiffness: 380, damping: 32 }}
              />
            )}
            <span className="relative z-10">{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}
