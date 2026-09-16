"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getHealth } from "@/lib/api";

const PERSONAS = [
  {
    href: "/supervisor",
    icon: "▦",
    title: "SUPERVISOR",
    tag: "ACTION",
    body: "Live line state, defect alerts with root causes, and containment of every unit sharing the same causal fingerprint.",
    accent: "text-signal-green border-signal-green/40",
  },
  {
    href: "/manager",
    icon: "◫",
    title: "MANAGER",
    tag: "ANALYSIS",
    body: "Constraint migration over time, station league table, and the drivers behind the defect Pareto.",
    accent: "text-signal-blue border-signal-blue/40",
  },
  {
    href: "/leadership",
    icon: "◈",
    title: "LEADERSHIP",
    tag: "INVESTMENT",
    body: "Value derived from measured recall, the alarm-budget trade-off, and where the next sensor dollar pays back.",
    accent: "text-signal-purple border-signal-purple/40",
  },
  {
    href: "/ops",
    icon: "◉",
    title: "OPERATIONS",
    tag: "SYSTEM",
    body: "Model health, drift detection, ingestion lag, and infra status -- keeping the twin itself honest, not the line.",
    accent: "text-signal-amber border-signal-amber/40",
  },
];

export default function Landing() {
  const [health, setHealth] = useState<any>(null);
  const [err, setErr] = useState(false);

  useEffect(() => { getHealth().then(setHealth).catch(() => setErr(true)); }, []);

  return (
    <main className="min-h-screen font-mono px-6 py-12 max-w-6xl mx-auto">
      <div className="mb-14">
        <div className="flex items-baseline justify-between mb-3">
          <div className="text-signal-blue tracking-[0.3em] text-xs">PULSETWIN</div>
          <Link href="/login" className="text-[11px] text-txt-muted hover:text-txt-primary">
            Login / Role →
          </Link>
        </div>
        <h1 className="text-4xl font-semibold mb-3 tracking-tight">
          The constraint moved.<br />
          <span className="text-txt-muted">No single station metric would have told you.</span>
        </h1>
        <p className="text-txt-muted max-w-2xl leading-relaxed text-sm">
          A digital twin of a mixed-model vehicle assembly line. On EV-heavy days the
          bottleneck shifts from S12 to S19, backing units up in the paint buffer and
          pushing operators at an unsensored station to rush their checklist. Two
          different defect modes, one shared cause, neither visible until final
          inspection 22 minutes later.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-4 mb-12">
        {PERSONAS.map((p) => (
          <Link key={p.href} href={p.href}
                className={`block border rounded p-5 bg-bg-card hover:bg-bg-surface transition-colors ${p.accent}`}>
            <div className="flex items-baseline justify-between mb-3">
              <span className="text-2xl">{p.icon}</span>
              <span className="text-[10px] tracking-widest text-txt-muted">{p.tag}</span>
            </div>
            <div className="font-semibold tracking-wide mb-2">{p.title}</div>
            <p className="text-[12px] text-txt-muted leading-relaxed mb-4">{p.body}</p>
            <span className="text-[11px]">Enter →</span>
          </Link>
        ))}
      </div>

      <div className="border-t border-bg-surface pt-4 text-[11px] text-txt-muted flex flex-wrap gap-x-6 gap-y-1">
        {err && <span className="text-signal-red">API unreachable — start the backend on :7860</span>}
        {health && (
          <>
            <span className="text-signal-green">● API {health.status}</span>
            <span>plants: {health.plants.join(", ")}</span>
            <span>
              stations: {Object.entries(health.station_counts).map(([k, v]) => `${k}=${v}`).join("  ")}
            </span>
            <span>RSS: {health.rss_mb ? `${health.rss_mb.toFixed(0)} MB` : health.rss_source}</span>
            <span>artifacts built {health.artifact_built_at}</span>
          </>
        )}
      </div>
    </main>
  );
}
