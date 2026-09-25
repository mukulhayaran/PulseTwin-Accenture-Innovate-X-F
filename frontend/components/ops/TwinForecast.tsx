"use client";

// ADR-055: what the twin says each proposed action does, against doing nothing.
//
// The chart form is an interval against a zero reference, not a fan of trajectories:
// every arm runs near the same UPH, so a +0.5 UPH effect is invisible on a cumulative
// scale and legible only as a difference. Zero IS the do-nothing arm. Every value is
// inferred (simulated), so it carries the amber hatch and the INFERRED badge (contract #1).

import { Counterfactual, ForecastArm, Interval } from "@/lib/api";
import { Badge, InferredBadge, Tooltip } from "@/components/ui";

const signed = (v: number, digits = 2) => `${v > 0 ? "+" : ""}${v.toFixed(digits)}`;
const interval = (i: Interval, digits = 2) =>
  `${signed(i.mean, digits)} [${signed(i.ci_low, digits)}, ${signed(i.ci_high, digits)}]`;

/** Shared half-width for every strip in one proposal, so arms compare by eye. */
export function impactScale(cf: Counterfactual | null): number {
  const widths = (cf?.arms ?? []).flatMap((a) => {
    const d = a.vs_do_nothing?.throughput_uph;
    return d ? [Math.abs(d.ci_low), Math.abs(d.ci_high)] : [];
  });
  return Math.max(0.5, ...widths) * 1.15;
}

/** The decision-time summary: what the twin reproduced, and the do-nothing forecast. */
export function ForecastHeader({ cf, nothing }: { cf: Counterfactual | null; nothing: ForecastArm | null }) {
  if (!cf) return null;
  if (cf.status === "queued" || cf.status === "running") {
    return (
      <div className="mt-2 flex items-center gap-2 border-t border-line pt-2">
        <Badge variant="info" dot>simulating</Badge>
        <span className="text-2xs text-ink-dim">
          Forking the twin into paired futures, doing nothing and each action. Approval unlocks when it finishes.
        </span>
      </div>
    );
  }
  if (cf.status === "failed") {
    return (
      <p className="mt-2 border-t border-line pt-2 text-2xs text-danger-fg">
        Twin forecast unavailable: {cf.error}. Decide on the evidence alone.
      </p>
    );
  }
  const hours = (cf.method?.horizon_s ?? 0) / 3600;
  return (
    <div className="mt-2 border-t border-line pt-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-2xs font-bold uppercase tracking-[0.115em] text-ink">
          If nothing is done · next {hours} h
        </span>
        <InferredBadge />
      </div>
      {nothing?.throughput_uph && nothing.expected_defects && (
        <p className="text-numeric mt-1 text-xs text-ink">
          {nothing.throughput_uph.mean.toFixed(1)} UPH{" "}
          <span className="text-ink-faint">
            [{nothing.throughput_uph.ci_low.toFixed(1)}–{nothing.throughput_uph.ci_high.toFixed(1)}]
          </span>
          {" · "}
          {nothing.expected_defects.mean.toFixed(2)} expected defects{" "}
          <span className="text-ink-faint">
            [{nothing.expected_defects.ci_low.toFixed(2)}–{nothing.expected_defects.ci_high.toFixed(2)}]
          </span>
        </p>
      )}
      {cf.hypothesis && (
        <p className="mt-0.5 text-2xs text-ink-faint">
          Twin reproduced {cf.hypothesis.scenario.replace(/_/g, " ")} at severity{" "}
          {cf.hypothesis.severity.toFixed(2)} ({cf.hypothesis.basis}) · {cf.method?.replicas} paired replicas
          per arm · 95% CI
        </p>
      )}
    </div>
  );
}

/** Directional: "distinguishable" alone would paint an action that is reliably WORSE green. */
function Verdict({ d }: { d: NonNullable<ForecastArm["vs_do_nothing"]> }) {
  const t = d.throughput_uph, q = d.expected_defects;
  const [variant, text]: ["ok" | "danger" | "neutral", string] =
    t.ci_low > 0 ? ["ok", "gains throughput"]
    : t.ci_high < 0 ? ["danger", "loses throughput"]
    : q.ci_high < 0 ? ["ok", "fewer defects"]
    : q.ci_low > 0 ? ["danger", "more defects"]
    // Paired replicas that never diverge: the change never bound, not a noisy estimate.
    : [t.ci_low, t.ci_high, q.ci_low, q.ci_high].every((v) => v === 0) ? ["neutral", "no effect"]
    : ["neutral", "within noise"];
  return <Badge variant={variant}>{text}</Badge>;
}

/** One action's simulated effect: Δ throughput vs do-nothing as an interval on a zero line. */
export function ImpactStrip({ arm, scale }: { arm: ForecastArm | null | undefined; scale: number }) {
  if (!arm) return null;
  if (!arm.modelled) {
    return <p className="mt-1.5 text-2xs text-ink-faint">Not simulated: {arm.reason}.</p>;
  }
  const d = arm.vs_do_nothing;
  if (!d) return null;
  const pos = (v: number) => `${50 + (50 * Math.max(-scale, Math.min(scale, v))) / scale}%`;
  const t = d.throughput_uph;
  return (
    <div className="mt-1.5 border-t border-ai/20 pt-1.5">
      <div className="flex items-center gap-2">
        <span className="w-32 shrink-0 text-2xs text-ink-dim">Δ UPH vs nothing</span>
        <Tooltip
          content={
            <span className="text-numeric">
              {interval(t)} UPH · 95% CI of the paired difference · zero is doing nothing
            </span>
          }
        >
          <div
            className="relative h-4 min-w-24 flex-1"
            role="img"
            aria-label={`Throughput change ${interval(t)} units per hour versus doing nothing`}
          >
            <div className="absolute inset-y-0 left-1/2 w-px bg-ink-faint" />
            <div className="absolute inset-x-0 top-1/2 h-px bg-line" />
            <div
              className="hatch-inferred absolute top-1/2 h-2.5 -translate-y-1/2 border border-warn"
              style={{ left: pos(t.ci_low), width: `calc(${pos(t.ci_high)} - ${pos(t.ci_low)})`, minWidth: 2 }}
            />
            <div
              className="absolute top-1/2 h-3.5 w-0.5 -translate-x-1/2 -translate-y-1/2 bg-ink"
              style={{ left: pos(t.mean) }}
            />
          </div>
        </Tooltip>
        <span className="text-numeric w-40 shrink-0 text-right text-2xs text-ink">{interval(t)} UPH</span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <Verdict d={d} />
        <span className="text-numeric text-2xs text-ink-dim">
          Δ expected defects {interval(d.expected_defects, 3)}
        </span>
        {arm.simulated_as && (
          <span className="text-2xs text-ink-faint">· simulated as: {arm.simulated_as}</span>
        )}
      </div>
    </div>
  );
}
