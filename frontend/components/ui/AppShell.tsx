"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
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
  manager: "Priya Raman",
  leadership: "Alex Whitfield",
  system: "Sam Okafor",
};

export const PLANT_OPTIONS = [
  { value: "plant_a", label: "Plant A — Midwest Assembly" },
  { value: "plant_b", label: "Plant B — Southern Truck" },
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
  const ref = useRef<HTMLDivElement>(null);

  // Plain outside-click rather than @radix-ui/react-popover: the tray is the only
  // popover in the app that Radix does not already cover, and this frontend ships as
  // a static export onto a plant network, where a dependency costs more than 20 lines.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
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
      </button>

      {open && (
        <div className="absolute right-0 top-11 z-50 w-80 border border-line-strong bg-canvas shadow-pop">
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
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------ user chip

function UserChip({ role, sessionRole }: { role: Role; sessionRole: Role | null }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const signOut = async () => {
    setBusy(true);
    // Revokes the token server-side, not just here -- otherwise "sign out" would leave a
    // working credential in sessionStorage and only pretend to end the session.
    await logout();
    setOpen(false);
    setBusy(false);
    router.replace("/login");
  };

  // Anonymous gets the sign-in affordance, not a fabricated identity. Showing a named
  // account for someone who never signed in is the one thing a chip like this must not do.
  if (!sessionRole) {
    return (
      <Link
        href="/login"
        className={cn(
          "flex items-center gap-2 border border-line-strong px-3 py-1.5 text-xs font-bold text-ink",
          "transition-colors duration-150 hover:bg-surface-2 hover:border-ink"
        )}
      >
        <IconLock size={13} className="text-ink-faint" />
        Sign in
      </Link>
    );
  }

  const name = DEMO_USER[sessionRole];
  const initials = name
    .split(" ")
    .map((w) => w[0])
    .join("");

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
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
      </button>

      {open && (
        <div className="absolute right-0 top-12 z-50 w-72 border border-line-strong bg-canvas shadow-pop">
          <div className="border-b border-line px-4 py-3">
            <div className="text-xs font-bold text-ink">{name}</div>
            <div className={cn("mt-0.5 text-2xs capitalize", ROLE_TONE[sessionRole])}>
              {sessionRole}
              {sessionRole !== role && (
                <span className="text-ink-faint"> · viewing as {role}</span>
              )}
            </div>
            <p className="mt-2 text-2xs leading-relaxed text-ink-faint">
              A fixed demo account per role, validated server-side — not a user directory.
              Signing in mints a session token the API resolves to this role on every
              request, and signing out revokes it.
            </p>
          </div>
          <button
            type="button"
            onClick={signOut}
            disabled={busy}
            className="flex w-full items-center gap-2 px-4 py-3 text-left text-xs text-ink-dim transition-colors hover:bg-surface-2 hover:text-ink disabled:opacity-50"
          >
            <IconLogout size={14} />
            {busy ? "Signing out…" : "Sign out"}
          </button>
        </div>
      )}
    </div>
  );
}

// -------------------------------------------------------------------------- the rail

/** The plant outline under the rail's sign-off. Decorative; marked aria-hidden. */
function RailSkyline() {
  return (
    <svg
      viewBox="0 0 220 76"
      className="w-full text-on-dark/12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.2"
      aria-hidden
    >
      <path d="M0 70h220" />
      <path d="M8 70V44l16-9v9l16-9v35z" />
      <path d="M46 70V30l14-8v48z" />
      <path d="M64 70V38l18-10v9l18-10v43z" />
      <path d="M104 70V22l12-7v55z" />
      <path d="M120 70V46l20-11v35z" />
      <path d="M144 70V34l16-9v45z" />
      <path d="M164 70V40l22-12v42z" />
      <path d="M190 70V50l14-8v28z" />
      <path d="M30 35v-9M32 24a3 3 0 1 0-4 0" />
      <path d="M110 15V6M112 4a3 3 0 1 0-4 0" />
      <path d="M172 28v-8M174 18a3 3 0 1 0-4 0" />
    </svg>
  );
}

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
    <div className="flex h-full flex-col bg-surface-dark">
      <Link
        href="/"
        onClick={onNavigate}
        className="block px-5 pb-6 pt-5 transition-opacity hover:opacity-90"
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
          Digital Twin for
          <br />a Smarter Tomorrow
        </span>
      </Link>

      <nav className="flex-1 px-3" aria-label="Views">
        <ul className="space-y-0.5">
          {ROUTE_POLICIES.map(({ href, label, requires }) => {
            const Icon = VIEW_ICONS[href] ?? IconHome;
            const active = pathname === href;
            const open = canEnter(href, sessionRole);

            // Locked: still listed, never a link. Clicking it would land on the guard,
            // which either bounces to /login or shows AccessRestricted -- so the rail
            // says which it would be up front instead of making the user find out.
            if (!open) {
              return (
                <li key={href}>
                  <span
                    title={
                      sessionRole
                        ? `Signed in as ${sessionRole}. This view is for ${requires?.join(" or ")}.`
                        : `Sign in as ${requires?.join(" or ")} to open this view.`
                    }
                    aria-disabled
                    className="relative flex cursor-not-allowed items-center gap-3 px-3 py-2.5 text-sm text-on-dark/35"
                  >
                    <Icon size={17} className="shrink-0" />
                    {label}
                    <IconLock size={12} className="ml-auto shrink-0" />
                  </span>
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
                    "relative flex items-center gap-3 px-3 py-2.5 text-sm transition-colors duration-150",
                    active
                      ? "bg-on-dark/8 font-bold text-on-dark"
                      : "text-on-dark-soft hover:bg-on-dark/5 hover:text-on-dark"
                  )}
                >
                  {active && (
                    <span aria-hidden className="absolute inset-y-0 left-0 w-[3px] bg-info-on-dark" />
                  )}
                  <Icon size={17} className="shrink-0" />
                  {label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="px-5 pb-5 pt-6">
        <p className="text-sm font-bold leading-snug text-on-dark">
          Build Better.
          <br />
          Faster.
          <br />
          Together.
        </p>
        <div className="mt-4">
          <RailSkyline />
        </div>
        <div className="mt-4 flex items-center justify-between gap-2 border-t border-line-on-dark pt-3">
          <span className="text-numeric text-2xs text-on-dark-soft">v0.4.0</span>
          <span
            className={cn(
              "flex items-center gap-1.5 text-2xs",
              connected ? "text-ok-on-dark" : "text-danger-on-dark"
            )}
          >
            <span className="size-1.5 rounded-full bg-current" />
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
              Not signed in ·{" "}
              <Link href="/login" className="font-bold text-on-dark underline-offset-2 hover:underline">
                Sign in
              </Link>
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
      router.push(`/supervisor#unit=${q.toUpperCase()}`);
    } else {
      router.push(`/copilot#q=${encodeURIComponent(q)}`);
    }
    setQuery("");
  };

  return (
    <TooltipProvider>
      <div className="min-h-screen bg-canvas text-ink-dim">
        {/* ---------------------------------------------------------- the rail */}
        <aside className="fixed inset-y-0 left-0 z-40 hidden w-60 overflow-y-auto lg:block">
          <Rail role={role} sessionRole={sessionRole} connected={connected} />
        </aside>

        {railOpen && (
          <div className="fixed inset-0 z-50 lg:hidden">
            <button
              aria-label="Close navigation"
              onClick={() => setRailOpen(false)}
              className="absolute inset-0 bg-surface-dark/60"
            />
            <aside className="absolute inset-y-0 left-0 w-60 overflow-y-auto">
              <Rail
                role={role}
                sessionRole={sessionRole}
                connected={connected}
                onNavigate={() => setRailOpen(false)}
              />
            </aside>
          </div>
        )}

        <div className="flex min-h-screen flex-col lg:pl-60">
          <OfflineBanner connected={connected} lastKnownAt={lastKnownAt} onRetry={onRetry} />

          {/* ------------------------------------------------------- top bar */}
          <header className="sticky top-0 z-30 border-b border-line bg-canvas">
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
                    "placeholder:text-ink-faint transition-colors",
                    "hover:border-ink focus:border-ink focus:outline-none"
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
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line px-4 py-2.5 lg:px-6">
            <h1 className="shrink-0 text-sm font-bold uppercase tracking-[0.115em] text-ink">
              {persona}
            </h1>
            {controls}
            <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-2">
              {status}
              <span className="text-numeric shrink-0 text-2xs text-ink-faint">
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

          <main className="flex-1 p-4 lg:p-6">{children}</main>
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
