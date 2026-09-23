"use client";

// Full-page counterpart to RoleGate.tsx's small in-panel lock card, for when an entire
// persona page is off-limits to the role /login left in place, not just one widget on it.
// Same honesty rule applies: this is what the UI does when the role is insufficient, not
// what stops someone hitting the API directly with a different header -- the server-side
// require_role() calls are the actual enforcement (docs/SECURITY.md).

import Link from "next/link";
import type { Role } from "@/lib/api";

export default function AccessRestricted({ minRole }: { minRole: Role }) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-canvas px-5 text-center">
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
        <p className="text-sm font-semibold text-ink">This view requires the {minRole} role or higher</p>
        <p className="mx-auto mt-1.5 max-w-sm text-xs leading-relaxed text-ink-faint">
          Signed in at a lower role. Choose a different role to continue — the API would
          refuse this page&apos;s data at your current role regardless of what renders here.
        </p>
      </div>
      <Link
        href="/login"
        className="rounded-md border border-line-strong bg-surface-2 px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-surface-3"
      >
        Choose a role
      </Link>
    </main>
  );
}
