"use client";

// Magic UI `text-animate` (magicui.design/r/text-animate.json), trimmed to what the
// landing uses (ADR-075): split by WORD, the `blurInUp` preset, animate once on mount.
// Changes from upstream: cn from @/lib/cn; the other 13 presets and by=character/line
// dropped (unused); reduced motion renders the plain text with no animation at all.

import { memo } from "react";
import { motion, useReducedMotion, type Variants } from "motion/react";
import { cn } from "@/lib/cn";

const item: Variants = {
  hidden: { opacity: 0, filter: "blur(10px)", y: 20 },
  show: {
    opacity: 1,
    filter: "blur(0px)",
    y: 0,
    transition: { y: { duration: 0.3 }, opacity: { duration: 0.4 }, filter: { duration: 0.3 } },
  },
};

function TextAnimateBase({
  children,
  className,
  segmentClassName,
  delay = 0,
  duration = 0.3,
}: {
  children: string;
  className?: string;
  segmentClassName?: string;
  /** Seconds before the first word starts. */
  delay?: number;
  /** Seconds the stagger is spread across, first word to last. */
  duration?: number;
}) {
  const reduced = useReducedMotion();
  if (reduced) return <span className={className}>{children}</span>;

  const segments = children.split(/(\s+)/);
  return (
    <motion.span
      className={cn("whitespace-pre-wrap", className)}
      initial="hidden"
      animate="show"
      variants={{
        hidden: { opacity: 1 },
        show: { opacity: 1, transition: { delayChildren: delay, staggerChildren: duration / segments.length } },
      }}
    >
      {/* Screen readers get the sentence once, not word by word. */}
      <span className="sr-only">{children}</span>
      {segments.map((segment, i) => (
        <motion.span
          key={`${segment}-${i}`}
          variants={item}
          aria-hidden
          className={cn("inline-block whitespace-pre", segmentClassName)}
        >
          {segment}
        </motion.span>
      ))}
    </motion.span>
  );
}

export const TextAnimate = memo(TextAnimateBase);
