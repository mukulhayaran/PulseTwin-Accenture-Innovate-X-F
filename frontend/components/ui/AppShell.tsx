"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  getSession, getShiftSummary, logout, roleAtLeast, type Role, type ShiftSummary,
} from "@/lib/api";
import { ROUTE_POLICIES, canEnter } from "@/lib/routeAccess";
import { cn } from "@/lib/cn";
import OfflineBanner from "@/components/shared/OfflineBanner";
import Select from "./Select";
import { TooltipProvider } from "./Tooltip";
import { Popover, PopoverContent, PopoverTrigger } from "./Popover";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger,
} from "./DropdownMenu";
import { Sheet, SheetContent, SheetTitle } from "./Sheet";
import {
  IconAlert, IconBell, IconChart, IconChevronDown, IconGear, IconHome, IconLock,
  IconLogout, IconSearch, IconSparkle, IconTarget, IconUser,
} from "./icons";

// The four persona pages each hand-rolled the same header: wordmark, persona label,
// plant <select>, nav links, role chip. This is that header once.
//
// It does NOT own the role. Each view sets its own via setRole() on mount, and /ops
// deliberately keeps a live selector so a demo can drop privilege and watch RoleGate
// close panels (CLAUDE.md §7) -- so `status` takes arbitrary controls rather than the
// shell assuming one role model for everybody. The `role` prop is therefore the PAGE's
// acting persona; the SESSION's role (which decides what the rail may link to) is read
// separately from lib/api.ts, because on /ops and /copilot those two differ on purpose.
//
// ADR-047 rebuilt the chrome as a dark rail + light top bar without changing the prop
// contract, so all four persona pages mount it unedited. The rail is the system's ONE
// dark band (BMW: alternate light and dark, never two of a kind in a row) and it is
// what keeps a white-on-white dashboard from reading as a document.

// Order and labels come from lib/routeAccess.ts so the rail and the route guard can
// never disagree; the icons are the shell's own business. A link the session may not
// open renders as a lock rather than vanishing -- a view you cannot reach is information
// about the system, and silently hiding it makes the app look like it has fewer parts
// than it does.
const VIEW_ICONS: Record<string, typeof IconHome> = {
  "/overview": IconHome,
  "/supervisor": IconUser,
  "/manager": IconChart,
  "/leadership": IconTarget,
  "/ops": IconGear,
  "/copilot": IconSparkle,
};

const ROLE_TONE: Record<Role, string> = {
  supervisor: "text-ok-fg",
  manager: "text-info-fg",
  leadership: "text-ai-fg",
  system: "text-warn-fg",
};

// The demo accounts behind serve/routers/auth_demo.py -- one fixed account per role,
// not a user directory. Named rather than shown as "demo-manager" because the chip is
// the one place the UI has to look like a real product; the menu says what it is.
const DEMO_USER: Record<Role, string> = {
  supervisor: "Jordan Malik",
  manager: "Mukul Hayaran",
  leadership: "Rishabh Yadav",
  system: "Prashant Mishra",
};

export const PLANT_OPTIONS = [
  { value: "plant_a", label: "Plant A — Midwest Assembly" },
];

// ---------------------------------------------------------------- notification tray

interface Notice {
  tone: "danger" | "warn" | "info";
  title: string;
  detail: string;
  href: string;
}

/**
 * Derived from the shift summary, not invented. Every entry is a condition the API
 * actually reported, and the count on the bell is the number of them -- a badge that
 * says "3" when nothing is wrong is the fastest way to teach an operator to ignore it.
 */
function noticesFrom(s: ShiftSummary | null): Notice[] {
  if (!s) return [];
  const out: Notice[] = [];

  if (s.sensors.dropouts_active > 0) {
    out.push({
      tone: "warn",
      title: `${s.sensors.dropouts_active} sensor dropout${s.sensors.dropouts_active === 1 ? "" : "s"} active`,
      detail:
        s.sensors.stations_affected.length > 0
          ? `Stations ${s.sensors.stations_affected.map((n) => `S${n}`).join(", ")} — values there are inferred, not measured.`
          : "Affected values are inferred, not measured.",
      href: "/ops",
    });
  }

  if (s.model.drift_metrics_breached > 0) {
    out.push({
      tone: "warn",
      title: `${s.model.drift_metrics_breached} drift threshold${s.model.drift_metrics_breached === 1 ? "" : "s"} breached`,
      detail:
        s.model.breached
          .slice(0, 3)
          .map((b) => `${b.feature} (${b.metric_type})`)
          .join(", ") || "See the drift monitor for the affected features.",
      href: "/ops",
    });
  }

  const { alerts_fired: fired, alert_budget: budget } = s.production;
  if (budget > 0 && fired >= budget) {
    out.push({
      tone: "danger",
      title: "Alert budget exhausted",
      detail: `${fired} of ${budget} alerts fired this shift. Further alerts compete for the same review capacity.`,
      href: "/manager",
    });
  }

  if (s.bottleneck.shift_events > 0) {
    out.push({
      tone: "info",
      title: `Constraint moved ${s.bottleneck.shift_events}×`,
      detail:
        s.bottleneck.closed_at_station != null
          ? `Currently at S${s.bottleneck.closed_at_station}, over ${s.bottleneck.windows_in_shift} windows this shift.`
          : `Across ${s.bottleneck.windows_in_shift} windows this shift.`,
      href: "/manager",
    });
  }

  return out;
}

const NOTICE_TONE: Record<Notice["tone"], string> = {
  danger: "text-danger-fg",
  warn: "text-warn-fg",
  info: "text-info-fg",
};

function NotificationTray({ notices }: { notices: Notice[] }) {
  const [open, setOpen] = useState(false);

  // Radix Popover (ADR-075): outside-click, Esc, focus return and viewport collision.
  // The hand-rolled version handled the first two and could open off-screen.
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        aria-label={`Notifications${notices.length ? ` (${notices.length})` : ""}`}
        className={cn(
          "relative flex size-9 items-center justify-center border border-transparent text-ink-dim",
          "transition-colors duration-150 hover:border-line-strong hover:text-ink"
        )}
      >
        <IconBell size={18} />
        {notices.length > 0 && (
          <span
            className={cn(
              "text-numeric absolute -right-0.5 -top-0.5 flex size-4 items-center justify-center",
              "rounded-full bg-danger text-[9px] font-bold leading-none text-on-primary"
            )}
          >
            {notices.length}
          </span>
        )}
      </PopoverTrigger>

      <PopoverContent className="w-80 border-line-strong">
          <div className="border-b border-line px-4 py-2.5 text-2xs font-bold uppercase tracking-[0.115em] text-ink-faint">
            Needs attention
          </div>
          {notices.length === 0 ? (
            <p className="px-4 py-5 text-xs text-ink-faint">
              Nothing open. Sensor coverage, drift thresholds and the alert budget are all
              within limits this shift.
            </p>
          ) : (
            <ul>
              {notices.map((n) => (
                <li key={n.title} className="border-b border-line last:border-0">
                  <Link
                    href={n.href}
                    onClick={() => setOpen(false)}
                    className="flex gap-2.5 px-4 py-3 transition-colors hover:bg-surface-2"
                  >
                    <IconAlert size={14} className={cn("mt-0.5 shrink-0", NOTICE_TONE[n.tone])} />
                    <span className="min-w-0">
                      <span className="block text-xs font-bold text-ink">{n.title}</span>
                      <span className="mt-0.5 block text-2xs leading-relaxed text-ink-faint">
                        {n.detail}
                      </span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
      </PopoverContent>
    </Popover>
  );
}

// ------------------------------------------------------------------------ user chip

function UserChip({ role, sessionRole }: { role: Role; sessionRole: Role | null }) {
  const [busy, setBusy] = useState(false);

  const signOut = async () => {
    setBusy(true);
    // Revokes the token server-side, not just here -- otherwise "sign out" would leave a
    // working credential in sessionStorage and only pretend to end the session.
    await logout();
    setBusy(false);
    // A restricted page re-renders its SignInGate on SESSION_EVENT; nowhere to navigate.
  };

  // Anonymous gets a plain "not signed in", not a fabricated identity. There is no login
  // page to link to: each restricted view asks for sign-in in place (SignInGate).
  if (!sessionRole) {
    return (
      <span className="flex items-center gap-2 px-3 py-1.5 text-xs text-ink-faint" title="Not signed in">
        <IconLock size={13} />
        <span className="hidden sm:inline">Not signed in</span>
        <span className="sr-only sm:hidden">Not signed in</span>
      </span>
    );
  }

  const name = DEMO_USER[sessionRole];
  const initials = name
    .split(" ")
    .map((w) => w[0])
    .join("");

  // Radix DropdownMenu (ADR-075): arrow keys, typeahead, Esc and focus return, with
  // role="menu" semantics the hand-rolled panel only claimed via aria-haspopup.
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={`Account: ${name}, ${sessionRole}`}
        className={cn(
          "flex items-center gap-2.5 border border-transparent py-1 pl-1 pr-2 text-left",
          "transition-colors duration-150 hover:border-line-strong"
        )}
      >
        <span
          aria-hidden
          className="text-numeric flex size-8 shrink-0 items-center justify-center rounded-full bg-surface-dark text-2xs font-bold text-on-dark"
        >
          {initials}
        </span>
        <span className="hidden min-w-0 leading-tight sm:block">
          <span className="block truncate text-xs font-bold text-ink">{name}</span>
          <span className={cn("block truncate text-2xs capitalize", ROLE_TONE[sessionRole])}>
            {sessionRole}
          </span>
        </span>
        <IconChevronDown size={14} className="shrink-0 text-ink-faint" />
      </DropdownMenuTrigger>

      <DropdownMenuContent className="w-72 border-line-strong py-0">
        <DropdownMenuLabel className="border-b border-line px-4 py-3">
          <span className="block text-xs font-bold text-ink">{name}</span>
          <span className={cn("mt-0.5 block text-2xs capitalize", ROLE_TONE[sessionRole])}>
            {sessionRole}
            {sessionRole !== role && (
              <span className="text-ink-faint"> · viewing as {role}</span>
            )}
          </span>
          <span className="mt-2 block text-2xs leading-relaxed text-ink-faint">
            A fixed demo account per role, validated server-side — not a user directory.
            Signing in mints a session token the API resolves to this role on every
            request, and signing out revokes it.
          </span>
        </DropdownMenuLabel>
        <DropdownMenuItem
          onSelect={signOut}
          disabled={busy}
          className="px-4 py-3 text-xs text-ink-dim data-[highlighted]:text-ink"
        >
          <IconLogout size={14} />
          {busy ? "Signing out…" : "Sign out"}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// -------------------------------------------------------------------------- the rail

function Rail({
  role,
  sessionRole,
  connected,
  onNavigate,
}: {
  /** The page's acting persona — what the sign-off at the foot of the rail reports. */
  role: Role;
  /** The signed-in role, or null when anonymous — what decides which links open. */
  sessionRole: Role | null;
  connected: boolean;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();

  return (
    <div className="relative flex h-full flex-col bg-[linear-gradient(180deg,#f8fafd,#f3f6fb_40%,#edf2f9)] shadow-[inset_-1px_0_0_var(--color-line)]">
      <div aria-hidden className="tex-grain pointer-events-none absolute inset-0" />
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-72 bg-[radial-gradient(120%_80%_at_0%_0%,rgb(28_105_212/0.10),transparent_70%)]" />
      <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 h-48 bg-[radial-gradient(100%_80%_at_100%_100%,rgb(124_58_237/0.06),transparent_70%)]" />
      <Link
        href="/"
        onClick={onNavigate}
        className="relative block px-5 pb-6 pt-5 transition-opacity hover:opacity-90"
      >
        <span className="flex items-center gap-2.5">
          <span aria-hidden className="flex items-end gap-[3px]">
            <span className="block h-3 w-[3px] bg-info-on-dark" />
            <span className="block h-5 w-[3px] bg-on-dark" />
            <span className="block h-2.5 w-[3px] bg-info-on-dark" />
            <span className="block h-4 w-[3px] bg-on-dark" />
          </span>
          <span className="text-lg font-bold tracking-normal text-on-dark">PulseTwin</span>
        </span>
        <span className="mt-2 block text-2xs leading-relaxed text-on-dark-soft">
          Assembly-line digital twin
        </span>
      </Link>

      <nav className="relative flex-1 px-3" aria-label="Views">
        <ul className="space-y-0.5">
          {ROUTE_POLICIES.filter((p) => !p.hideFromRail).map(({ href, label, requires }) => {
            const Icon = VIEW_ICONS[href] ?? IconHome;
            const active = pathname === href;
            const open = canEnter(href, sessionRole);

            // Locked: still a link -- the view itself asks for sign-in as its own role
            // (SignInGate), so a click goes straight to that form. The lock says so first.
            if (!open) {
              return (
                <li key={href}>
                  <Link
                    href={href}
                    onClick={onNavigate}
                    aria-current={active ? "page" : undefined}
                    title={`Sign in as ${requires?.join(" or ")} to open this view.`}
                    className="group relative flex items-center gap-3 px-3 py-2.5 text-sm text-on-dark/45 transition-colors duration-200 hover:bg-on-dark/[0.035] hover:text-on-dark/60"
                  >
                    <Icon size={17} className="shrink-0" />
                    {label}
                    <IconLock size={12} className="ml-auto shrink-0 transition-colors duration-200 group-hover:text-on-dark-soft" />
                  </Link>
                </li>
              );
            }

            return (
              <li key={href}>
                <Link
                  href={href}
                  onClick={onNavigate}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "group relative flex items-center gap-3 px-3 py-2.5 text-sm transition-colors duration-200",
                    active
                      ? "bg-[linear-gradient(90deg,rgb(28_105_212/0.10),rgb(28_105_212/0.02))] font-bold text-on-dark"
                      : "text-on-dark-soft hover:bg-on-dark/[0.035] hover:text-on-dark"
                  )}
                >
                  {active && (
                    <span aria-hidden className="bar-in absolute inset-y-0 left-0 w-[3px] bg-info-on-dark" />
                  )}
                  <Icon
                    size={17}
                    className={cn(
                      "shrink-0 transition-[color,transform] duration-200",
                      active ? "text-info-on-dark" : "motion-safe:group-hover:translate-x-px"
                    )}
                  />
                  {label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="relative px-5 pb-5 pt-6">
        <div className="flex items-center justify-between gap-2 border-t border-line-on-dark pt-3">
          <span className="text-numeric text-2xs text-on-dark-soft">v0.4.0</span>
          <span
            className={cn(
              "flex items-center gap-1.5 text-2xs",
              connected ? "text-ok-on-dark" : "text-danger-on-dark"
            )}
          >
            <span className={cn("size-1.5 rounded-full bg-current", connected && "dot-pulse")} />
            {connected ? "All systems online" : "Twin unreachable"}
          </span>
        </div>
        <div className="mt-3 text-2xs text-on-dark-soft">
          {sessionRole ? (
            <>
              Signed in as <span className="font-bold capitalize text-on-dark">{sessionRole}</span>
              {/* /ops and /copilot deliberately act as a role below the session's; saying
                  so is the difference between a demo and a bug report. */}
              {sessionRole !== role && (
                <> · viewing as <span className="font-bold capitalize text-on-dark">{role}</span></>
              )}
            </>
          ) : (
            <>
              Not signed in · restricted views ask for sign-in
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------ the shell

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
  /** e.g. "Analysis" — rendered as the page's own title in the sub-bar. */
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
  const router = useRouter();
  const pathname = usePathname();
  const [shift, setShift] = useState<ShiftSummary | null>(null);
  const [query, setQuery] = useState("");
  const [railOpen, setRailOpen] = useState(false);
  // Read after mount, never during render: the session lives in sessionStorage, which a
  // prerendered static export cannot see, so reading it in the render body would make the
  // server HTML and the first client render disagree (hydration #418). Null until then,
  // which renders the signed-out rail for one frame -- the page guard itself has not
  // resolved at that point either, so nothing gated is on screen yet.
  const [sessionRole, setSessionRole] = useState<Role | null>(null);
  useEffect(() => setSessionRole(getSession()?.role ?? null), [role]);
  // Rendered only after mount: a server-rendered clock and a client one disagree by
  // however long the static export sat on disk, which React reports as a hydration
  // mismatch and repairs by re-rendering the subtree.
  const [now, setNow] = useState<Date | null>(null);

  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    let cancelled = false;
    // The shift window and the notice list are both real API state. /api/shift/summary
    // is manager-gated, so don't ASK when this viewer is below that: the catch below
    // already handled the 403 correctly, but issuing a request we know will be refused
    // painted a red error into the console of the one view that is public and is where
    // the demo starts. Not asking is the same outcome without the false alarm.
    if (!roleAtLeast(role, "manager")) {
      setShift(null);
      return;
    }
    getShiftSummary(plant)
      .then((s) => !cancelled && setShift(s))
      .catch(() => !cancelled && setShift(null));
    return () => {
      cancelled = true;
    };
  }, [plant, connected, role]);

  const notices = noticesFrom(shift);

  const submitSearch = () => {
    const q = query.trim();
    if (!q) return;
    // Exactly two destinations, both of which exist. A unit id opens that unit on the
    // floor view; everything else is a question, and questions go to the copilot as a
    // DRAFT it does not send by itself.
    //
    // There is deliberately no "station" branch: a station has no detail view to land
    // on, and a search box that silently does nothing is worse than one that hands the
    // query to something which can actually answer it. "S19" reaches the copilot as
    // prose -- which is also why the copilot never parses a station back out of it
    // (CLAUDE.md §1); its what-if action takes the station structurally instead.
    if (/^u\d{4,8}$/i.test(q)) {
      // Already on the floor view: router.push to the same route neither remounts the
      // page nor fires `hashchange`, so the unit never opened. Setting the hash does.
      if (pathname === "/supervisor") window.location.hash = `unit=${q.toUpperCase()}`;
      else router.push(`/supervisor#unit=${q.toUpperCase()}`);
    } else {
      router.push(`/copilot#q=${encodeURIComponent(q)}`);
    }
    setQuery("");
  };

  return (
    <TooltipProvider>
      <div className="app-canvas min-h-screen text-ink-dim">
        {/* ---------------------------------------------------------- the rail */}
        <aside className="fixed inset-y-0 left-0 z-40 hidden w-60 overflow-y-auto lg:block">
          <Rail role={role} sessionRole={sessionRole} connected={connected} />
        </aside>

        {/* Mobile nav as a Radix Sheet (ADR-075): focus trap, Esc, scroll lock and focus
            back on the menu button -- the hand-rolled overlay had none of them. */}
        <Sheet open={railOpen} onOpenChange={setRailOpen}>
          <SheetContent side="left" showCloseButton={false} className="w-60 border-r-0 lg:hidden">
            <SheetTitle className="sr-only">Navigation</SheetTitle>
            <Rail
              role={role}
              sessionRole={sessionRole}
              connected={connected}
              onNavigate={() => setRailOpen(false)}
            />
          </SheetContent>
        </Sheet>

        <div className="flex min-h-screen flex-col lg:pl-60">
          <OfflineBanner connected={connected} lastKnownAt={lastKnownAt} onRetry={onRetry} />

          {/* ------------------------------------------------------- top bar */}
          <header className="sticky top-0 z-30 border-b border-[rgb(15_23_42/0.07)] bg-canvas/80 backdrop-blur-xl">
            <div className="flex h-16 items-center gap-3 px-4 lg:px-6">
              <button
                type="button"
                onClick={() => setRailOpen(true)}
                aria-label="Open navigation"
                className="flex size-9 shrink-0 items-center justify-center border border-line-strong text-ink lg:hidden"
              >
                <span aria-hidden className="space-y-1">
                  <span className="block h-px w-4 bg-current" />
                  <span className="block h-px w-4 bg-current" />
                  <span className="block h-px w-4 bg-current" />
                </span>
              </button>

              <Select
                value={plant}
                options={PLANT_OPTIONS}
                onChange={onPlantChange}
                ariaLabel="Plant"
                size="md"
                className="shrink-0"
              />

              {shift && (
                <span className="hidden shrink-0 items-center gap-2 border-l border-line pl-3 text-2xs text-ink-faint xl:flex">
                  <span
                    className={cn(
                      "size-1.5 rounded-full",
                      connected ? "bg-ok" : "bg-ink-disabled"
                    )}
                  />
                  <span className="font-bold text-ink">{shift.shift.label}</span>
                  <span className="text-numeric">
                    {fmtWindow(shift.shift.window_start, shift.shift.window_end)}
                  </span>
                </span>
              )}

              <form
                role="search"
                onSubmit={(e) => {
                  e.preventDefault();
                  submitSearch();
                }}
                className="relative mx-auto hidden w-full max-w-xl md:block"
              >
                <IconSearch
                  size={15}
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint"
                />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Open a unit by id (U029629), or ask the Copilot…"
                  aria-label="Open a unit by id, or ask the Copilot"
                  className={cn(
                    "h-10 w-full border border-line-strong bg-canvas pl-9 pr-3 text-xs text-ink",
                    "placeholder:text-ink-faint transition-[border-color,box-shadow] duration-200",
                    "hover:border-ink focus:border-primary focus:shadow-[0_0_0_3px_rgb(28_105_212/0.12)] focus:outline-none"
                  )}
                />
              </form>

              <div className="ml-auto flex shrink-0 items-center gap-1">
                <NotificationTray notices={notices} />
                <UserChip role={role} sessionRole={sessionRole} />
              </div>
            </div>
          </header>

          {/* ------------------------------------------------------ sub-bar */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-[rgb(15_23_42/0.07)] bg-canvas/55 px-4 py-2.5 backdrop-blur-sm lg:px-6">
            <h1 className="shrink-0 text-xl font-bold text-ink">
              {persona}
            </h1>
            {controls}
            <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-2">
              {status}
              <span className="text-numeric hidden shrink-0 text-2xs text-ink-faint 2xl:inline">
                {now
                  ? now.toLocaleDateString(undefined, {
                      weekday: "short",
                      month: "short",
                      day: "numeric",
                      year: "numeric",
                    })
                  : ""}
              </span>
            </div>
          </div>

          {/* pb-20 keeps the last panel scrollable clear of the fixed copilot dock */}
          <main key={pathname} className="page-in flex-1 p-4 pb-20 lg:p-6 lg:pb-20">{children}</main>
        </div>
      </div>
    </TooltipProvider>
  );
}

/** "2:00 PM – 10:00 PM" from the shift window the API reported. */
function fmtWindow(start: string, end: string): string {
  const f = (iso: string) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime())
      ? "—"
      : d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  };
  return `${f(start)} – ${f(end)}`;
}
