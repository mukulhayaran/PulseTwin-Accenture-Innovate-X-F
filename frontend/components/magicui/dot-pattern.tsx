"use client";

// Magic UI `dot-pattern` (magicui.design/r/dot-pattern.json), adapted (ADR-075). The
// upstream component renders one animated <motion.circle> per dot, re-measures on every
// resize and seeds Math.random() delays (a server/client mismatch in its glow mode). Used
// here only as a STATIC texture, so it is the pattern form: one <pattern>, no JS layout,
// no motion. Same props. Colour comes from the tokens via `text-*`.

import { useId, type SVGProps } from "react";
import { cn } from "@/lib/cn";

interface DotPatternProps extends SVGProps<SVGSVGElement> {
  width?: number;
  height?: number;
  x?: number;
  y?: number;
  cx?: number;
  cy?: number;
  cr?: number;
}

export function DotPattern({
  width = 16,
  height = 16,
  x = 0,
  y = 0,
  cx = 1,
  cy = 1,
  cr = 1,
  className,
  ...props
}: DotPatternProps) {
  const id = useId();
  return (
    <svg
      aria-hidden
      className={cn("pointer-events-none absolute inset-0 h-full w-full fill-line-strong", className)}
      {...props}
    >
      <defs>
        <pattern id={id} width={width} height={height} patternUnits="userSpaceOnUse" x={x} y={y}>
          <circle cx={cx} cy={cy} r={cr} />
        </pattern>
      </defs>
      <rect width="100%" height="100%" strokeWidth={0} fill={`url(#${id})`} />
    </svg>
  );
}
