"use client";

// The cross-persona landing (ADR-047).
//
// Deliberately NOT gated by usePersonaAccess: the other views each pin themselves to one
// persona, and this one is the page you arrive on before choosing. It keeps whatever role
// the session holds -- "supervisor" when signed out, which is the API's own default for
// an unauthenticated caller -- and degrades section by section, telling the visitor in
// words which panels their role cannot load. That is the same honesty rule RoleGate
// follows, applied to a whole page, and it is why gating its ENTRY would defeat it.
//
// Every figure here is read from an endpoint. Nothing on this page is illustrative:
// where the API has no number, the panel says so rather than showing a plausible one.

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertTriage, Bottleneck, ShiftSummary, StationCfg,
  getAlertTriage, getBottleneck, getHealth, getRole, getShiftSummary, getStationStats,
  getSession, getStations, restoreSession, type Role, type Session,
} from "@/lib/api";
import { canEnter, policyFor } from "@/lib/routeAccess";
import { cn } from "@/lib/cn";
import StaleDataIndicator from "@/components/shared/StaleDataIndicator";
import CopilotDock from "@/components/copilot/CopilotDock";
import {
  AppShell, Badge, Button, DataTable, InferredBadge, Kpi, Panel, Skeleton, SkeletonTable,
  Tooltip, type Column,
  IconAlert, IconArrowRight, IconChart, IconGauge, IconGear, IconShield, IconSparkle,
  IconLock, IconTarget, IconUser,
} from "@/components/ui";

const POLL_MS = 20_000;

interface StationStat {
  station_id: number;
  provenance: "measured" | "inferred";
  util: number | null;
  blocked: number | null;
  starved: number | null;
  down: number | null;
  lost_uph: number | null;
}

// Health bands over measured lost throughput.
//
// RELATIVE, not absolute. A fixed "critical above 4 uph" cut point is a number nobody
// chose: on this plant it painted 24 of 30 stations red, which tells an operator
// nothing and trains them to ignore the colour. Banding by position within the plant's
// own measured distribution always leaves a short critical list, and it travels to a
// second plant with a different cycle time without being re-tuned -- which the config
// swap in CLAUDE.md §1 requires.
//
// Unsensored stations get no band at all: /api/stations/stats reports their figures
// without a confidence interval, so placing them on a scale against measured ones is
// the same laundering the league table below refuses to do.
type Band = "normal" | "watch" | "critical" | "unknown";

interface Bands {
  critical: number;
  watch: number;
}

function bandCuts(measured: StationStat[]): Bands {
  const sorted = measured
    .map((s) => s.lost_uph ?? 0)
    .filter((v) => v > 0)
    .sort((a, b) => a - b);
  if (sorted.length === 0) return { critical: Infinity, watch: Infinity };
  const at = (q: number) => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * q))];
  return { critical: at(0.9), watch: at(0.7) };
}

function band(s: StationStat | undefined, cuts: Bands): Band {
  if (!s || s.lost_uph == null) return "unknown";
  if (s.provenance !== "measured") return "unknown";
  if (s.lost_uph >= cuts.critical) return "critical";
  if (s.lost_uph >= cuts.watch) return "watch";
  return "normal";
}

const BAND_TILE: Record<Band, string> = {
  normal: "border-ok/45 bg-ok/12 text-ok-fg",
  watch: "border-warn/50 bg-warn/15 text-warn-fg",
  critical: "border-danger/50 bg-danger/15 text-danger-fg",
  unknown: "border-line-strong bg-surface-2 text-ink-faint",
};

const BAND_DOT: Record<Band, string> = {
  normal: "bg-ok",
  watch: "bg-warn",
  critical: "bg-danger",
  unknown: "bg-ink-disabled",
};

const BAND_LABEL: Record<Band, string> = {
  normal: "Normal",
  watch: "Watch",
  critical: "Critical",
  unknown: "Not measured",
};

const PERSONAS: {
  href: string;
  title: string;
  body: string;
  Icon: typeof IconUser;
  min: Role;
}[] = [
  {
    href: "/supervisor",
    title: "Supervisor",
    body: "Live line map, the alert feed with its risk drivers, and containment of units sharing production context.",
    Icon: IconUser,
    min: "supervisor",
  },
  {
    href: "/manager",
    title: "Manager",
    body: "Constraint migration over time, the station league table, the defect Pareto and the what-if console.",
    Icon: IconChart,
    min: "manager",
  },
  {
    href: "/leadership",
    title: "Leadership",
    body: "Value derived from measured recall, the alarm-budget trade-off, and where the next sensor dollar pays back.",
    Icon: IconTarget,
    min: "leadership",
  },
  {
    href: "/ops",
    title: "Ops",
    body: "Model health, drift detection, ingestion lag and the audit trail — keeping the twin itself honest.",
    Icon: IconGear,
    min: "manager",
  },
  {
    href: "/copilot",
    title: "Copilot",
    body: "Ask a question, get an investigation whose every claim is re-checked against the API before you see it.",
    Icon: IconSparkle,
    min: "manager",
  },
];

/** A section the current role cannot load. Says which role, not just "no data". */
function RoleNote({ min }: { min: Role }) {
  return (
    <div className="flex flex-col items-start gap-2 py-6">
      <p className="text-xs leading-relaxed text-ink-faint">
        This panel reads an endpoint that requires the <span className="font-bold text-ink">{min}</span>{" "}
        role or higher. The API refuses it at your current role, so nothing is shown rather
        than an estimate.
      </p>
      <Link href="/login">
        <Button variant="link">
          Choose a role
          <IconArrowRight size={13} />
        </Button>
      </Link>
    </div>
  );
}

export default function OverviewPage() {
  const [plant, setPlant] = useState("plant_a");
  const [role, setRoleState] = useState<Role>("supervisor");
  const [session, setSessionState] = useState<Session | null>(null);
  const [connected, setConnected] = useState(true);
  const [lastKnownAt, setLastKnownAt] = useState<string | null>(null);

  const [stations, setStations] = useState<StationCfg[]>([]);
  const [stats, setStats] = useState<StationStat[]>([]);
  const [bn, setBn] = useState<Bottleneck | null>(null);
  const [shift, setShift] = useState<ShiftSummary | null>(null);
  const [triage, setTriage] = useState<AlertTriage | null>(null);
  const [shiftDenied, setShiftDenied] = useState(false);
  const [loaded, setLoaded] = useState(false);

  // Whatever the session holds. This page does not pin a persona -- see the file header
  // -- so it only mirrors the role, it never sets one. restoreSession() first, so a
  // token that expired or was signed out elsewhere drops back to "supervisor" here
  // instead of rendering a role the server would refuse on every call.
  useEffect(() => {
    let cancelled = false;
    restoreSession()
      .catch(() => null)
      .then(() => {
        if (cancelled) return;
        setSessionState(getSession());
        setRoleState(getRole());
      });
    return () => { cancelled = true; };
  }, []);

  const poll = useCallback(async () => {
    try {
      await getHealth();
      setConnected(true);
      setLastKnownAt(new Date().toISOString());
    } catch {
      setConnected(false);
      return;
    }

    getStations(plant).then((r) => setStations(r.stations)).catch(() => setStations([]));
    getStationStats(plant).then((r) => setStats(r as StationStat[])).catch(() => setStats([]));
    getBottleneck(plant).then(setBn).catch(() => setBn(null));
    getAlertTriage(plant, 50).then(setTriage).catch(() => setTriage(null));
    getShiftSummary(plant)
      .then((s) => {
        setShift(s);
        setShiftDenied(false);
      })
      .catch(() => {
        setShift(null);
        setShiftDenied(true);
      });
    setLoaded(true);
  }, [plant]);

  useEffect(() => {
    poll();
    const t = setInterval(poll, POLL_MS);
    return () => clearInterval(t);
  }, [poll]);

  const byStation = new Map(stats.map((s) => [s.station_id, s]));

  // CLAUDE.md §8: /api/stations/stats ships `provenance: "inferred"` rows with no
  // ci_low/ci_high, which violates contract #1. Ranking those rows against measured
  // ones would launder an un-bounded estimate into a league position, so the ranked
  // list below is measured-only and says so. The inferred stations still appear in the
  // heatmap, hatched, because "we cannot see this station well" is itself the finding.
  const measured = stats.filter((s) => s.provenance === "measured");
  const inferredCount = stats.length - measured.length;
  const worst = [...measured]
    .filter((s) => (s.lost_uph ?? 0) > 0)
    .sort((a, b) => (b.lost_uph ?? 0) - (a.lost_uph ?? 0))
    .slice(0, 6);

  const cuts = bandCuts(measured);
  const counts = stats.reduce(
    (acc, s) => {
      acc[band(s, cuts)] += 1;
      return acc;
    },
    { normal: 0, watch: 0, critical: 0, unknown: 0 } as Record<Band, number>
  );

  const critical =
    (triage?.counts_by_class?.CRITICAL ?? 0) + (triage?.counts_by_class?.INVESTIGATE ?? 0);

  const uph = shift?.production.achieved_uph ?? null;
  const target = shift?.production.units_per_hour_target ?? null;
  const uphDelta = uph != null && target ? ((uph - target) / target) * 100 : null;

  const firedPct =
    shift && shift.production.alert_budget > 0
      ? (shift.production.alerts_fired / shift.production.alert_budget) * 100
      : null;

  const worstColumns: Column<StationStat>[] = [
    {
      key: "station",
      header: "Station",
      cell: (r) => {
        const cfg = stations.find((s) => s.station_id === r.station_id);
        return (
          <span className="whitespace-nowrap">
            <span className="text-numeric font-bold text-ink">S{r.station_id}</span>
            {cfg && <span className="ml-2 text-ink-faint">{cfg.zone_name}</span>}
          </span>
        );
      },
    },
    {
      key: "lost",
      header: "Lost throughput",
      align: "right",
      cell: (r) => (
        <span className="text-numeric font-bold text-danger-fg">
          {(r.lost_uph ?? 0).toFixed(1)}
          <span className="ml-1 font-normal text-ink-faint">uph</span>
        </span>
      ),
    },
    {
      key: "util",
      header: "Utilisation",
      align: "right",
      cell: (r) => (
        <span className="text-numeric text-ink-dim">
          {r.util != null ? `${(r.util * 100).toFixed(1)}%` : "—"}
        </span>
      ),
    },
    {
      key: "blocked",
      header: "Blocked",
      align: "right",
      cell: (r) => (
        <span className="text-numeric text-ink-dim">
          {r.blocked != null ? `${(r.blocked * 100).toFixed(1)}%` : "—"}
        </span>
      ),
    },
  ];

  return (
    <AppShell
      persona="Overview"
      plant={plant}
      onPlantChange={setPlant}
      role={role}
      connected={connected}
      lastKnownAt={lastKnownAt}
      onRetry={poll}
      status={<StaleDataIndicator timestamp={lastKnownAt} />}
    >
      <div className="space-y-6">
        {/* ---------------------------------------------------------- KPI row */}
        <div className="grid grid-cols-1 gap-px bg-line sm:grid-cols-2 xl:grid-cols-5">
          {!loaded ? (
            Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-28" />)
          ) : (
            <>
              <Kpi
                label="Throughput"
                icon={<IconGauge size={14} />}
                value={uph != null ? uph.toFixed(1) : "—"}
                unit="units/hr"
                delta={
                  uphDelta != null
                    ? {
                        value: `${Math.abs(uphDelta).toFixed(1)}%`,
                        direction: uphDelta >= 0 ? "up" : "down",
                        tone: uphDelta >= 0 ? "ok" : "danger",
                      }
                    : undefined
                }
                sub={target ? `Target ${target.toFixed(1)} uph` : "Requires the manager role"}
              />
              <Kpi
                label="Units this shift"
                icon={<IconChart size={14} />}
                value={shift ? String(shift.production.units) : "—"}
                unit="units"
                sub={
                  shift
                    ? `${shift.production.confirmed_defects} confirmed defects`
                    : "Requires the manager role"
                }
              />
              <Kpi
                label="Alerts fired"
                icon={<IconAlert size={14} />}
                tone={firedPct != null && firedPct >= 100 ? "danger" : "warn"}
                value={shift ? String(shift.production.alerts_fired) : "—"}
                unit={shift ? `of ${shift.production.alert_budget}` : undefined}
                sub={
                  firedPct != null
                    ? `${firedPct.toFixed(0)}% of the shift alert budget`
                    : "Requires the manager role"
                }
              />
              <Kpi
                label="Needs review now"
                icon={<IconShield size={14} />}
                tone="ai"
                value={triage ? String(critical) : "—"}
                unit="units"
                accent
                sub={
                  triage
                    ? `${triage.count} ranked, of ${triage.already_fired} fired`
                    : "Requires the supervisor role"
                }
              />
              <Kpi
                label="Detector agreement"
                icon={<IconTarget size={14} />}
                value={
                  bn?.detection_accuracy != null
                    ? `${(bn.detection_accuracy * 100).toFixed(1)}%`
                    : "—"
                }
                sub="vs 2h sustained truth"
              />
            </>
          )}
        </div>

        {/* ------------------------------------------- situation + line state */}
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
          <Panel
            title="Current situation"
            tone={bn ? "danger" : "default"}
            // "separation", not "confidence": the stored value is
            // (top - runner_up) / top over the window's active time
            // (offline/ml/bottleneck.py::detect), so 0% means the top two stations tied
            // in this window -- a real and readable statement. Labelled "confidence" it
            // just reads as a broken field.
            note={
              bn ? (
                <Tooltip content="Separation from the runner-up station over this window: (top − second) ÷ top. 0% means the two were tied, which is a genuine finding about the window, not a missing value.">
                  <span>separation {(bn.confidence * 100).toFixed(0)}%</span>
                </Tooltip>
              ) : undefined
            }
          >
            {!bn ? (
              <p className="py-6 text-xs text-ink-faint">
                No constraint reported for this plant.
              </p>
            ) : (
              <div className="space-y-4">
                <h3 className="text-xl font-bold leading-snug text-ink">
                  {bn.previous_station != null && bn.previous_station !== bn.current_station
                    ? `The constraint moved from S${bn.previous_station} to S${bn.current_station}`
                    : `The constraint is holding at S${bn.current_station}`}
                </h3>
                <p className="text-sm leading-relaxed text-ink-dim">
                  {bn.current_station_name} has been the binding station since{" "}
                  <span className="text-numeric text-ink">{fmtTime(bn.since)}</span>
                  {bn.shifted_at && bn.previous_station != null && (
                    <>
                      , having taken over from S{bn.previous_station} at{" "}
                      <span className="text-numeric text-ink">{fmtTime(bn.shifted_at)}</span>
                    </>
                  )}
                  . The detector and the sustained-truth window deliberately disagree — they
                  use different windows, so agreement below 100% is the design, not a defect.
                </p>

                <div className="grid grid-cols-2 gap-px border border-line bg-line sm:grid-cols-3">
                  <Cell label="Binding station" value={`S${bn.current_station}`} tone="danger" />
                  <Cell
                    label="Previous"
                    value={bn.previous_station != null ? `S${bn.previous_station}` : "—"}
                  />
                  <Cell
                    label="Shifts this shift"
                    value={shift ? String(shift.bottleneck.shift_events) : "—"}
                  />
                </div>

                <Link href="/manager">
                  <Button variant="link">
                    See the constraint timeline
                    <IconArrowRight size={13} />
                  </Button>
                </Link>
              </div>
            )}
          </Panel>

          <Panel
            title="Assembly line — live status"
            note={
              stations.length
                ? `${stations.length} stations · ${inferredCount} inferred`
                : undefined
            }
          >
            {stations.length === 0 ? (
              <Skeleton className="h-28" />
            ) : (
              <div className="space-y-4">
                <ol className="flex flex-wrap items-end gap-1.5">
                  {stations.map((s) => {
                    const st = byStation.get(s.station_id);
                    const b = band(st, cuts);
                    const isBn = bn?.current_station === s.station_id;
                    return (
                      <li key={s.station_id}>
                        <Tooltip
                          content={
                            <span className="block space-y-0.5">
                              <span className="block font-bold text-on-dark">
                                S{s.station_id} · {s.station_name}
                              </span>
                              <span className="block">{s.zone_name}</span>
                              <span className="block">
                                {st?.lost_uph != null
                                  ? `${st.lost_uph.toFixed(1)} uph lost · ${BAND_LABEL[b]}`
                                  : "No station statistics"}
                              </span>
                              {st?.provenance === "inferred" && (
                                <span className="block text-warn-on-dark">
                                  Inferred — this station is unsensored, and the API reports
                                  no interval for it.
                                </span>
                              )}
                            </span>
                          }
                        >
                          <span
                            className={cn(
                              "relative flex size-7 items-center justify-center border",
                              "text-numeric text-[9px] font-bold",
                              BAND_TILE[b],
                              st?.provenance === "inferred" && "hatch-inferred",
                              isBn && "outline outline-2 outline-offset-1 outline-danger"
                            )}
                          >
                            {s.station_id}
                          </span>
                        </Tooltip>
                      </li>
                    );
                  })}
                </ol>

                <div className="space-y-2 border-t border-line pt-3">
                  <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
                    {(["critical", "watch", "normal", "unknown"] as Band[]).map((b) => (
                      <span key={b} className="flex items-center gap-1.5 text-2xs text-ink-faint">
                        <span
                          className={cn(
                            "size-2",
                            BAND_DOT[b],
                            b === "unknown" && inferredCount > 0 && "hatch-inferred border border-warn/60"
                          )}
                        />
                        {BAND_LABEL[b]} ({counts[b]})
                      </span>
                    ))}
                    {bn && (
                      <span className="flex items-center gap-1.5 text-2xs text-ink-faint">
                        <span className="size-2 outline outline-2 outline-offset-1 outline-danger" />
                        Constraint
                      </span>
                    )}
                  </div>
                  <p className="text-2xs leading-relaxed text-ink-faint">
                    Banded by position within this plant&apos;s own measured distribution of lost
                    throughput — critical is the top decile, watch the next fifth. Unsensored
                    stations carry no interval and so take no band.
                  </p>
                </div>
              </div>
            )}
          </Panel>
        </div>

        {/* ------------------------------------------ losses + what to do next */}
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <Panel
            title="Where throughput is going"
            note={
              inferredCount > 0
                ? `measured stations only — ${inferredCount} inferred excluded`
                : "measured stations only"
            }
          >
            {!loaded ? (
              <SkeletonTable rows={5} cols={4} />
            ) : (
              <>
                <DataTable
                  columns={worstColumns}
                  rows={worst}
                  rowKey={(r) => String(r.station_id)}
                  empty="No measured station is losing throughput."
                />
                {inferredCount > 0 && (
                  <p className="mt-3 flex items-start gap-2 border-t border-line pt-3 text-2xs leading-relaxed text-ink-faint">
                    <InferredBadge />
                    <span>
                      {inferredCount} station{inferredCount === 1 ? " is" : "s are"} unsensored.
                      The API reports their figures without a confidence interval, so ranking
                      them against measured stations would give an unbounded estimate a league
                      position. They are shown in the line map above instead.
                    </span>
                  </p>
                )}
              </>
            )}
          </Panel>

          <Panel title="Needs attention" note="derived from the shift summary">
            {shiftDenied ? (
              <RoleNote min="manager" />
            ) : !shift ? (
              <SkeletonTable rows={4} cols={2} />
            ) : (
              <ul className="divide-y divide-line">
                <Attention
                  tone={shift.sensors.dropouts_active > 0 ? "warn" : "ok"}
                  title={
                    shift.sensors.dropouts_active > 0
                      ? `${shift.sensors.dropouts_active} sensor dropout${shift.sensors.dropouts_active === 1 ? "" : "s"} active`
                      : "Sensor coverage complete"
                  }
                  detail={
                    shift.sensors.stations_affected.length > 0
                      ? `Stations ${shift.sensors.stations_affected.map((n) => `S${n}`).join(", ")} — values there are inferred, not measured.`
                      : "Every configured sensor is reporting."
                  }
                  href="/ops"
                />
                <Attention
                  tone={shift.model.drift_metrics_breached > 0 ? "warn" : "ok"}
                  title={
                    shift.model.drift_metrics_breached > 0
                      ? `${shift.model.drift_metrics_breached} drift threshold${shift.model.drift_metrics_breached === 1 ? "" : "s"} breached`
                      : "Model within drift thresholds"
                  }
                  detail={
                    shift.model.breached.length > 0
                      ? shift.model.breached
                          .slice(0, 3)
                          .map((b) => `${b.feature} (${b.metric_type})`)
                          .join(", ")
                      : "No feature has crossed its PSI or calibration threshold this shift."
                  }
                  href="/ops"
                />
                <Attention
                  tone={firedPct != null && firedPct >= 100 ? "danger" : "ok"}
                  title={
                    firedPct != null && firedPct >= 100
                      ? "Alert budget exhausted"
                      : "Alert budget within limits"
                  }
                  detail={`${shift.production.alerts_fired} of ${shift.production.alert_budget} alerts fired this shift.`}
                  href="/leadership"
                />
                <Attention
                  tone={shift.bottleneck.shift_events > 0 ? "info" : "ok"}
                  title={
                    shift.bottleneck.shift_events > 0
                      ? `Constraint moved ${shift.bottleneck.shift_events}×`
                      : "Constraint stable"
                  }
                  detail={`Across ${shift.bottleneck.windows_in_shift} windows this shift${
                    shift.bottleneck.detector_agreement != null
                      ? `, detector agreement ${(shift.bottleneck.detector_agreement * 100).toFixed(0)}%`
                      : ""
                  }.`}
                  href="/manager"
                />
              </ul>
            )}
          </Panel>
        </div>

        {/* ------------------------------------------------------ persona grid */}
        <section>
          <h2 className="mb-3 text-xs font-bold uppercase tracking-[0.115em] text-ink-faint">
            Go to a view
          </h2>
          <div className="grid grid-cols-1 gap-px bg-line sm:grid-cols-2 xl:grid-cols-5">
            {PERSONAS.map(({ href, title, body, Icon }) => {
              // Each card says whether THIS session can open it, and a locked one links
              // to /login carrying `next` -- so the click still gets the visitor where
              // they were going, one sign-in later, instead of landing on a refusal.
              const open = canEnter(href, session?.role ?? null);
              const requires = policyFor(href)?.requires;
              return (
                <Link
                  key={href}
                  href={open ? href : `/login?next=${encodeURIComponent(href)}`}
                  className={cn(
                    "group flex flex-col bg-canvas p-5",
                    "transition-colors duration-150 hover:bg-surface-2"
                  )}
                >
                  <span className="flex items-center justify-between gap-2">
                    <Icon size={20} className={open ? "text-primary" : "text-ink-faint"} />
                    {!open && <IconLock size={14} className="text-ink-faint" />}
                  </span>
                  <h3 className="mt-3 text-base font-bold text-ink">{title}</h3>
                  <p className="mt-1.5 flex-1 text-xs leading-relaxed text-ink-faint">{body}</p>
                  <span className="label-upper mt-4 flex items-center gap-1.5 text-ink transition-colors group-hover:text-primary">
                    {open ? "Open" : `Sign in as ${requires?.join(" or ")}`}
                    <IconArrowRight size={13} />
                  </span>
                </Link>
              );
            })}
          </div>
        </section>
      </div>

      <CopilotDock plant={plant} />
    </AppShell>
  );
}

// ------------------------------------------------------------------------- fragments

function Cell({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "danger";
}) {
  return (
    <div className="bg-canvas px-4 py-3">
      <div className="text-2xs font-bold uppercase tracking-[0.115em] text-ink-faint">{label}</div>
      <div
        className={cn(
          "text-numeric mt-1 text-lg font-bold",
          tone === "danger" ? "text-danger-fg" : "text-ink"
        )}
      >
        {value}
      </div>
    </div>
  );
}

const ATTENTION_TONE = {
  ok: "text-ok-fg",
  warn: "text-warn-fg",
  danger: "text-danger-fg",
  info: "text-info-fg",
} as const;

function Attention({
  tone,
  title,
  detail,
  href,
}: {
  tone: keyof typeof ATTENTION_TONE;
  title: string;
  detail: string;
  href: string;
}) {
  return (
    <li>
      <Link href={href} className="-mx-2 flex gap-3 px-2 py-3 transition-colors hover:bg-surface-2">
        <span className={cn("mt-1.5 size-2 shrink-0", tone === "ok" ? "bg-ok" : "bg-current", ATTENTION_TONE[tone])} />
        <span className="min-w-0 flex-1">
          <span className="block text-xs font-bold text-ink">{title}</span>
          <span className="mt-0.5 block text-2xs leading-relaxed text-ink-faint">{detail}</span>
        </span>
        <IconArrowRight size={14} className="mt-0.5 shrink-0 text-ink-faint" />
      </Link>
    </li>
  );
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}
