"use client";

import { useId, useState, type CSSProperties } from "react";
import { motion, useMotionValue, useReducedMotion, useSpring, useTransform } from "motion/react";
import styles from "./VehicleHero.module.css";

const DETAILS = [
  {
    id: "quality",
    label: "Quality",
    title: "Precision, made visible.",
    description: "Connect station signals to quality risk before a vehicle leaves the line.",
    x: "34%",
    y: "66%",
    path: "M340 660 L250 550 L250 420 L430 420",
  },
  {
    id: "assembly",
    label: "Assembly",
    title: "Every station. One story.",
    description: "Follow production flow and see where constraints move across the line.",
    x: "63%",
    y: "78%",
    path: "M630 780 L770 850 L910 850 L910 420 L590 420",
  },
  {
    id: "traceability",
    label: "Traceability",
    title: "Context behind every alert.",
    description: "Trace alerts to their context. Keep measured and inferred evidence distinct.",
    x: "76%",
    y: "65%",
    path: "M760 650 L840 540 L840 420 L590 420",
  },
] as const;

/** An illustrative product introduction; the markers never imply live telemetry. */
export function VehicleHero() {
  const [active, setActive] = useState(0);
  const reduced = useReducedMotion();
  const detailId = useId();
  const pointerX = useMotionValue(0.5);
  const pointerY = useMotionValue(0.5);
  const spring = { stiffness: 130, damping: 25, mass: 0.4 };
  const x = useSpring(useTransform(pointerX, [0, 1], [-4, 4]), spring);
  const y = useSpring(useTransform(pointerY, [0, 1], [-3, 3]), spring);
  const detail = DETAILS[active];

  return (
    <figure className={styles.figure} aria-label="Explore the connected vehicle">
      <div className={styles.topline}>
        <span className={styles.kicker}><span aria-hidden="true" />Vehicle intelligence</span>
        <span className={styles.concept}>Concept view</span>
      </div>
      <div
        className={styles.photo}
        onPointerMove={(event) => {
          if (reduced || event.pointerType !== "mouse") return;
          const bounds = event.currentTarget.getBoundingClientRect();
          pointerX.set(Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)));
          pointerY.set(Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)));
        }}
        onPointerLeave={() => {
          pointerX.set(0.5);
          pointerY.set(0.5);
        }}
      >
        <motion.div className={styles.parallax} style={{ x: reduced ? 0 : x, y: reduced ? 0 : y }}>
          <div className={styles.scene}>
            <TwinCar className={styles.image} />

            <svg className={styles.connections} viewBox="0 0 1000 1000" fill="none" aria-hidden="true">
              <path className={styles.frame} pathLength={1} d="M105 535 V490 H155 M850 490 H900 V535 M900 825 V870 H850 M155 870 H105 V825" />
              {DETAILS.map((item, index) => (
                <path
                  key={item.id}
                  d={item.path}
                  className={styles.connection}
                  data-active={active === index}
                  pathLength={1}
                />
              ))}
              <path className={styles.crosshair} d="M500 395 V445 M475 420 H525" />
            </svg>

            {DETAILS.map((item, index) => (
              <button
                key={item.id}
                type="button"
                className={styles.hotspot}
                style={{ "--point-x": item.x, "--point-y": item.y } as CSSProperties}
                data-active={active === index}
                aria-label={`Explore ${item.label.toLowerCase()}`}
                aria-pressed={active === index}
                aria-controls={detailId}
                onPointerEnter={(event) => {
                  if (event.pointerType === "mouse") setActive(index);
                }}
                onFocus={() => setActive(index)}
                onClick={() => setActive(index)}
              >
                <span className={styles.hotspotCore} aria-hidden="true">+</span>
              </button>
            ))}
          </div>
        </motion.div>

        <div className={styles.shade} aria-hidden="true" />
        <div className={styles.scan} aria-hidden="true" />
      </div>

      <div className={styles.detail} id={detailId} aria-live="polite" aria-atomic="true">
        <span className={styles.detailIndex} aria-hidden="true">0{active + 1}</span>
        <div>
          <h2 className={styles.detailTitle}>{detail.title}</h2>
          <p className={styles.detailDescription}>{detail.description}</p>
        </div>
      </div>

      <div className={styles.controls} role="group" aria-label="Vehicle intelligence views">
        {DETAILS.map((item, index) => (
          <button
            key={item.id}
            type="button"
            className={styles.control}
            aria-pressed={active === index}
            aria-controls={detailId}
            onClick={() => setActive(index)}
          >
            <span className={styles.controlNumber}>0{index + 1}</span>
            <span>{item.label}</span>
            <span className={styles.controlArrow} aria-hidden="true">↗</span>
          </button>
        ))}
      </div>
      <figcaption className={styles.caption}>
        <span className={styles.captionMark} aria-hidden="true">+</span>
        Select a point to explore the twin
        <span className={styles.captionEnd}>An assembly perspective</span>
      </figcaption>
    </figure>
  );
}

// ---------------------------------------------------------------- the illustrated car
// One silhouette drives everything: the solid body, its floor reflection and the clip for
// the wireframe mesh. Coordinates share the hotspots' 1000x1000 square (ground at y=856).
const BODY =
  "M150 770 C138 760 134 745 138 732 C142 712 160 700 190 694 L370 660 " +
  "C410 630 440 600 480 588 C540 572 620 572 670 584 C720 598 770 625 805 640 " +
  "L862 652 C876 656 882 672 880 700 L874 762 C872 772 864 776 852 776 L797 776 " +
  "A77 77 0 0 0 643 776 L362 776 A77 77 0 0 0 208 776 L165 776 C158 776 153 774 150 770 Z";
const GLASS =
  "M402 664 C432 634 460 612 492 601 C548 588 612 588 658 598 " +
  "C698 607 732 624 758 641 C700 648 520 658 402 664 Z";
const BELT = "M196 698 C420 668 640 652 858 656";
const WHEELS = [285, 720] as const;
const SPLIT = 655; // solid to the left, digital twin to the right
const SPOKES = Array.from({ length: 10 }, (_, i) => i * 36);
const BLUE = "#1c69d4"; // --color-primary; SVG presentation attributes take hex

function Wheel({ cx, rim }: { cx: number; rim: string }) {
  return (
    <g transform={`translate(${cx} 790)`}>
      <circle r={66} fill="#0d1116" />
      <circle r={59} fill="none" stroke="#2b333d" strokeWidth={3} />
      <circle r={47} fill="#161c23" />
      {/* brake caliper: the one blue accent on the solid car */}
      <path d="M-30 -18 A35 35 0 0 1 12 -33" stroke={BLUE} strokeWidth={9} strokeLinecap="round" fill="none" />
      {SPOKES.map((a) => (
        <path key={a} d="M-2.5 -9 L-5 -45 L5 -45 L2.5 -9 Z" fill={rim} transform={`rotate(${a})`} />
      ))}
      <circle r={47} fill="none" stroke={rim} strokeWidth={3.5} />
      <circle r={10} fill={rim} />
      <circle r={4} fill="#0d1116" />
    </g>
  );
}

/** A concept car drawn in SVG, half physical and half digital twin. No photo, no brand. */
function TwinCar({ className }: { className?: string }) {
  const id = useId().replace(/[^a-zA-Z0-9-]/g, "");
  const u = (name: string) => `${id}-${name}`;
  const url = (name: string) => `url(#${u(name)})`;

  return (
    <svg
      className={className}
      viewBox="0 0 1000 1000"
      role="img"
      aria-label="Concept car, half solid and half digital-twin wireframe"
    >
      <defs>
        <linearGradient id={u("paint")} x1="0" y1="560" x2="0" y2="780" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#5b6878" />
          <stop offset="0.28" stopColor="#2c3642" />
          <stop offset="0.62" stopColor="#1a2129" />
          <stop offset="1" stopColor="#0f141a" />
        </linearGradient>
        <linearGradient id={u("sheen")} x1="140" y1="0" x2="880" y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#fff" stopOpacity="0" />
          <stop offset="0.35" stopColor="#fff" stopOpacity="0.22" />
          <stop offset="0.5" stopColor="#fff" stopOpacity="0.05" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
        <linearGradient id={u("glass")} x1="0" y1="585" x2="0" y2="665" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#3d5f8c" />
          <stop offset="0.5" stopColor="#152131" />
          <stop offset="1" stopColor="#0b1119" />
        </linearGradient>
        <linearGradient id={u("rim")} x1="0" y1="-47" x2="0" y2="47" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#e9edf2" />
          <stop offset="0.5" stopColor="#9aa4b1" />
          <stop offset="1" stopColor="#5d6773" />
        </linearGradient>
        <radialGradient id={u("glow")} cx="520" cy="700" r="420" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor={BLUE} stopOpacity="0.16" />
          <stop offset="1" stopColor={BLUE} stopOpacity="0" />
        </radialGradient>
        <radialGradient id={u("shadow")}>
          <stop offset="0" stopColor="#0b0f14" stopOpacity="0.55" />
          <stop offset="1" stopColor="#0b0f14" stopOpacity="0" />
        </radialGradient>
        <linearGradient id={u("scanband")} x1={SPLIT} y1="0" x2={SPLIT + 60} y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor={BLUE} stopOpacity="0.28" />
          <stop offset="1" stopColor={BLUE} stopOpacity="0" />
        </linearGradient>
        <linearGradient id={u("fade")} x1="0" y1="856" x2="0" y2="990" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#fff" stopOpacity="1" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
        <mask id={u("floor")}>
          <rect x="0" y="856" width="1000" height="144" fill={url("fade")} />
        </mask>
        <filter id={u("bloom")} x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="4" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <clipPath id={u("solidSide")}>
          <rect x="0" y="0" width={SPLIT} height="1000" />
        </clipPath>
        <clipPath id={u("twinSide")}>
          <rect x={SPLIT} y="0" width={1000 - SPLIT} height="1000" />
        </clipPath>
        <clipPath id={u("bodyClip")}>
          <path d={BODY} />
        </clipPath>

        <g id={u("car")}>
          <path d={BODY} fill={url("paint")} />
          <path d={BODY} fill={url("sheen")} />
          <path d={GLASS} fill={url("glass")} />
          <path d="M430 612 C520 596 600 594 650 600" stroke="#fff" strokeOpacity="0.35" strokeWidth="2" fill="none" />
          <path d="M586 590 L581 652" stroke="#1a2129" strokeWidth="7" />
          <path d={BELT} stroke="#fff" strokeOpacity="0.4" strokeWidth="1.5" fill="none" />
          <path d="M215 732 C400 716 650 712 872 716" stroke="#000" strokeOpacity="0.35" strokeWidth="1.5" fill="none" />
          <path d="M432 664 L442 772 M602 654 L610 772" stroke="#000" strokeOpacity="0.45" strokeWidth="1.2" />
          <rect x="505" y="686" width="30" height="5" fill="#8d97a3" />
          <rect x="660" y="680" width="28" height="5" fill="#8d97a3" />
          <path d="M404 654 L430 640 L442 649 L418 664 Z" fill="#11171e" />
          <path d="M150 770 L360 770 M800 770 L862 770" stroke="#07090c" strokeWidth="6" />
          {WHEELS.map((cx) => <Wheel key={cx} cx={cx} rim={url("rim")} />)}
        </g>
      </defs>

      {/* stage: ambient glow, perspective floor grid, horizon */}
      <ellipse cx="520" cy="700" rx="460" ry="300" fill={url("glow")} />
      <g mask={url("floor")} stroke={BLUE} strokeOpacity="0.22" strokeWidth="1">
        {Array.from({ length: 15 }, (_, i) => -200 + i * 100).map((x) => (
          <line key={x} x1={500 + (x - 500) * 0.35} y1="856" x2={x} y2="1000" />
        ))}
        {[870, 892, 922, 962].map((y) => <line key={y} x1="0" y1={y} x2="1000" y2={y} />)}
      </g>
      <line x1="60" y1="856" x2="940" y2="856" stroke={BLUE} strokeOpacity="0.35" />

      {/* floor reflection, then contact shadow */}
      <g mask={url("floor")} opacity="0.22">
        <use href={`#${u("car")}`} transform="translate(0 1712) scale(1 -1)" />
      </g>
      <ellipse cx="505" cy="858" rx="410" ry="26" fill={url("shadow")} />

      {/* the physical car */}
      <g clipPath={url("solidSide")}>
        <use href={`#${u("car")}`} />
        <path d="M150 724 C170 716 196 708 222 704" stroke="#dbeafe" strokeWidth="4" strokeLinecap="round" fill="none" filter={url("bloom")} />
        <path d="M146 746 L182 741" stroke={BLUE} strokeWidth="3" strokeLinecap="round" filter={url("bloom")} />
      </g>

      {/* its digital twin */}
      <g clipPath={url("twinSide")} className={styles.twin}>
        <path d={BODY} fill={BLUE} fillOpacity="0.06" stroke={BLUE} strokeWidth="1.6" />
        <g clipPath={url("bodyClip")} stroke={BLUE} strokeOpacity="0.45" strokeWidth="1" fill="none">
          {[0, 24, 48, 72, 96].map((dy) => <path key={dy} d={BELT} transform={`translate(0 ${dy - 70})`} />)}
          {Array.from({ length: 8 }, (_, i) => SPLIT + 10 + i * 30).map((x) => (
            <line key={x} x1={x} y1="560" x2={x} y2="800" />
          ))}
        </g>
        <path d={GLASS} fill={BLUE} fillOpacity="0.1" stroke={BLUE} strokeWidth="1.2" />
        <g transform="translate(720 790)" stroke={BLUE} fill="none">
          <circle r="66" strokeWidth="1.6" />
          <circle r="47" strokeWidth="1" />
          <circle r="10" strokeWidth="1" />
          {SPOKES.map((a) => <line key={a} x1="0" y1="-10" x2="0" y2="-47" strokeWidth="1" transform={`rotate(${a})`} />)}
        </g>
        <path d="M872 664 L878 704" stroke={BLUE} strokeWidth="4" strokeLinecap="round" filter={url("bloom")} />
        {[[700, 600], [805, 640], [862, 652], [880, 700], [797, 776], [643, 776]].map(([x, y]) => (
          <rect key={`${x}-${y}`} x={x - 3} y={y - 3} width="6" height="6" fill="#fff" stroke={BLUE} strokeWidth="1.2" />
        ))}
      </g>

      {/* scan boundary between the two */}
      <rect x={SPLIT} y="548" width="60" height="312" fill={url("scanband")} className={styles.twin} />
      <line x1={SPLIT} y1="540" x2={SPLIT} y2="872" stroke={BLUE} strokeWidth="2" filter={url("bloom")} className={styles.scanLine} />
      <text x={SPLIT + 10} y="534" fill={BLUE} fontSize="13" fontFamily="var(--font-mono)" letterSpacing="1.5">
        TWIN · SYNCED
      </text>
    </svg>
  );
}
