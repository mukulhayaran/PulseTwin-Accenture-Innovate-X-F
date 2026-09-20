"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { Role } from "@/lib/api";
import { cn } from "@/lib/cn";
import OfflineBanner from "@/components/shared/OfflineBanner";
import Select from "./Select";
import { TooltipProvider } from "./Tooltip";

// The four persona pages each hand-rolled the same header: wordmark, persona label,
// plant <select>, nav links, role chip. This is that header once.
//
// It does NOT own the role. Each view sets its own via setRole() on mount, and /ops
// deliberately keeps a live selector so a demo can drop privilege and watch RoleGate
// close panels (CLAUDE.md §7) -- so `status` takes arbitrary controls rather than the
// shell assuming one role model for everybody.

const VIEWS = [
  { href: "/supervisor", label: "Supervisor" },
  { href: "/manager", label: "Manager" },
  { href: "/leadership", label: "Leadership" },
  { href: "/ops", label: "Operations" },
  { href: "/copilot", label: "Copilot" },
] as const;

const ROLE_TONE: Record<Role, string> = {
  supervisor: "text-ok-fg",
  manager: "text-info-fg",
  leadership: "text-ai-fg",
  system: "text-warn-fg",
};

export const PLANT_OPTIONS = [
  { value: "plant_a", label: "Plant A — Midwest Assembly" },
  { value: "plant_b", label: "Plant B — Southern Truck" },
];

export default function AppShell({
  persona,
  plant,
  onPlantChange,
  role,
  connected,
  lastKnownAt,
  onRetry,
  controls,
  status,
  children,
}: {
  /** e.g. "Analysis" — rendered after the wordmark. */
  persona: string;
  plant: string;
  onPlantChange: (p: string) => void;
  role: Role;
  connected: boolean;
  lastKnownAt: string | null;
  onRetry: () => void;
  /** Left cluster, after the plant picker: speed, scenario, budget. */
  controls?: ReactNode;
  /** Right cluster, before the nav: clock, connection state, staleness. */
  status?: ReactNode;
  children: ReactNode;
}) {
  const pathname = usePathname();

  return (
    <TooltipProvider>
      <div className="flex min-h-screen flex-col bg-canvas text-ink">
        <OfflineBanner connected={connected} lastKnownAt={lastKnownAt} onRetry={onRetry} />

        <header className="sticky top-0 z-40 border-b border-line bg-canvas/85 backdrop-blur-md">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5">
            <Link
              href="/"
              className="flex shrink-0 items-baseline gap-2 rounded-md transition-opacity hover:opacity-80"
            >
              <span className="text-sm font-semibold tracking-tight text-ink">PulseTwin</span>
              <span className="text-2xs font-medium uppercase tracking-[0.16em] text-ink-faint">
                {persona}
              </span>
            </Link>

            <span aria-hidden className="h-4 w-px shrink-0 bg-line" />

            <Select
              value={plant}
              options={PLANT_OPTIONS}
              onChange={onPlantChange}
              ariaLabel="Plant"
            />

            {controls}

            <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-2">
              {status}

              <nav className="flex items-center gap-0.5" aria-label="Persona views">
                {VIEWS.filter((v) => v.href !== pathname).map((v) => (
                  <Link
                    key={v.href}
                    href={v.href}
                    className={cn(
                      "rounded-md px-2 py-1 text-2xs font-medium text-ink-faint",
                      "transition-colors duration-150 hover:bg-surface-2 hover:text-ink"
                    )}
                  >
                    {v.label}
                  </Link>
                ))}
              </nav>

              <span
                className="flex shrink-0 items-center gap-1.5 border-l border-line pl-3 text-2xs text-ink-faint"
                title="Server-enforced role — see docs/SECURITY.md"
              >
                Role
                <span className={cn("font-semibold uppercase tracking-wider", ROLE_TONE[role])}>
                  {role}
                </span>
              </span>
            </div>
          </div>
        </header>

        <main className="flex-1 p-4">{children}</main>
      </div>
    </TooltipProvider>
  );
}
