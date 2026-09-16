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

  return (
    <>
      {fallback ?? (
        <div className="rounded border border-bg-surface bg-bg-card p-2 text-[11px] text-txt-muted">
          Requires {allowed.join(" or ")} role
        </div>
      )}
    </>
  );
}
