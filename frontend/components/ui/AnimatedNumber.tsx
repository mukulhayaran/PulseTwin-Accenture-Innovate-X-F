"use client";

import { useEffect } from "react";
import { motion, useMotionValue, useReducedMotion, useSpring, useTransform } from "motion/react";

// Spring-transitions a figure to its new value instead of snapping. Used on KPI tiles,
// where a jump-cut reads as a glitch rather than as news.
//
// The formatter runs on every animation frame, so it must stay cheap -- no Intl
// object construction inside it.
export default function AnimatedNumber({
  value,
  format,
  className,
}: {
  value: number;
  format: (v: number) => string;
  className?: string;
}) {
  const reduced = useReducedMotion();
  const raw = useMotionValue(value);
  const spring = useSpring(raw, { stiffness: 140, damping: 22, mass: 0.6 });
  const text = useTransform(reduced ? raw : spring, format);

  useEffect(() => {
    raw.set(value);
  }, [value, raw]);

  return <motion.span className={className}>{text}</motion.span>;
}
