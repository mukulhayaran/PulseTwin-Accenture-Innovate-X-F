"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { SESSION_EVENT, type Role, getSession, restoreSession, setRole } from "@/lib/api";
import { canEnter, policyFor } from "@/lib/routeAccess";

// Gates a persona page's ENTRY on the SESSION the server issued, then locks the page to
// its own fixed persona once entry is granted -- two different questions kept deliberately
// separate, as before. What changed is the first one:
//
//   was: entry was granted by `roleAtLeast(currentRole, minRole)` against a role the
//        client had set on itself, defaulting to "leadership". So a visitor who never
//        signed in entered every view, and a leadership login entered /manager.
//   now: entry requires a live server session whose role the route policy names
//        explicitly (lib/routeAccess.ts). Signed out -> the page renders SignInGate in place. Signed in as
//        the wrong role -> the page renders SignInGate for its own role (switch account).
//
// The persona pin stays exactly as it was and for the same reason: a page shows its own
// persona's job, never a blend (CLAUDE.md §7, "the views are not symmetric"). setRole()
// now clamps to the session, so pinning can no longer be the thing that grants privilege.
//
// The session is re-validated against /api/auth/session on mount rather than trusted from
// sessionStorage, so an expired or signed-out token does not keep opening pages just
// because a tab still holds the string.

export type AccessState =
  | { status: "pending" }
  /** Entry granted; the page may render. */
  | { status: "granted"; role: Role }
  /** No session at all -- the page renders SignInGate instead of its data. */
  | { status: "anonymous" }
  /** Signed in, but as a role this view is not for. */
  | { status: "denied"; role: Role };

export function usePersonaAccess(pageRole: Role): AccessState {
  const pathname = usePathname();
  const [state, setState] = useState<AccessState>({ status: "pending" });
  // Bumped on sign-in / sign-out, so the gate re-evaluates without a navigation.
  const [epoch, setEpoch] = useState(0);

  useEffect(() => {
    const bump = () => setEpoch((n) => n + 1);
    window.addEventListener(SESSION_EVENT, bump);
    return () => window.removeEventListener(SESSION_EVENT, bump);
  }, []);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const session = getSession() ? await restoreSession() : null;
      if (cancelled) return;

      // A public route (currently /supervisor) is enterable by anyone; an anonymous
      // visitor simply acts as "supervisor", which is the API's own default role.
      const policy = policyFor(pathname ?? "");
      const isPublic = !policy || policy.requires === null;

      if (!session && !isPublic) {
        setState({ status: "anonymous" });
        return;
      }

      const role = session?.role ?? "supervisor";
      if (!canEnter(pathname ?? "", session?.role ?? null)) {
        setState({ status: "denied", role });
        return;
      }

      setRole(pageRole);
      setState({ status: "granted", role });
    })();

    return () => {
      cancelled = true;
    };
  }, [pageRole, pathname, epoch]);

  return state;
}
