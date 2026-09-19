"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { getHealth } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Badge, Skeleton } from "@/components/ui";

const PERSONAS = [
  {
    href: "/supervisor",
    title: "Supervisor",
    tag: "Action",
    body: "Live line state, defect alerts with root causes, and containment of every unit sharing the same causal fingerprint.",
    accent: "ok",
    glyph: (
      <>
        <rect x="2" y="7" width="4" height="10" rx="1" />
        <rect x="8" y="4" width="4" height="13" rx="1" />
        <rect x="14" y="10" width="4" height="7" rx="1" />
      </>
    ),
  },
  {
    href: "/manager",
    title: "Manager",
    tag: "Analysis",
    body: "Constraint migration over time, station league table, and the drivers behind the defect Pareto.",
    accent: "info",
    glyph: (
      <>
        <path d="M2 14l5-5 4 3 7-8" fill="none" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="7" cy="9" r="1.6" />
        <circle cx="11" cy="12" r="1.6" />
      </>
    ),
  },
  {
    href: "/leadership",
    title: "Leadership",
    tag: "Investment",
    body: "Value derived from measured recall, the alarm-budget trade-off, and where the next sensor dollar pays back.",
    accent: "ai",
    glyph: (
      <>
        <path d="M10 2l7 4v6c0 4-3 6.5-7 8-4-1.5-7-4-7-8V6z" fill="none" strokeWidth="1.5" strokeLinejoin="round" />
        <path d="M7 10l2 2 4-4" fill="none" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </>
    ),
  },
  {
    href: "/ops",
    title: "Operations",
    tag: "System",
    body: "Model health, drift detection, ingestion lag and audit trail — keeping the twin itself honest, not the line.",
    accent: "warn",
    glyph: (
      <>
        <circle cx="10" cy="10" r="3" fill="none" strokeWidth="1.6" />
        <path d="M10 1.5v3M10 15.5v3M18.5 10h-3M4.5 10h-3M16 4l-2.1 2.1M6.1 13.9L4 16M16 16l-2.1-2.1M6.1 6.1L4 4" strokeWidth="1.5" strokeLinecap="round" />
      </>
    ),
  },
] as const;

const ACCENT: Record<string, { ring: string; text: string; glow: string }> = {
  ok: { ring: "hover:border-ok/40", text: "text-ok-fg", glow: "group-hover:shadow-[0_0_40px_-12px_var(--color-ok)]" },
  info: { ring: "hover:border-info/40", text: "text-info-fg", glow: "group-hover:shadow-[0_0_40px_-12px_var(--color-info)]" },
  ai: { ring: "hover:border-ai/40", text: "text-ai-fg", glow: "group-hover:shadow-[0_0_40px_-12px_var(--color-ai)]" },
  warn: { ring: "hover:border-warn/40", text: "text-warn-fg", glow: "group-hover:shadow-[0_0_40px_-12px_var(--color-warn)]" },
};

export default function Landing() {
  const [health, setHealth] = useState<any>(null);
  const [err, setErr] = useState(false);
  const reduced = useReducedMotion();

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setErr(true));
  }, []);

  const rise = (i: number) =>
    reduced
      ? {}
      : {
          initial: { opacity: 0, y: 12 },
          animate: { opacity: 1, y: 0 },
          transition: { duration: 0.4, delay: i * 0.06, ease: [0.2, 0.8, 0.25, 1] as const },
        };

  return (
    <main className="relative mx-auto min-h-screen w-full max-w-6xl px-5 py-10 sm:px-8 sm:py-16">
      {/* Ambient wash — the only decorative element on the page. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-80 opacity-60"
        style={{
          background:
            "radial-gradient(60% 100% at 25% 0%, color-mix(in oklab, var(--color-info) 12%, transparent), transparent 70%)",
        }}
      />

      <motion.header {...rise(0)} className="relative mb-12 sm:mb-16">
        <div className="mb-5 flex items-center justify-between gap-4">
          <span className="text-xs font-semibold tracking-[0.2em] text-info-fg">PULSETWIN</span>
          <Link
            href="/login"
            className="rounded-md px-2 py-1 text-2xs font-medium text-ink-faint transition-colors hover:bg-surface-2 hover:text-ink"
          >
            Login / Role →
          </Link>
        </div>

        <h1 className="max-w-3xl text-2xl font-semibold leading-[1.15] tracking-tight text-ink sm:text-3xl">
          The constraint moved.
          <span className="block text-ink-faint">No single station metric would have told you.</span>
        </h1>

        <p className="mt-5 max-w-2xl text-sm leading-relaxed text-ink-dim">
          A digital twin of a mixed-model vehicle assembly line. On EV-heavy days the bottleneck
          shifts from <Mono>S12</Mono> to <Mono>S19</Mono>, backing units up in the paint buffer and
          pushing operators at an unsensored station to rush their checklist. Two different defect
          modes, one shared cause, neither visible until final inspection{" "}
          <Mono>22 minutes</Mono> later.
        </p>
      </motion.header>

      <div className="relative mb-12 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {PERSONAS.map((p, i) => {
          const a = ACCENT[p.accent];
          return (
            <motion.div key={p.href} {...rise(i + 1)}>
              <Link
                href={p.href}
                className={cn(
                  "group flex h-full flex-col rounded-xl border border-line bg-surface p-5",
                  "transition-[border-color,background-color,box-shadow,transform] duration-300 ease-soft",
                  "hover:-translate-y-0.5 hover:bg-surface-2",
                  a.ring,
                  a.glow
                )}
              >
                <div className="mb-4 flex items-start justify-between">
                  <svg
                    viewBox="0 0 20 20"
                    className={cn("size-5 stroke-current", a.text)}
                    fill="currentColor"
                    aria-hidden
                  >
                    {p.glyph}
                  </svg>
                  <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-faint">
                    {p.tag}
                  </span>
                </div>

                <h2 className="mb-1.5 text-base font-semibold text-ink">{p.title}</h2>
                <p className="mb-5 flex-1 text-xs leading-relaxed text-ink-dim">{p.body}</p>

                <span
                  className={cn(
                    "inline-flex items-center gap-1 text-2xs font-medium transition-transform duration-300",
                    "group-hover:translate-x-0.5",
                    a.text
                  )}
                >
                  Enter
                  <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
                    <path d="M2 6h8M6.5 2.5L10 6l-3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </span>
              </Link>
            </motion.div>
          );
        })}
      </div>

      <motion.footer
        {...rise(5)}
        className="relative flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-line pt-5 text-2xs text-ink-faint"
      >
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
            <Meta
              k="stations"
              v={Object.entries(health.station_counts)
                .map(([k, v]) => `${k}=${v}`)
                .join("  ")}
            />
            <Meta k="database" v={health.database} />
            <Meta
              k="artifact built"
              v={String(health.artifact_built_at).replace("T", " ").slice(0, 16)}
            />
          </>
        )}
      </motion.footer>
    </main>
  );
}

function Mono({ children }: { children: React.ReactNode }) {
  return <span className="text-numeric text-ink">{children}</span>;
}

function Meta({ k, v }: { k: string; v: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="text-ink-faint">{k}</span>
      <span className="text-numeric text-ink-dim">{v}</span>
    </span>
  );
}
