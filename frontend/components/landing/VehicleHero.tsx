"use client";

import Image from "next/image";
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
            <Image
              src="/images/bmw-hero.webp"
              alt="Silver BMW travelling along a coastal road under a blue sky"
              width={1254}
              height={1254}
              priority
              sizes="(max-width: 599px) calc(100vw - 40px), 560px"
              className={styles.image}
            />

            <svg className={styles.connections} viewBox="0 0 1000 1000" fill="none" aria-hidden="true">
              <path className={styles.frame} d="M105 535 V490 H155 M850 490 H900 V535 M900 825 V870 H850 M155 870 H105 V825" />
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
