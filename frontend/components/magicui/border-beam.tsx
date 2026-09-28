"use client";

// Magic UI `border-beam` (magicui.design/r/border-beam.json), adapted (ADR-075): cn from
// @/lib/cn; defaults are BMW blue instead of orange->violet (the system has one action
// colour); the travel path has SQUARE corners (`round 0px`) to match 0px panels; and it
// renders nothing under reduced motion -- a beam that cannot move is just a smudge.

import type { CSSProperties } from "react";
import { motion, useReducedMotion, type MotionStyle } from "motion/react";
import { cn } from "@/lib/cn";

interface BorderBeamProps {
  /** Length of the travelling light, px. */
  size?: number;
  /** Seconds per lap. */
  duration?: number;
  delay?: number;
  colorFrom?: string;
  colorTo?: string;
  className?: string;
  borderWidth?: number;
}

export function BorderBeam({
  className,
  size = 80,
  delay = 0,
  duration = 8,
  colorFrom = "var(--color-primary)",
  colorTo = "rgb(28 105 212 / 0.25)",
  borderWidth = 1,
}: BorderBeamProps) {
  const reduced = useReducedMotion();
  if (reduced) return null;
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0 border-(length:--border-beam-width) border-transparent mask-[linear-gradient(transparent,transparent),linear-gradient(#000,#000)] mask-intersect [mask-clip:padding-box,border-box]"
      style={{ "--border-beam-width": `${borderWidth}px` } as CSSProperties}
    >
      <motion.div
        className={cn("absolute aspect-square bg-linear-to-l from-(--color-from) via-(--color-to) to-transparent", className)}
        style={
          {
            width: size,
            offsetPath: "rect(0 auto auto 0 round 0px)",
            "--color-from": colorFrom,
            "--color-to": colorTo,
          } as MotionStyle
        }
        initial={{ offsetDistance: "0%" }}
        animate={{ offsetDistance: ["0%", "100%"] }}
        transition={{ repeat: Infinity, ease: "linear", duration, delay: -delay }}
      />
    </div>
  );
}
