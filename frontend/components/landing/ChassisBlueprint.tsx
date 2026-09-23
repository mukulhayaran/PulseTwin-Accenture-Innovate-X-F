"use client";

// The hero's one bold visual: a fastback EV profile, rendered as a single continuous
// outline rather than a gradient blob or a generic sedan silhouette. Real automotive
// geometry -- wheel arches actually cut into the rocker, a fastback C-pillar, a hood
// that meets the fascia at the right angle, a mirror -- carries more of the "digital
// twin" idea than any effect layered on top of it. One outline gets the gradient; every
// other line (glass, handle, mirror, rims) stays quiet so the silhouette itself reads
// as the confident thing, not a pile of details competing for attention. Once revealed,
// it stays quietly alive: an idle float and slowly turning wheels, like a unit idling
// on the line rather than a static diagram.

import { motion } from "motion/react";

const EASE = [0.2, 0.8, 0.25, 1] as const;

const GROUND_Y = 176;
// A wheel radius big enough to look modern but small enough to leave the body room for
// an actual arch above it. At r=33 (the first pass) the wheel top sat almost exactly at
// hood height, so there was no vertical space left for an arch and the "arch" curve
// punched straight through the hood line instead of clearing the wheel -- a real
// self-intersecting loop, not a rendering glitch. r=23 keeps wheel-diameter at ~38% of
// the car's total height, which is where real proportions sit.
const WHEEL_R = 23;
// A circle centered ON the ground line sinks its bottom half through it. The wheel
// center has to sit one radius above the datum for the tire to actually touch down.
const REAR_WHEEL = { cx: 166, cy: GROUND_Y - WHEEL_R };
const FRONT_WHEEL = { cx: 456, cy: GROUND_Y - WHEEL_R };

const SILHOUETTE =
  "M68,158" +
  " Q60,148 64,140" +
  " Q80,124 108,120" +
  " L152,117" +
  " C175,117 192,95 212,58" +
  " C255,46 305,45 344,55" +
  " C376,62 398,78 418,97" +
  " Q444,111 468,115" +
  " Q500,120 520,131" +
  " Q530,140 532,158" +
  " Q520,150 505,140" +
  " Q483,140 456,120" +
  " Q434,120 411,140" +
  " L216,140" +
  " Q191,140 166,120" +
  " Q141,120 121,140" +
  " Q105,140 90,150" +
  " Q78,155 68,158" +
  " Z";

function spokes({ cx, cy }: { cx: number; cy: number }) {
  return Array.from({ length: 5 }, (_, i) => {
    const a = ((i * 72 - 90) * Math.PI) / 180;
    return {
      x1: cx + 7 * Math.cos(a),
      y1: cy + 7 * Math.sin(a),
      x2: cx + 18 * Math.cos(a),
      y2: cy + 18 * Math.sin(a),
    };
  });
}

export function ChassisBlueprint({ reduced }: { reduced: boolean }) {
  const revealDuration = 1.3;

  return (
    <motion.svg
      viewBox="0 0 600 220"
      className="h-full w-full overflow-visible"
      fill="none"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      animate={reduced ? {} : { y: [0, -4, 0] }}
      transition={reduced ? {} : { duration: 4.5, delay: revealDuration + 0.2, repeat: Infinity, ease: "easeInOut" }}
    >
      <defs>
        <linearGradient id="chassisGrad" x1="0" y1="0.1" x2="1" y2="0.3">
          <stop offset="0%" style={{ stopColor: "var(--color-info)" }} />
          <stop offset="100%" style={{ stopColor: "var(--color-ai)" }} />
        </linearGradient>
        <radialGradient id="contactShadow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" style={{ stopColor: "var(--color-line-strong)", stopOpacity: 0.8 }} />
          <stop offset="100%" style={{ stopColor: "var(--color-line-strong)", stopOpacity: 0 }} />
        </radialGradient>
        <clipPath id="chassisReveal">
          <motion.rect
            x="0"
            y="0"
            height="220"
            initial={{ width: reduced ? 600 : 0 }}
            animate={{ width: 600 }}
            transition={reduced ? { duration: 0 } : { duration: revealDuration, ease: EASE }}
          />
        </clipPath>
      </defs>

      {/* ground datum -- the one blueprint holdover, kept quiet */}
      <motion.line
        x1="16" y1="176" x2="584" y2="176" strokeDasharray="2 6" opacity={0.3}
        className="text-ink-faint" stroke="currentColor" strokeWidth="1.2"
        initial={{ pathLength: reduced ? 1 : 0 }} animate={{ pathLength: 1 }}
        transition={reduced ? { duration: 0 } : { duration: 0.6, ease: EASE }}
      />
      <ellipse cx={REAR_WHEEL.cx} cy={GROUND_Y + 2} rx={40} ry={7} fill="url(#contactShadow)" />
      <ellipse cx={FRONT_WHEEL.cx} cy={GROUND_Y + 2} rx={40} ry={7} fill="url(#contactShadow)" />

      <g clipPath="url(#chassisReveal)">
        {/* wheels -- rim, hub, spokes, all quiet: the outline below carries the accent */}
        <g className="text-ink-dim" stroke="currentColor" strokeWidth="1.4">
          <circle cx={REAR_WHEEL.cx} cy={REAR_WHEEL.cy} r={WHEEL_R} />
          <circle cx={FRONT_WHEEL.cx} cy={FRONT_WHEEL.cy} r={WHEEL_R} />
          <circle cx={REAR_WHEEL.cx} cy={REAR_WHEEL.cy} r={6} />
          <circle cx={FRONT_WHEEL.cx} cy={FRONT_WHEEL.cy} r={6} />
        </g>
        {!reduced && (
          <>
            <g className="hub-spokes hub-spokes--rear text-ink-faint" stroke="currentColor" strokeWidth="1.2">
              {spokes(REAR_WHEEL).map((s, i) => (
                <line key={i} {...s} />
              ))}
            </g>
            <g className="hub-spokes hub-spokes--front text-ink-faint" stroke="currentColor" strokeWidth="1.2">
              {spokes(FRONT_WHEEL).map((s, i) => (
                <line key={i} {...s} />
              ))}
            </g>
          </>
        )}

        {/* the silhouette -- one closed outline, top deck to rocker to wheel arches and
            back, so nothing about the car reads as two shapes glued together */}
        <path d={SILHOUETTE} stroke="url(#chassisGrad)" strokeWidth="2.2" />

        {/* beltline + B-pillar -- the two-pane greenhouse */}
        <g className="text-ink-faint" stroke="currentColor" strokeWidth="1.2" opacity={0.6}>
          <path d="M155,118 Q285,110 415,100" />
          <line x1="286" y1="112" x2="284" y2="51" />
        </g>

        {/* door handle + mirror -- small specific details a generic hero skips */}
        <g className="text-ink-faint" stroke="currentColor" strokeWidth="1.3" opacity={0.7}>
          <line x1="253" y1="106" x2="276" y2="105" />
          <path d="M403 91 L418 83 L421 92 Z" fill="currentColor" stroke="none" opacity={0.55} />
        </g>

        {/* sensor node on the hood -- the one non-structural accent mark, the twin
            watching its own vehicle */}
        <motion.circle
          cx="446" cy="107" r="3.5"
          className="text-info-fg" fill="currentColor" stroke="none"
          initial={reduced ? { opacity: 1, scale: 1 } : { opacity: 0, scale: 0 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={reduced ? { duration: 0 } : { delay: revealDuration - 0.15, duration: 0.35, ease: EASE }}
        />
        <motion.circle
          cx="446" cy="107" r="3.5"
          className="text-info-fg" stroke="currentColor" strokeWidth="1" fill="none"
          initial={{ opacity: 0.6, scale: 1 }}
          animate={reduced ? { opacity: 0, scale: 1 } : { opacity: [0.6, 0], scale: [1, 3.4] }}
          transition={reduced ? { duration: 0 } : { delay: revealDuration + 0.1, duration: 1.6, repeat: Infinity, ease: "easeOut" }}
        />
      </g>

      {/* scan bar -- travels with the reveal clip, like the twin resolving the vehicle
          into being rather than a shape simply fading in */}
      {!reduced && (
        <motion.rect
          y="4" height="212" width="3" rx="1.5"
          fill="url(#chassisGrad)"
          initial={{ x: -3, opacity: 0.9 }}
          animate={{ x: 600, opacity: [0.9, 0.9, 0] }}
          transition={{ duration: revealDuration, ease: EASE, times: [0, 0.85, 1] }}
          style={{ filter: "drop-shadow(0 0 6px var(--color-info))" }}
        />
      )}
    </motion.svg>
  );
}
