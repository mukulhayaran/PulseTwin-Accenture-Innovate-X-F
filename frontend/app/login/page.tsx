"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { type Role, type Session, getSession, login, logout, restoreSession } from "@/lib/api";
import { ROLE_HOME, canEnter, safeNext } from "@/lib/routeAccess";
import { cn } from "@/lib/cn";
import {
  Badge, Button, TooltipProvider,
  IconArrowRight, IconChart, IconGear, IconTarget, IconUser,
} from "@/components/ui";

const ROLES: {
  role: Role;
  title: string;
  body: string;
  Icon: typeof IconUser;
}[] = [
  {
    role: "supervisor",
    title: "Supervisor",
    body: "Floor-level: replay or live-synthetic line map, alert feed, risk drivers, and related-unit containment.",
    Icon: IconUser,
  },
  {
    role: "manager",
    title: "Manager",
    body: "Constraint migration over time, station league table, root-cause Pareto, and the what-if console.",
    Icon: IconChart,
  },
  {
    role: "leadership",
    title: "Leadership",
    body: "ROI, the value bridge, the alarm-budget trade-off, and the instrumentation planner.",
    Icon: IconTarget,
  },
  {
    role: "system",
    title: "System",
    body: "Model health, drift, ingestion lag, and the audit log — infra-facing, not shop-floor-facing.",
    Icon: IconGear,
  },
];

const ROLE_TONE: Record<Role, "ok" | "info" | "ai" | "warn"> = {
  supervisor: "ok",
  manager: "info",
  leadership: "ai",
  system: "warn",
};

// BMW `text-input`: square, hairline, 48px tall, and the border thickens to ink on
// focus rather than taking a coloured ring.
const inputClass = cn(
  "h-12 w-full border border-line-strong bg-canvas px-4",
  "text-sm text-ink placeholder:text-ink-faint",
  "transition-colors hover:border-ink focus:border-ink focus:outline-none"
);

export default function LoginPage() {
  const router = useRouter();
  const [session, setSessionState] = useState<Session | null>(null);
  const [picked, setPicked] = useState<(typeof ROLES)[number] | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // The view the guard bounced the visitor off. Read from window.location rather than
  // useSearchParams(): `next.config.ts` sets output: "export", and useSearchParams forces
  // the whole page into a client-side bailout that a static export has to render as a
  // Suspense fallback first.
  const [next, setNext] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined") {
      setNext(safeNext(new URLSearchParams(window.location.search).get("next")));
    }
    // A token in sessionStorage is only a session if the server still honours it --
    // otherwise this page would offer "continue as manager" for a session that ended.
    restoreSession()
      .catch(() => null)
      .then(() => setSessionState(getSession()));
  }, []);

  const pick = (r: (typeof ROLES)[number]) => {
    setPicked(r);
    setUsername("");
    setPassword("");
    setError(null);
  };

  // Back to where the visitor was headed when the guard stopped them, but only if the
  // role they just signed in as may actually open it -- otherwise a manager who was
  // bounced off /leadership would be handed straight back to the same refusal.
  const destinationFor = (role: Role) =>
    next && canEnter(next, role) ? next : ROLE_HOME[role];

  const submit = async () => {
    if (!picked || busy) return;
    setBusy(true);
    setError(null);
    try {
      const s = await login(username.trim(), password);
      setSessionState(s);
      // Straight to that role's own page -- landing on "/" after already choosing and
      // authenticating a role just adds a redundant extra click through the same choice.
      router.push(destinationFor(s.role));
    } catch {
      setError("Incorrect username or password for this role.");
    } finally {
      setBusy(false);
    }
  };

  const signOut = async () => {
    setBusy(true);
    await logout();
    setSessionState(null);
    setPicked(null);
    setBusy(false);
  };

  const current: Role = session?.role ?? "supervisor";

  return (
    <TooltipProvider>
      <main className="min-h-screen bg-canvas">
        {/* ------------------------------------------------------ dark band */}
        <section className="bg-surface-dark">
          <div className="mx-auto w-full max-w-[1440px] px-5 py-16 sm:px-8">
            <Link href="/" className="flex items-center gap-2.5">
              <span aria-hidden className="flex items-end gap-[3px]">
                <span className="block h-3 w-[3px] bg-info-on-dark" />
                <span className="block h-5 w-[3px] bg-on-dark" />
                <span className="block h-2.5 w-[3px] bg-info-on-dark" />
                <span className="block h-4 w-[3px] bg-on-dark" />
              </span>
              <span className="text-base font-bold text-on-dark">PulseTwin</span>
            </Link>

            <h1 className="mt-10 max-w-2xl text-3xl font-bold leading-[1.1] text-on-dark">
              {picked ? `Sign in as ${picked.title}` : "Choose a role"}
            </h1>
            <p className="mt-5 max-w-2xl text-sm leading-relaxed text-on-dark-soft">
              Each role sees the decision it owns and not the others&apos; — these are
              separations of duty, not privilege levels, so a manager does not open the
              leadership view and leadership does not open the manager&apos;s. The API
              enforces its own gate on every request; the interface mirrors it.
            </p>
            {next && !session && (
              <p className="mt-5 max-w-2xl border-l-2 border-info-on-dark pl-4 text-sm leading-relaxed text-on-dark">
                <span className="font-bold">{next}</span> needs a signed-in role. Sign in
                below and you will be taken straight there.
              </p>
            )}
          </div>
        </section>

        {/* ----------------------------------------------------- light band */}
        <section className="mx-auto w-full max-w-[1440px] px-5 py-14 sm:px-8">
          {session ? (
            <div className="mb-8 flex flex-wrap items-center gap-3 border border-line bg-surface-2 px-4 py-3">
              <span className="text-2xs text-ink-faint">Signed in as</span>
              <Badge variant={ROLE_TONE[current]}>{current}</Badge>
              <span className="flex-1" />
              <Link href={destinationFor(current)}>
                <Button variant="primary" size="xs">
                  Continue to the {current} view
                  <IconArrowRight size={13} />
                </Button>
              </Link>
              <Button variant="ghost" size="xs" onClick={signOut} disabled={busy}>
                Sign out
              </Button>
            </div>
          ) : (
            <div className="mb-8 flex items-center gap-3 text-2xs text-ink-faint">
              Not signed in — acting as
              <Badge variant={ROLE_TONE.supervisor}>supervisor</Badge>
              <span>
                the floor view and the overview are open; everything else needs a role.
              </span>
            </div>
          )}

          <AnimatePresence mode="wait">
            {!picked ? (
              <motion.div
                key="grid"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="grid gap-px bg-line sm:grid-cols-2 xl:grid-cols-4"
              >
                {ROLES.map((r, i) => {
                  const active = session?.role === r.role;
                  return (
                    <motion.button
                      key={r.role}
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.35, delay: i * 0.05, ease: [0.2, 0.8, 0.25, 1] }}
                      onClick={() => pick(r)}
                      className={cn(
                        "group flex flex-col bg-canvas p-6 text-left",
                        "transition-colors duration-200 ease-soft hover:bg-surface-2"
                      )}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <r.Icon size={22} className="text-primary" />
                        {active && <Badge variant="solid">Active</Badge>}
                      </div>
                      <h2 className="mt-5 text-lg font-bold text-ink">{r.title}</h2>
                      <p className="mt-3 flex-1 text-sm leading-relaxed text-ink-dim">{r.body}</p>
                      <span className="label-upper mt-6 flex items-center gap-1.5 text-ink transition-colors group-hover:text-primary">
                        Sign in
                        <span aria-hidden>›</span>
                      </span>
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
                className="max-w-md border border-line p-6"
              >
                <div className="mb-6 flex items-center justify-between gap-3">
                  <span className="flex items-center gap-2.5">
                    <picked.Icon size={20} className="text-primary" />
                    <h2 className="text-lg font-bold text-ink">{picked.title}</h2>
                  </span>
                  <button
                    onClick={() => setPicked(null)}
                    className="text-2xs text-ink-faint transition-colors hover:text-ink"
                  >
                    ← choose a different role
                  </button>
                </div>

                <form
                  className="space-y-4"
                  onSubmit={(e) => {
                    e.preventDefault();
                    submit();
                  }}
                >
                  {/* htmlFor/id, not a wrapping <label>: without the association a
                      screen reader announces both fields as unlabelled, and the password
                      box in particular is then just "edit text". */}
                  <div>
                    <label
                      htmlFor="login-username"
                      className="mb-2 block text-2xs font-bold uppercase tracking-[0.115em] text-ink-faint"
                    >
                      Username
                    </label>
                    <input
                      id="login-username"
                      name="username"
                      autoComplete="username"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      placeholder={picked.role}
                      autoFocus
                      className={inputClass}
                    />
                  </div>
                  <div>
                    <label
                      htmlFor="login-password"
                      className="mb-2 block text-2xs font-bold uppercase tracking-[0.115em] text-ink-faint"
                    >
                      Password
                    </label>
                    <input
                      id="login-password"
                      name="password"
                      type="password"
                      autoComplete="current-password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className={inputClass}
                    />
                  </div>
                  {error && <p className="text-xs text-danger-fg">{error}</p>}
                  <Button
                    type="submit"
                    variant="primary"
                    size="lg"
                    disabled={busy}
                    className="w-full"
                  >
                    {busy ? "Signing in…" : `Sign in as ${picked.title}`}
                    {!busy && <IconArrowRight size={15} />}
                  </Button>
                </form>
              </motion.div>
            )}
          </AnimatePresence>

          <p className="mt-12 max-w-3xl border-t border-line pt-6 text-xs leading-relaxed text-ink-faint">
            A production deployment would use SSO (Azure AD / Okta) issuing a JWT with a role
            claim, verified by the API on every request via the same{" "}
            <code className="text-numeric border border-line bg-surface-2 px-1.5 py-0.5 text-2xs text-ink">
              X-PulseTwin-Role
            </code>{" "}
            hierarchy this demo uses. The credential check here is real — validated
            server-side in{" "}
            <code className="text-numeric border border-line bg-surface-2 px-1.5 py-0.5 text-2xs text-ink">
              serve/routers/auth_demo.py
            </code>{" "}
            — and a correct login mints an opaque session token the API resolves to a role on
            every request that carries it, so the role on that path is one the server issued
            and can revoke, not one the client asserts. It is still a fixed demo account per
            role, not a user directory, and a caller that presents no token and sets{" "}
            <code className="text-numeric border border-line bg-surface-2 px-1.5 py-0.5 text-2xs text-ink">
              X-PulseTwin-Role
            </code>{" "}
            by hand is treated exactly as before: this raises the bar for the UI, it does not
            replace the OIDC upgrade path in docs/SECURITY.md.
          </p>

          <Link
            href="/"
            className="mt-8 inline-block text-2xs text-ink-faint transition-colors hover:text-ink"
          >
            ← Back to landing
          </Link>
        </section>
      </main>
    </TooltipProvider>
  );
}
