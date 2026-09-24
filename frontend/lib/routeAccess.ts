// Which signed-in role may OPEN which view. One table, so the route guard
// (usePersonaAccess) and the rail's navigation (AppShell) can never disagree about it --
// a lock icon on a link that still opens is worse than no lock icon at all.
//
// This is an EXACT-MATCH policy, deliberately not the role hierarchy in
// serve/middleware/auth.py. Those answer different questions:
//
//   the hierarchy  -- what the API will DO for a role. leadership >= manager, so a
//                     leadership token is allowed to call /api/whatif. Unchanged.
//   this table     -- which persona view a role may ENTER. Manager and leadership are
//                     peers here, not ranks: neither opens the other's view. That is
//                     separation of duties, and a rank comparison cannot express it --
//                     under the old `roleAtLeast` entry check a leadership login walked
//                     straight into /manager, and system walked into all four.
//
// Supervisor is the one view with no requirement at all: the floor view is what the line
// is for, it is the page an unauthenticated visitor lands on, and every endpoint it calls
// is already at the API's own default role. Everything above the floor needs a session.
//
// As always, this is the UI's half. The API enforces its own gate on every request
// regardless of what this lets someone navigate to (docs/SECURITY.md).

import type { Role } from "@/lib/api";

export interface RoutePolicy {
  href: string;
  label: string;
  /** null = public, no session required. Otherwise the exact roles that may enter. */
  requires: Role[] | null;
}

export const ROUTE_POLICIES: RoutePolicy[] = [
  { href: "/overview", label: "Overview", requires: null },
  { href: "/supervisor", label: "Supervisor", requires: null },
  { href: "/manager", label: "Manager", requires: ["manager"] },
  { href: "/leadership", label: "Leadership", requires: ["leadership"] },
  { href: "/ops", label: "Ops", requires: ["system"] },
  // The copilot's own endpoints start at `manager` (serve/routers/triage.py), so a
  // supervisor session could enter the page only to have every call 403. Listing the
  // three roles that can actually use it is more honest than a page that loads empty.
  { href: "/copilot", label: "Copilot", requires: ["manager", "leadership", "system"] },
];

/** Where each role goes after signing in, and where "back to your view" points. */
export const ROLE_HOME: Record<Role, string> = {
  supervisor: "/supervisor",
  manager: "/manager",
  leadership: "/leadership",
  system: "/ops",
};

export function policyFor(href: string): RoutePolicy | undefined {
  return ROUTE_POLICIES.find((p) => p.href === href);
}

/** True when `role` (null = signed out) may open `href`. Unknown routes are public. */
export function canEnter(href: string, role: Role | null): boolean {
  const policy = policyFor(href);
  if (!policy || policy.requires === null) return true;
  return role !== null && policy.requires.includes(role);
}

/**
 * Normalise a `?next=` value into somewhere safe to send the browser.
 *
 * Only same-origin absolute paths off the app root are accepted. `//evil.example` and
 * `https://evil.example` both parse as a path in careless code and would turn the login
 * form into an open redirect -- the one genuinely exploitable bug a "next" parameter
 * invites, and it costs two lines to close.
 */
export function safeNext(next: string | null): string | null {
  if (!next) return null;
  if (!next.startsWith("/") || next.startsWith("//")) return null;
  return next;
}
