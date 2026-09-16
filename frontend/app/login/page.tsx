"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { type Role, getRole, setRole } from "@/lib/api";

const ROLES: { role: Role; icon: string; title: string; body: string; accent: string }[] = [
  {
    role: "supervisor",
    icon: "▦",
    title: "SUPERVISOR",
    body: "Floor-level: live line map, alert feed, root causes, and containment of units sharing a defect fingerprint.",
    accent: "text-signal-green border-signal-green/40",
  },
  {
    role: "manager",
    icon: "◫",
    title: "MANAGER",
    body: "Constraint migration over time, station league table, root-cause Pareto, and the what-if console.",
    accent: "text-signal-blue border-signal-blue/40",
  },
  {
    role: "leadership",
    icon: "◈",
    title: "LEADERSHIP",
    body: "ROI, the value bridge, the alarm-budget trade-off, and the instrumentation planner.",
    accent: "text-signal-purple border-signal-purple/40",
  },
  {
    role: "system",
    icon: "◉",
    title: "SYSTEM",
    body: "Model health, drift, ingestion lag, and the audit log -- infra-facing, not shop-floor-facing.",
    accent: "text-signal-amber border-signal-amber/40",
  },
];

export default function LoginPage() {
  const router = useRouter();
  const [current, setCurrent] = useState<Role>(() => getRole());

  const choose = (role: Role) => {
    setRole(role);
    setCurrent(role);
    router.push("/");
  };

  return (
    <main className="min-h-screen bg-bg-primary font-mono px-6 py-12 max-w-4xl mx-auto text-txt-primary">
      <div className="mb-10">
        <div className="text-signal-blue tracking-[0.3em] text-xs mb-3">PULSETWIN</div>
        <h1 className="text-3xl font-semibold mb-3 tracking-tight">Choose a role</h1>
        <p className="text-txt-muted max-w-2xl leading-relaxed text-sm">
          A production deployment would use SSO (Azure AD / Okta) issuing a JWT with a
          role claim, verified by the API on every request via the same{" "}
          <code className="text-txt-primary">X-PulseTwin-Role</code> hierarchy this demo
          uses. There is no real authentication here -- this page only sets the demo
          role client-side so you can see what each persona is, and isn&apos;t, allowed
          to see and do.
        </p>
      </div>

      <div className="mb-8 text-[12px] text-txt-muted">
        Active role:{" "}
        <span className="text-signal-green font-semibold">{current.toUpperCase()}</span>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {ROLES.map((r) => (
          <button
            key={r.role}
            onClick={() => choose(r.role)}
            className={`text-left block border rounded p-5 bg-bg-card hover:bg-bg-surface transition-colors ${r.accent} ${
              current === r.role ? "ring-1 ring-inset ring-current" : ""
            }`}
          >
            <div className="flex items-baseline justify-between mb-3">
              <span className="text-2xl">{r.icon}</span>
              {current === r.role && (
                <span className="text-[10px] tracking-widest">ACTIVE</span>
              )}
            </div>
            <div className="font-semibold tracking-wide mb-2">{r.title}</div>
            <p className="text-[12px] text-txt-muted leading-relaxed">{r.body}</p>
          </button>
        ))}
      </div>

      <div className="mt-10 border-t border-bg-surface pt-4 text-[11px]">
        <Link href="/" className="text-txt-muted hover:text-txt-primary">
          ← back to landing
        </Link>
      </div>
    </main>
  );
}
