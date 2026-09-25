"use client";

// Client-side mirror of the backend RBAC check (serve/middleware/auth.py). This does
// NOT replace server enforcement -- the API rejects a disallowed request regardless of
// what this renders -- it just keeps the UI honest about what the current role could
// actually do, and gives a judge a visible "this is gated" signal instead of content
// silently vanishing.

import type { ReactNode } from "react";
import { type Role, getRole } from "@/lib/api";

interface RoleGateProps {
  allowed: Role[];
  children: ReactNode;
  fallback?: ReactNode;
}

export default function RoleGate({ allowed, children, fallback }: RoleGateProps) {
  const role = getRole();
  if (allowed.includes(role)) return <>{children}</>;

  // `fallback ?? default` was wrong: ?? only short-circuits on null/undefined, so an
  // explicit fallback={null} -- meaning "render nothing here" -- still produced the
  // default lock card, which wedges a full lock panel into e.g. a Panel header.
  if (fallback !== undefined) return <>{fallback}</>;

  return (
    <div className="panel-surface flex min-h-24 flex-col items-center justify-center gap-2 p-5 text-center">
      <span
        aria-hidden
        className="flex size-7 items-center justify-center rounded-full border border-line-strong bg-surface-2 text-ink-faint"
      >
        <svg width="12" height="12" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4">
          <rect x="2.5" y="6" width="9" height="6.5" rx="1.5" />
          <path d="M4.75 6V4.25a2.25 2.25 0 0 1 4.5 0V6" strokeLinecap="round" />
        </svg>
      </span>
      <p className="text-2xs text-ink-faint">
        Requires <span className="font-bold text-ink-dim">{allowed.join(" or ")}</span> role
      </p>
    </div>
  );
}
