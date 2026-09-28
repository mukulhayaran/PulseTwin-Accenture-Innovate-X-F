"use client";

// The landing band stack (ADR-047).
//
// BMW's page rhythm rule is that no two consecutive bands share a surface mode, so the
// order here is fixed and load-bearing:
//
//   white nav -> light hero -> TINTED principles -> white persona grid -> TINTED cta -> grey footer
//
// Light theme only (ADR-074): bands alternate white and a cool blue-grey gradient
// with hairlines, never dark. The illustrated twin car sits within the light hero.

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  motion,
  useReducedMotion,
  useScroll,
  useTransform,
} from "motion/react";
import { getHealth } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  AnimatedNumber, Badge, Skeleton,
  IconArrowRight, IconChart, IconGear, IconTarget, IconUser,
} from "@/components/ui";
import { VehicleHero } from "@/components/landing/VehicleHero";
import { StationTicker } from "@/components/landing/StationTicker";
import { TextAnimate } from "@/components/magicui/text-animate";

const STATIONS = [
  {
    id: "01",
    href: "/supervisor",
    title: "Supervisor",
    role: "Action",
    Icon: IconUser,
    body: "Replay or live-synthetic line state, defect alerts with risk drivers, and containment of units sharing production context.",
  },
  {
    id: "02",
    href: "/manager",
    title: "Manager",
    role: "Analysis",
    Icon: IconChart,
    body: "Constraint migration over time, station league table, and the drivers behind the defect Pareto.",
  },
  {
    id: "03",
    href: "/leadership",
    title: "Leadership",
    role: "Investment",
    Icon: IconTarget,
    body: "Value derived from measured recall, the alarm-budget trade-off, and where the next sensor dollar pays back.",
  },
  {
    id: "04",
    href: "/ops",
    title: "Operations",
    role: "System",
    Icon: IconGear,
    body: "Model health, drift detection, ingestion lag and audit trail — keeping the twin itself honest, not the line.",
  },
] as const;

// The three data contracts, stated as the product's argument rather than as docs.
// Each one is enforced in code (CLAUDE.md §3); none of them is a slogan.
const PRINCIPLES = [
  {
    n: "01",
    title: "Measured and inferred never look alike",
    body: "Every value carries its provenance. An inferred figure arrives with a confidence interval and renders behind a hatch, so a reconstruction can never be read as a reading.",
  },
  {
    n: "02",
    title: "No figure without its source",
    body: "Every currency amount traces to a named assumption. Where the model cannot support a claim, the interface says so instead of estimating — including about itself.",
  },
  {
    n: "03",
    title: "The line is watched, the operator is not",
    body: "Aggregation to shift, team or opaque bucket is enforced at the boundary. No individual identity or performance surfaces in any response, table or tooltip.",
  },
] as const;

const EXPO = [0.16, 1, 0.3, 1] as const;

// One-shot entrance for the hero copy: each piece rises on its own beat.
const heroItem = (reduced: boolean | null, delay: number, y = 24, x = 0) => ({
  initial: reduced ? false : { opacity: 0, y, x },
  animate: { opacity: 1, y: 0, x: 0 },
  transition: { duration: 0.7, ease: EXPO, delay },
});

// Scroll reveal for the marketing bands only: once, and never for a reduced-motion user.
const reveal = (reduced: boolean | null, i: number) => ({
  initial: reduced ? false : { opacity: 0, y: 20 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: "0px 0px -60px 0px" },
  transition: { duration: 0.65, ease: EXPO, delay: i * 0.07 },
});

/** Sets --mx/--my on whichever `.spotlight` card the pointer is over. One listener per grid. */
function trackSpotlight(e: React.PointerEvent<HTMLElement>) {
  if (e.pointerType !== "mouse") return;
  const card = (e.target as HTMLElement).closest<HTMLElement>(".spotlight");
  if (!card) return;
  const r = card.getBoundingClientRect();
  card.style.setProperty("--mx", `${e.clientX - r.left}px`);
  card.style.setProperty("--my", `${e.clientY - r.top}px`);
}

export default function Landing() {
  const [health, setHealth] = useState<any>(null);
  const [err, setErr] = useState(false);
  const reduced = useReducedMotion();
  const lineRef = useRef<HTMLDivElement>(null);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setErr(true));
  }, []);

  const { scrollYProgress } = useScroll({
    target: lineRef,
    offset: ["start end", "end start"],
  });
  const spineHeight = useTransform(scrollYProgress, [0.08, 0.92], ["0%", "100%"]);

  return (
    <main className="relative w-full bg-canvas">
      {/* ======================================================== top nav */}
      <header
        className={cn(
          "sticky top-0 z-40 border-b transition-[background-color,box-shadow,border-color] duration-300",
          scrolled
            ? "border-[rgb(15_23_42/0.07)] bg-canvas/[0.97] shadow-[0_4px_20px_-8px_rgb(15_23_42/0.12)] backdrop-blur-md"
            : "border-line bg-canvas"
        )}
      >
        <div className="mx-auto flex h-16 w-full max-w-[1280px] items-center gap-3 px-5 sm:gap-6 sm:px-8">
          <Link href="/" className="flex items-center gap-2.5">
            <span aria-hidden className="flex items-end gap-[3px]">
              <span className="block h-3 w-[3px] bg-primary" />
              <span className="block h-5 w-[3px] bg-ink" />
              <span className="block h-2.5 w-[3px] bg-primary" />
              <span className="block h-4 w-[3px] bg-ink" />
            </span>
            <span className="text-base font-bold text-ink">PulseTwin</span>
          </Link>

          <nav className="ml-6 hidden items-center gap-7 md:flex" aria-label="Sections">
            <a href="#principles" className="link-underline text-xs text-ink-dim transition-colors hover:text-primary">
              How it works
            </a>
            <a href="#views" className="link-underline text-xs text-ink-dim transition-colors hover:text-primary">
              Views
            </a>
            <Link href="/overview" className="link-underline text-xs text-ink-dim transition-colors hover:text-primary">
              Overview
            </Link>
          </nav>

          <div className="ml-auto flex items-center gap-3">
            {err && (
              <Badge variant="danger" dot className="hidden sm:inline-flex">
                API offline
              </Badge>
            )}
            <Link
              href="/overview"
              className="group inline-flex min-h-10 shrink-0 items-center gap-2 whitespace-nowrap border border-line px-3 text-xs font-bold text-ink transition-[color,border-color,background-color] duration-200 hover:border-primary hover:bg-primary/[0.03] hover:text-primary sm:px-4"
            >
              Open workspace
              <IconArrowRight size={14} className="transition-transform motion-safe:group-hover:translate-x-0.5" />
            </Link>
          </div>
        </div>
      </header>

      {/* ============================================== hero (light band) */}
      <section
        className="relative overflow-hidden border-b border-line bg-[linear-gradient(180deg,#ffffff_0%,#fbfcfe_55%,#f8fafc_100%)]"
        aria-labelledby="hero-title"
        onPointerMove={(e) => {
          if (reduced || e.pointerType !== "mouse") return;
          const r = e.currentTarget.getBoundingClientRect();
          e.currentTarget.style.setProperty("--mouse-x", `${e.clientX - r.left}px`);
          e.currentTarget.style.setProperty("--mouse-y", `${e.clientY - r.top}px`);
        }}
      >
        {/* A quiet engineering grid connects the photograph to the product UI. */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-70"
          style={{
            backgroundImage:
              "linear-gradient(var(--color-line) 1px, transparent 1px), linear-gradient(90deg, var(--color-line) 1px, transparent 1px)",
            backgroundSize: "48px 48px",
            maskImage: "radial-gradient(75% 70% at 30% 20%, #000 10%, transparent 100%)",
          }}
        />
        <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
          <div className="aurora aurora-a left-[-8%] top-[-20%] h-[460px] w-[560px] bg-[rgb(28_105_212/0.16)]" />
          <div className="aurora aurora-b right-[-6%] top-[10%] h-[520px] w-[620px] bg-[rgb(138_180_240/0.22)]" />
          <div className="aurora aurora-a bottom-[-30%] left-[35%] h-[380px] w-[520px] bg-[rgb(124_58_237/0.07)]" />
        </div>
        {/* A soft bloom behind the vehicle and a 5% pointer light -- both barely there. */}
        <div
          aria-hidden
          className="pointer-events-none absolute right-[-10%] top-[5%] hidden aspect-square w-[60%] lg:block"
          style={{ background: "radial-gradient(circle, rgb(28 105 212 / 0.08), transparent 62%)" }}
        />
        <div
          aria-hidden
          className="pointer-light pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(500px circle at var(--mouse-x, -600px) var(--mouse-y, -600px), rgb(28 105 212 / 0.08), transparent 60%)",
          }}
        />

        <div className="relative mx-auto grid w-full max-w-[1280px] gap-10 px-5 py-10 sm:px-8 sm:py-12 lg:grid-cols-[1.05fr_0.95fr] lg:items-center lg:gap-12 xl:gap-16">
          <div>
            <motion.span
              {...heroItem(reduced, 0, 0, -10)}
              className="label-upper inline-flex items-center gap-2.5 border border-primary/20 bg-canvas/70 px-3 py-1.5 text-primary shadow-[0_1px_2px_rgb(15_23_42/0.04)] backdrop-blur-sm"
            >
              <span aria-hidden className="live-dot size-1.5 shrink-0 rounded-full bg-primary text-primary" />
              Digital twin · mixed-model assembly
            </motion.span>

            <h1 id="hero-title" className="mt-6 max-w-2xl text-3xl font-bold leading-[1.04] text-ink sm:text-4xl lg:text-[clamp(2.5rem,4vw,3.6rem)]">
              {/* The page's one orchestrated moment (ADR-075): the first line resolves word
                  by word, then the gradient line follows whole -- split into words, a
                  background-clip gradient would restart on every word. */}
              <TextAnimate className="block" delay={0.08} duration={0.35}>
                The line never fails in one place.
              </TextAnimate>
              <motion.span {...heroItem(reduced, 0.5)} className="text-gradient mt-2 block pb-1">
                It fails in the space between stations.
              </motion.span>
            </h1>

            <motion.p
              {...heroItem(reduced, 0.32, 12)}
              className="mt-7 max-w-lg text-base leading-relaxed text-ink-dim"
            >
              PulseTwin is a digital twin for the assembly line — a second set of eyes that
              watches every station at once, catching what no single sensor, checklist or
              inspector could ever catch alone.
            </motion.p>

            <motion.div
              {...heroItem(reduced, 0.42, 10)}
              className="mt-8 flex flex-wrap items-center gap-6"
            >
              <Link
                href="/overview"
                className={cn(
                  "group inline-flex h-12 items-center gap-3 bg-primary px-7 text-sm font-bold",
                  "btn-lift tracking-[0.03em] text-on-primary hover:bg-primary-active"
                )}
              >
                Open the twin
                <IconArrowRight size={15} className="transition-transform motion-safe:group-hover:translate-x-1" />
              </Link>
              <a href="#views" className="group label-upper flex items-center gap-1.5 text-ink transition-colors hover:text-primary">
                Explore the views
                <span aria-hidden className="transition-transform duration-200 motion-safe:group-hover:translate-x-[3px]">›</span>
              </a>
            </motion.div>
            <motion.div
              {...heroItem(reduced, 0.52, 0)}
              className="mt-8 flex max-w-lg items-center gap-3 border-t border-line pt-5 text-2xs text-ink-faint sm:gap-5">
              <span>Station signals</span>
              <span aria-hidden className="text-primary">↗</span>
              <span>Shared context</span>
              <span aria-hidden className="text-primary">↗</span>
              <span className="text-ink">Clear decisions</span>
            </motion.div>
          </div>

          <motion.div
            initial={reduced ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.6, delay: 0.15 }}
            className="relative min-w-0"
          >
            <VehicleHero />
          </motion.div>
        </div>
      </section>

      <StationTicker />

      {/* ========================================== principles (accent band) */}
      <section id="principles" className="band-dark relative scroll-mt-16 overflow-hidden">
        {/* Hairline highlight where the light hero hands over to the accent band. */}
        <div aria-hidden className="absolute inset-x-0 top-0 h-px bg-[linear-gradient(90deg,transparent,rgb(138_180_240/0.35),transparent)]" />
        <div aria-hidden className="tex-grid-dark pointer-events-none absolute inset-0 [mask-image:linear-gradient(180deg,#000,transparent_85%)]" />
        <div aria-hidden className="aurora aurora-b right-[-10%] top-[-30%] h-[420px] w-[620px] bg-[rgb(28_105_212/0.10)]" />
        <div aria-hidden className="tex-grain pointer-events-none absolute inset-0" />
        <div className="relative mx-auto w-full max-w-[1280px] px-5 py-section sm:px-8">
          <div className="max-w-2xl">
            <span className="label-upper text-on-dark-soft">What it refuses to do</span>
            <h2 className="mt-4 text-3xl font-bold leading-[1.1] text-on-dark sm:text-4xl">
              Three contracts the interface <span className="text-gradient-dark">cannot break.</span>
            </h2>
            <p className="mt-5 text-base leading-relaxed text-on-dark-soft">
              A twin that quietly guesses is worse than no twin at all. These are enforced
              in code, from the simulator through the API to the last tooltip — not stated
              as intent and hoped for.
            </p>
          </div>

          <div onPointerMove={trackSpotlight} className="beam-top mt-14 grid gap-px border border-line-on-dark bg-line-on-dark md:grid-cols-3">
            {PRINCIPLES.map((p, i) => (
              <motion.div
                key={p.n}
                {...reveal(reduced, i)}
                className="spotlight bg-canvas/90 px-7 py-8 transition-colors duration-300 [--spot:rgb(28_105_212/0.08)] hover:bg-canvas"
              >
                <motion.div
                  className="m-stripe w-12 origin-left"
                  initial={reduced ? false : { scaleX: 0 }}
                  whileInView={{ scaleX: 1 }}
                  viewport={{ once: true }}
                  transition={{ duration: 0.7, ease: EXPO, delay: 0.25 + i * 0.1 }}
                />
                <span className="text-numeric mt-5 block text-2xs text-on-dark-soft">{p.n}</span>
                <h3 className="mt-2 text-lg font-bold leading-snug text-on-dark">{p.title}</h3>
                <p className="mt-3 text-sm leading-relaxed text-on-dark-soft">{p.body}</p>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* ============================================ views (light band) */}
      <section id="views" ref={lineRef} className="scroll-mt-16 border-b border-line">
        <div className="mx-auto w-full max-w-[1280px] px-5 py-section sm:px-8">
          <div className="flex flex-wrap items-end justify-between gap-6">
            <div className="max-w-xl">
              <span className="label-upper text-primary">The views</span>
              <h2 className="mt-4 text-3xl font-bold leading-[1.1] text-ink">
                Four ways in. One set of numbers.
              </h2>
              <p className="mt-5 text-base leading-relaxed text-ink-dim">
                Each role sees the decision it actually owns, drawn from the same artifact —
                so the floor and the board are never arguing about whose figure is right.
              </p>
            </div>

            {/* A scroll-driven spine: the unit moving down the line, at page scale. */}
            <div aria-hidden className="hidden h-24 w-px overflow-hidden bg-line lg:block">
              <motion.div className="w-full bg-primary" style={{ height: spineHeight }} />
            </div>
          </div>

          <ol onPointerMove={trackSpotlight} className="mt-14 grid gap-px border border-line bg-line sm:grid-cols-2 xl:grid-cols-4">
            {STATIONS.map((s, i) => (
              <Station key={s.id} station={s} index={i} reduced={reduced} />
            ))}
          </ol>
        </div>
      </section>

      {/* ================================================ cta (accent band) */}
      <section className="band-dark relative overflow-hidden">
        <div aria-hidden className="absolute inset-x-0 top-0 h-px bg-[linear-gradient(90deg,transparent,rgb(138_180_240/0.35),transparent)]" />
        <div aria-hidden className="tex-grain pointer-events-none absolute inset-0" />
        <div aria-hidden className="aurora aurora-a left-[calc(50%-360px)] top-[calc(50%-180px)] h-[360px] w-[720px] bg-[rgb(28_105_212/0.10)]" />
        <div className="relative mx-auto w-full max-w-[1280px] px-5 py-section text-center sm:px-8">
          <h2 className="mx-auto max-w-2xl text-2xl font-bold leading-[1.15] text-on-dark sm:text-4xl">
            Start on the overview. <span className="text-gradient-dark">It tells you where to look first.</span>
          </h2>
          <p className="mx-auto mt-5 max-w-xl text-sm leading-relaxed text-on-dark-soft">
            Live constraint, station health, what the alert budget has left, and the one thing
            that has changed since the shift opened.
          </p>
          <div className="mt-10 flex flex-wrap items-center justify-center gap-4">
            <Link
              href="/overview"
              className="btn-lift cta-glow group inline-flex h-12 items-center gap-2 bg-primary px-8 text-sm font-bold tracking-[0.03em] text-on-primary hover:bg-primary-active"
            >
              Open the overview
              <IconArrowRight size={15} className="transition-transform motion-safe:group-hover:translate-x-[3px]" />
            </Link>
          </div>
        </div>
      </section>

      {/* ============================================ footer telemetry */}
      <footer className="bg-surface-2">
        <div className="mx-auto w-full max-w-[1280px] px-5 py-16 sm:px-8">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3 text-2xs text-ink-faint">
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
          </div>

          <div className="mt-10 flex flex-wrap items-center justify-between gap-4 border-t border-line pt-6">
            <span className="text-xs text-ink-faint">
              PulseTwin — digital twin for mixed-model vehicle assembly.
            </span>
            <span className="text-numeric text-2xs text-ink-faint">v0.4.0</span>
          </div>
        </div>
      </footer>
    </main>
  );
}

function Station({
  station,
  index,
  reduced,
}: {
  station: (typeof STATIONS)[number];
  index: number;
  reduced: boolean | null;
}) {
  const { Icon } = station;

  return (
    <motion.li {...reveal(reduced, index)} className="flex bg-canvas">
      <Link
        href={station.href}
        className={cn(
          "spotlight group relative flex w-full flex-col border-t-2 border-transparent bg-canvas p-7",
          "transition-[background-color,border-color,box-shadow] duration-200 ease-soft",
          "hover:border-primary hover:bg-[linear-gradient(180deg,rgb(28_105_212/0.04),transparent_70%)]",
          "hover:shadow-[inset_0_1px_0_rgb(255_255_255/0.9)] focus-visible:border-primary"
        )}
      >
        <div className="flex items-center justify-between gap-3">
          <Icon size={22} className="text-primary transition-[color,transform] duration-200 group-hover:text-primary-active motion-safe:group-hover:-translate-y-px" />
          <span className="text-numeric text-2xs text-ink-faint">{station.id}</span>
        </div>

        <h3 className="mt-5 text-lg font-bold text-ink transition-transform duration-200 motion-safe:group-hover:-translate-y-px">{station.title}</h3>
        <span className="mt-1 text-2xs uppercase tracking-[0.115em] text-ink-faint">
          {station.role}
        </span>

        <p className="mt-3 flex-1 text-sm leading-relaxed text-ink-dim">{station.body}</p>

        <span className="label-upper mt-6 flex items-center gap-1.5 text-ink transition-colors group-hover:text-primary">
          Enter
          <IconArrowRight size={14} className="transition-transform motion-safe:group-hover:translate-x-[3px]" />
        </span>
      </Link>
    </motion.li>
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
