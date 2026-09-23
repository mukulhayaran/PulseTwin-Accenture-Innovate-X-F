"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { type Role, getRole, login, setRole } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Badge, Button, TooltipProvider } from "@/components/ui";

const ROLES: { role: Role; title: string; body: string; accent: "ok" | "info" | "ai" | "warn" }[] = [
  {
    role: "supervisor",
    title: "Supervisor",
    body: "Floor-level: replay or live-synthetic line map, alert feed, risk drivers, and related-unit containment.",
    accent: "ok",
  },
  {
    role: "manager",
    title: "Manager",
    body: "Constraint migration over time, station league table, root-cause Pareto, and the what-if console.",
    accent: "info",
  },
  {
    role: "leadership",
    title: "Leadership",
    body: "ROI, the value bridge, the alarm-budget trade-off, and the instrumentation planner.",
    accent: "ai",
  },
  {
    role: "system",
    title: "System",
    body: "Model health, drift, ingestion lag, and the audit log — infra-facing, not shop-floor-facing.",
    accent: "warn",
  },
];

const ROLE_HOME: Record<Role, string> = {
  supervisor: "/supervisor",
  manager: "/manager",
  leadership: "/leadership",
  system: "/ops",
};

const ACCENT: Record<string, { text: string; ring: string; bar: string }> = {
  ok: { text: "text-ok-fg", ring: "hover:border-ok/45", bar: "bg-ok" },
  info: { text: "text-info-fg", ring: "hover:border-info/45", bar: "bg-info" },
  ai: { text: "text-ai-fg", ring: "hover:border-ai/45", bar: "bg-ai" },
  warn: { text: "text-warn-fg", ring: "hover:border-warn/45", bar: "bg-warn" },
};

const inputClass = cn(
  "w-full rounded-md border border-line bg-surface-2 px-2.5 py-1.5",
  "text-xs text-ink placeholder:text-ink-faint",
  "transition-colors hover:border-line-strong focus:border-info/50 focus:outline-none"
);

export default function LoginPage() {
  const router = useRouter();
  const [current, setCurrent] = useState<Role>(() => getRole());
  const [picked, setPicked] = useState<(typeof ROLES)[number] | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const pick = (r: (typeof ROLES)[number]) => {
    setPicked(r);
    setUsername("");
    setPassword("");
    setError(null);
  };

  const submit = async () => {
    if (!picked || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await login(username.trim(), password);
      setRole(r.role);
      setCurrent(r.role);
      // Straight to that role's own page -- landing on "/" after already choosing and
      // authenticating a role just adds a redundant extra click through the same choice.
      router.push(ROLE_HOME[r.role]);
    } catch {
      setError("Incorrect username or password for this role.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <TooltipProvider>
      <main className="mx-auto min-h-screen w-full max-w-4xl px-5 py-10 sm:px-8 sm:py-16">
        <header className="mb-10">
          <div className="mb-4 text-xs font-semibold tracking-[0.2em] text-info-fg">PULSETWIN</div>
          <h1 className="mb-4 text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
            {picked ? `Sign in as ${picked.title}` : "Choose a role"}
          </h1>
          <p className="max-w-2xl text-sm leading-relaxed text-ink-dim">
            A production deployment would use SSO (Azure AD / Okta) issuing a JWT with a role
            claim, verified by the API on every request via the same{" "}
            <code className="text-numeric rounded border border-line bg-surface-2 px-1 py-0.5 text-xs text-ink">
              X-PulseTwin-Role
            </code>{" "}
            hierarchy this demo uses. The credential check here is real — validated server-side
            in <code className="text-numeric rounded border border-line bg-surface-2 px-1 py-0.5 text-xs text-ink">
              serve/routers/auth_demo.py
            </code> — but it is a fixed demo account per role, not a user directory, and it
            issues no session token. Calling the API directly with any role header still works,
            same as before: this raises the bar for the UI, it does not replace the OIDC
            upgrade path in docs/SECURITY.md.
          </p>
        </header>

        <div className="mb-6 flex items-center gap-2 text-2xs text-ink-faint">
          Active role
          <Badge variant={ROLES.find((r) => r.role === current)?.accent ?? "neutral"}>
            {current}
          </Badge>
        </div>

        <AnimatePresence mode="wait">
          {!picked ? (
            <motion.div
              key="grid"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="grid gap-3 sm:grid-cols-2"
            >
              {ROLES.map((r, i) => {
                const a = ACCENT[r.accent];
                const active = current === r.role;
                return (
                  <motion.button
                    key={r.role}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.35, delay: i * 0.05, ease: [0.2, 0.8, 0.25, 1] }}
                    onClick={() => pick(r)}
                    className={cn(
                      "group relative overflow-hidden rounded-xl border border-line bg-surface p-5 text-left",
                      "transition-[border-color,background-color,transform] duration-300 ease-soft",
                      "hover:-translate-y-0.5 hover:bg-surface-2",
                      a.ring,
                      active && "border-line-strong bg-surface-2"
                    )}
                  >
                    <span
                      aria-hidden
                      className={cn(
                        "absolute inset-y-0 left-0 w-0.5 transition-opacity duration-300",
                        a.bar,
                        active ? "opacity-100" : "opacity-0 group-hover:opacity-60"
                      )}
                    />
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <h2 className={cn("text-base font-semibold", a.text)}>{r.title}</h2>
                      {active && <Badge variant="neutral">Active</Badge>}
                    </div>
                    <p className="text-xs leading-relaxed text-ink-dim">{r.body}</p>
                  </motion.button>
                );
              })}
            </motion.div>
          ) : (
            <motion.div
              key="form"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.25 }}
              className="max-w-sm rounded-xl border border-line-strong bg-surface-2 p-5"
            >
              <div className="mb-4 flex items-center justify-between gap-2">
                <h2 className={cn("text-base font-semibold", ACCENT[picked.accent].text)}>
                  {picked.title}
                </h2>
                <button
                  onClick={() => setPicked(null)}
                  className="text-2xs text-ink-faint transition-colors hover:text-ink"
                >
                  ← choose a different role
                </button>
              </div>

              <form
                className="space-y-3"
                onSubmit={(e) => { e.preventDefault(); submit(); }}
              >
                <div>
                  <label className="mb-1 block text-2xs font-medium text-ink-faint">Username</label>
                  <input
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder={picked.role}
                    autoFocus
                    className={inputClass}
                  />
                </div>
                <div>
                  <label className="mb-1 block text-2xs font-medium text-ink-faint">Password</label>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className={inputClass}
                  />
                </div>
                {error && <p className="text-2xs text-danger-fg">{error}</p>}
                <Button type="submit" variant="ok" size="sm" disabled={busy} className="w-full">
                  {busy ? "Signing in…" : `Sign in as ${picked.title}`}
                </Button>
              </form>
            </motion.div>
          )}
        </AnimatePresence>

        <footer className="mt-10 border-t border-line pt-5">
          <Link
            href="/"
            className="rounded-md text-2xs text-ink-faint transition-colors hover:text-ink"
          >
            ← Back to landing
          </Link>
        </footer>
      </main>
    </TooltipProvider>
  );
}
