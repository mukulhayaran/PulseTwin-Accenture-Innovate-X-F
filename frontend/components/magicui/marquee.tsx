// Magic UI `marquee` (magicui.design/r/marquee.json), adapted (ADR-075): cn from
// @/lib/cn, horizontal only (vertical unused), the repeated copies are aria-hidden so a
// screen reader hears the list once, and under reduced motion ONE static copy wraps in
// place -- the global reduced-motion rule would otherwise freeze it mid-scroll.
// `animate-marquee` and its keyframes live in globals.css @theme.

import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { cn } from "@/lib/cn";

interface MarqueeProps extends ComponentPropsWithoutRef<"div"> {
  reverse?: boolean;
  pauseOnHover?: boolean;
  children: ReactNode;
  /** Copies laid end to end so the loop never shows a gap. */
  repeat?: number;
}

export function Marquee({
  className,
  reverse = false,
  pauseOnHover = false,
  children,
  repeat = 4,
  ...props
}: MarqueeProps) {
  return (
    <div
      {...props}
      className={cn("group flex flex-row gap-(--gap) overflow-hidden [--duration:40s] [--gap:1rem]", className)}
    >
      {Array.from({ length: repeat }, (_, i) => (
        <div
          key={i}
          aria-hidden={i > 0 || undefined}
          className={cn(
            "flex shrink-0 flex-row justify-around gap-(--gap) animate-marquee",
            "motion-reduce:animate-none motion-reduce:shrink motion-reduce:flex-wrap",
            i > 0 && "motion-reduce:hidden",
            pauseOnHover && "group-hover:[animation-play-state:paused]",
            reverse && "[animation-direction:reverse]"
          )}
        >
          {children}
        </div>
      ))}
    </div>
  );
}
