"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  motion,
  useMotionValue,
  useReducedMotion,
  useScroll,
  useSpring,
  useTransform,
} from "motion/react";
import { getHealth } from "@/lib/api";
import { cn } from "@/lib/cn";
import { AnimatedNumber, Badge, Skeleton } from "@/components/ui";
import { ChassisBlueprint } from "@/components/landing/ChassisBlueprint";

type Accent = "ok" | "info" | "ai" | "warn";

const STATIONS = [
  {
    id: "01",
    href: "/supervisor",
    title: "Supervisor",
    role: "Action",
    accent: "ok",
    body: "Replay or live-synthetic line state, defect alerts with risk drivers, and containment of units sharing production context.",
  },
  {
    id: "02",
    href: "/manager",
    title: "Manager",
    role: "Analysis",
    accent: "info",
    body: "Constraint migration over time, station league table, and the drivers behind the defect Pareto.",
  },
  {
    id: "03",
    href: "/leadership",
    title: "Leadership",
    role: "Investment",
    accent: "ai",
    body: "Value derived from measured recall, the alarm-budget trade-off, and where the next sensor dollar pays back.",
  },
  {
    id: "04",
    href: "/ops",
    title: "Operations",
    role: "System",
    accent: "warn",
    body: "Model health, drift detection, ingestion lag and audit trail — keeping the twin itself honest, not the line.",
  },
] as const satisfies readonly {
  id: string;
  href: string;
  title: string;
  role: string;
  accent: Accent;
  body: string;
}[];

const ACCENT_TEXT: Record<Accent, string> = {
  ok: "text-ok-fg",
  info: "text-info-fg",
  ai: "text-ai-fg",
  warn: "text-warn-fg",
};
const ACCENT_BG: Record<Accent, string> = {
  ok: "bg-ok",
  info: "bg-info",
  ai: "bg-ai",
  warn: "bg-warn",
};

const EASE = [0.2, 0.8, 0.25, 1] as const;

export default function Landing() {
  const [health, setHealth] = useState<any>(null);
  const [err, setErr] = useState(false);
  const reduced = useReducedMotion();
  const lineRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setErr(true));
  }, []);

  const { scrollYProgress } = useScroll({
    target: lineRef,
    offset: ["start end", "end start"],
  });
  const spineHeight = useTransform(scrollYProgress, [0.08, 0.92], ["0%", "100%"]);

  // Mouse-parallax tilt on the chassis graphic — a light, premium touch, not a
  // gimmick: it re-centers on pointer leave and never engages under reduced motion.
  const mx = useMotionValue(0.5);
  const my = useMotionValue(0.5);
  const springConfig = { stiffness: 150, damping: 20, mass: 0.5 };
  const chassisRotateX = useSpring(useTransform(my, [0, 1], [8, -8]), springConfig);
  const chassisRotateY = useSpring(useTransform(mx, [0, 1], [-10, 10]), springConfig);

  function handleChassisPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    if (reduced) return;
    const rect = e.currentTarget.getBoundingClientRect();
    mx.set((e.clientX - rect.left) / rect.width);
    my.set((e.clientY - rect.top) / rect.height);
  }
  function handleChassisPointerLeave() {
    mx.set(0.5);
    my.set(0.5);
  }

  return (
    <main className="relative w-full">
      {/* ============================================================ hero */}
      <section className="relative mx-auto w-full max-w-6xl px-5 pt-8 sm:px-8 sm:pt-10">
        {/* A drafting-sheet grid, not a gradient wash — the hero's only ambient texture. */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-[560px] opacity-[0.35]"
          style={{
            backgroundImage:
              "linear-gradient(var(--color-line) 1px, transparent 1px), linear-gradient(90deg, var(--color-line) 1px, transparent 1px)",
            backgroundSize: "34px 34px",
            maskImage: "radial-gradient(70% 60% at 28% 15%, #000 35%, transparent 100%)",
          }}
        />

        <div className="relative mb-14 flex items-center justify-between gap-4 sm:mb-20">
          <span className="text-xs font-semibold tracking-[0.2em] text-info-fg">PULSETWIN</span>
          <Link
            href="/login"
            className="rounded-md px-2 py-1 text-2xs font-medium text-ink-faint transition-colors hover:bg-surface-2 hover:text-ink"
          >
            Login / Role
          </Link>
        </div>

        <div className="relative grid min-h-[55vh] gap-12 py-6 lg:grid-cols-[1.1fr_0.9fr] lg:items-center">
          <motion.div
            initial={reduced ? false : { opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease: EASE }}
          >
            <h1 className="max-w-xl text-3xl font-semibold leading-[1.12] tracking-tight text-ink sm:text-[2.75rem] sm:leading-[1.08]">
              The line never fails in one place.
              <span className="mt-1 block text-ink-faint">It fails in the space between stations.</span>
            </h1>

            <p className="mt-6 max-w-lg text-base leading-relaxed text-ink-dim">
              PulseTwin is a digital twin for the assembly line — a second set of eyes
              that watches every station at once, catching what no single sensor,
              checklist, or inspector could ever catch alone.
            </p>

            <div className="mt-9 flex flex-wrap items-center gap-5">
              <a
                href="#the-line"
                className="inline-flex items-center gap-2 rounded-lg border border-line-strong bg-surface-2 px-4 py-2.5 text-sm font-medium text-ink transition-colors duration-200 ease-soft hover:border-info/40 hover:bg-surface-3"
              >
                See the line
                <motion.svg
                  width="12"
                  height="12"
                  viewBox="0 0 12 12"
                  aria-hidden
                  animate={reduced ? {} : { y: [0, 3, 0] }}
                  transition={reduced ? {} : { duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
                >
                  <path
                    d="M6 2v8M2.5 6.5L6 10l3.5-3.5"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.4"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </motion.svg>
              </a>
              <span className="text-xs text-ink-faint">four roles, one twin</span>
            </div>
          </motion.div>

          <motion.div
            initial={reduced ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.6, delay: 0.15 }}
            onPointerMove={handleChassisPointerMove}
            onPointerLeave={handleChassisPointerLeave}
            style={{ rotateX: chassisRotateX, rotateY: chassisRotateY, transformPerspective: 700 }}
            className="relative aspect-[3/1.5] w-full lg:aspect-[3/1.9]"
          >
            <ChassisBlueprint reduced={!!reduced} />
          </motion.div>
        </div>
      </section>

      {/* ============================================================ the line */}
      <section
        id="the-line"
        ref={lineRef}
        className="relative mx-auto w-full max-w-4xl scroll-mt-8 px-5 py-20 sm:px-8 sm:py-28"
      >
        <h2 className="mb-1 text-xs font-semibold uppercase tracking-[0.16em] text-ink-faint">The line</h2>
        <p className="mb-16 max-w-md text-sm text-ink-dim">
          Four roles, one twin. Scroll down it the way a unit moves down the floor.
        </p>

        <div className="relative">
          <div className="absolute left-[7px] top-1 bottom-1 w-px overflow-hidden rounded-full bg-line sm:left-[9px]">
            <motion.div aria-hidden className="absolute inset-x-0 top-0 bg-info" style={{ height: spineHeight }}>
              {!reduced && (
                <motion.div
                  aria-hidden
                  className="absolute left-1/2 h-10 w-1.5 -translate-x-1/2 rounded-full bg-info-fg"
                  style={{ boxShadow: "0 0 14px 3px var(--color-info)" }}
                  animate={{ top: ["-12%", "112%"], opacity: [0, 1, 1, 0] }}
                  transition={{ duration: 2.4, repeat: Infinity, ease: "linear", times: [0, 0.15, 0.85, 1] }}
                />
              )}
            </motion.div>
          </div>

          <ol className="relative flex flex-col gap-14">
            {STATIONS.map((s) => (
              <Station key={s.id} station={s} />
            ))}
          </ol>
        </div>
      </section>

      {/* ============================================================ footer telemetry */}
      <footer className="relative mx-auto flex w-full max-w-4xl flex-wrap items-center gap-x-5 gap-y-2 border-t border-line px-5 py-8 text-2xs text-ink-faint sm:px-8">
        {err && (
          <Badge variant="danger" dot>
            API unreachable — start the backend on :7860
          </Badge>
        )}

        {!err && !health && (
          <>
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-4 w-36" />
            <Skeleton className="h-4 w-44" />
          </>
        )}

        {health && (
          <>
            <Badge variant="ok" dot>
              API {health.status}
            </Badge>
            <Meta k="plants" v={health.plants.join(", ")} />
            <span className="flex items-center gap-1.5">
              <span className="text-ink-faint">stations</span>
              <span className="flex items-center gap-3">
                {Object.entries(health.station_counts).map(([plant, count]) => (
                  <span key={plant} className="text-numeric text-ink-dim">
                    {plant}=
                    <AnimatedNumber value={Number(count)} format={(n) => String(Math.round(n))} />
                  </span>
                ))}
              </span>
            </span>
            <Meta k="database" v={health.database} />
            <Meta
              k="artifact built"
              v={String(health.artifact_built_at).replace("T", " ").slice(0, 16)}
            />
          </>
        )}
      </footer>
    </main>
  );
}

function Station({ station }: { station: (typeof STATIONS)[number] }) {
  const a = station.accent;

  return (
    <li className="relative pl-9 sm:pl-11">
      <span className="absolute left-0 top-1.5 flex size-[17px] items-center justify-center rounded-full border-2 border-line bg-canvas sm:size-[19px]">
        <span className={cn("size-2 rounded-full", ACCENT_BG[a])} />
      </span>

      <Link
        href={station.href}
        className={cn(
          "group flex flex-col gap-2.5 rounded-xl border border-line bg-surface p-5",
          "transition-[border-color,background-color,transform] duration-200 ease-soft",
          "hover:-translate-y-0.5 hover:border-line-strong hover:bg-surface-2"
        )}
      >
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-baseline gap-2.5">
            <span className="text-numeric text-xs text-ink-faint">{station.id}</span>
            <h3 className="text-base font-semibold text-ink">{station.title}</h3>
          </div>
          <Badge variant={a}>{station.role}</Badge>
        </div>

        <p className="max-w-xl text-sm leading-relaxed text-ink-dim">{station.body}</p>

        <span
          className={cn(
            "mt-1 inline-flex items-center gap-1 text-xs font-medium transition-transform duration-300 group-hover:translate-x-0.5",
            ACCENT_TEXT[a]
          )}
        >
          Enter
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
            <path
              d="M2 6h8M6.5 2.5L10 6l-3.5 3.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
      </Link>
    </li>
  );
}

function Meta({ k, v }: { k: string; v: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="text-ink-faint">{k}</span>
      <span className="text-numeric text-ink-dim">{v}</span>
    </span>
  );
}
