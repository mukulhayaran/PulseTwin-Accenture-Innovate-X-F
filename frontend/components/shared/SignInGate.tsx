"use client";

// The sign-in form a restricted view renders IN PLACE of its data when nobody is signed
// in -- the ordinary "this page needs an account" pattern, with no detour to a separate
// login page. On success login() fires SESSION_EVENT, usePersonaAccess re-evaluates, and
// the page renders. It is also what a signed-in user of ANOTHER role sees (`current`):
// signing in here switches the session to the account that owns this view. The
// credential check is server-side (serve/routers/auth_demo.py); this form only collects it.

import Link from "next/link";
import { useState } from "react";
import { type Role, login } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Button, IconArrowRight } from "@/components/ui";
import { BorderBeam } from "@/components/magicui/border-beam";

const inputClass = cn(
  "h-12 w-full border border-line-strong bg-canvas px-4",
  "text-sm text-ink placeholder:text-ink-faint",
  "transition-colors hover:border-ink focus:border-ink focus:outline-none"
);

const labelClass = "mb-2 block text-2xs font-bold uppercase tracking-[0.115em] text-ink-faint";

export default function SignInGate({
  view,
  required,
  current,
}: {
  view: string;
  required: Role[];
  /** The role already signed in, when it is not one this view is for. */
  current?: Role;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const s = await login(username.trim(), password);
      // Wrong account for this view: the page re-renders this same gate (state kept),
      // so say why instead of silently showing the form again.
      if (!required.includes(s.role)) {
        setError(`That is a ${s.role} account. The ${view} view needs ${required.join(" or ")}.`);
        setBusy(false);
      }
    } catch {
      setError("Incorrect username or password.");
      setBusy(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-surface-2 px-4">
      {/* The one highlighted panel in the app (ADR-075): a slow BMW-blue beam on the
          card you sign in on. Off under reduced motion. */}
      <div className="relative w-full max-w-sm border border-line bg-canvas p-8">
        <BorderBeam />
        <Link href="/" className="flex items-center gap-2.5">
          <span aria-hidden className="flex items-end gap-[3px]">
            <span className="block h-3 w-[3px] bg-primary" />
            <span className="block h-5 w-[3px] bg-ink" />
            <span className="block h-2.5 w-[3px] bg-primary" />
            <span className="block h-4 w-[3px] bg-ink" />
          </span>
          <span className="text-base font-bold text-ink">PulseTwin</span>
        </Link>

        <h1 className="mt-8 text-2xl font-bold text-ink">Sign in</h1>
        <p className="mt-2 text-xs leading-relaxed text-ink-faint">
          The {view} view is restricted to the {required.join(" or ")} role.
          {current && (
            <>
              {" "}You are signed in as <span className="font-bold text-ink-dim">{current}</span>;
              signing in here switches account.
            </>
          )}
        </p>

        <form className="mt-6 space-y-4" onSubmit={submit}>
          <div>
            <label htmlFor="signin-username" className={labelClass}>Username</label>
            <input
              id="signin-username"
              name="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
              required
              className={inputClass}
            />
          </div>
          <div>
            <label htmlFor="signin-password" className={labelClass}>Password</label>
            <input
              id="signin-password"
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className={inputClass}
            />
          </div>
          {error && <p role="alert" className="text-xs text-danger-fg">{error}</p>}
          <Button type="submit" variant="primary" size="lg" disabled={busy} className="w-full">
            {busy ? "Signing in…" : "Sign in"}
            {!busy && <IconArrowRight size={15} />}
          </Button>
        </form>

        <Link
          href="/supervisor"
          className="mt-6 inline-block text-2xs text-ink-faint transition-colors hover:text-ink"
        >
          ← Go to the public supervisor view
        </Link>
      </div>
    </main>
  );
}
