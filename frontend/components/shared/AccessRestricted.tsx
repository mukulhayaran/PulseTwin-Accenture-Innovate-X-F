"use client";

// Full-page counterpart to RoleGate.tsx's small in-panel lock card, for when an entire
// persona page is off-limits to the signed-in role, not just one widget on it.
//
// It is shown for exactly one case: a live session whose role this view is not for --
// a manager on /leadership, a leadership on /manager. Signed-out visitors never reach it,
// because usePersonaAccess sends them to /login instead; being asked to sign in is a
// clearer instruction than being told a role you do not have is required.
//
// Same honesty rule as before: this is what the UI does, not what stops someone hitting
// the API directly. The server-side require_role() calls are the enforcement, and the
// session token (serve/session_store.py) is what makes the role on those calls something
// the server issued rather than something the client asserted (docs/SECURITY.md).

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type Role, logout } from "@/lib/api";
import { ROLE_HOME } from "@/lib/routeAccess";
import { Button } from "@/components/ui";

export default function AccessRestricted({
  required,
  current,
}: {
  /** The roles this view is for. */
  required: Role[];
  /** The role actually signed in. */
  current: Role;
}) {
  const router = useRouter();

  const signOut = async () => {
    await logout();
    router.replace("/login");
  };

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-5 bg-canvas px-5 text-center">
      <span
        aria-hidden
        className="flex size-10 items-center justify-center rounded-full border border-line-strong bg-surface-2 text-ink-faint"
      >
        <svg width="16" height="16" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4">
          <rect x="2.5" y="6" width="9" height="6.5" rx="1.5" />
          <path d="M4.75 6V4.25a2.25 2.25 0 0 1 4.5 0V6" strokeLinecap="round" />
        </svg>
      </span>

      <div>
        <p className="text-sm font-bold text-ink">
          This view is for the {required.join(" or ")} role
        </p>
        <p className="mx-auto mt-2 max-w-md text-xs leading-relaxed text-ink-faint">
          You are signed in as <span className="font-bold text-ink-dim">{current}</span>.
          Each persona sees the decision it owns and not the others&apos; — these roles are
          peers, not ranks, so there is no view to be promoted into from here. Sign in with
          the credentials for that role to continue.
        </p>
      </div>

      <div className="flex flex-wrap items-center justify-center gap-2">
        <Link href={ROLE_HOME[current]}>
          <Button variant="primary" size="sm">
            Back to the {current} view
          </Button>
        </Link>
        <Button variant="ghost" size="sm" onClick={signOut}>
          Sign in as a different role
        </Button>
      </div>
    </main>
  );
}
