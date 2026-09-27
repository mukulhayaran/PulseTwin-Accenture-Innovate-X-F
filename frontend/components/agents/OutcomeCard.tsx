"use client";

// What happened after an approved action was applied to the simulated twin (ADR-064).
// Every value here was computed by serve/outcomes.py against configured thresholds; this
// component only lays it out. "Improvement" is shown as the reduction in a
// lower-is-better station metric, so a positive number always means "got better".

import type { ActionOutcome, OutcomeStatus } from "@/lib/api";
import { Badge, Tooltip } from "@/components/ui";

const TONE: Record<OutcomeStatus, "ok" | "warn" | "danger" | "info" | "neutral"> = {
  pending: "neutral", observing: "info", successful: "ok", partially_successful: "warn",
  no_material_effect: "neutral", harmful: "danger", inconclusive: "neutral",
  cancelled: "neutral",
};

const METRIC: Record<string, { label: string; scale: number; unit: string }> = {
  stalled_share: { label: "station stalled share", scale: 100, unit: " pp" },
  humidity_excess_pct: { label: "humidity over ambient", scale: 1, unit: " pts" },
  signal_deviation_pct: { label: "signal deviation", scale: 1, unit: " pp" },
};

function num(v: number | null | undefined, scale: number, unit: string, digits = 1) {
  if (v == null) return "-";
  const x = v * scale;
  return `${x > 0 ? "+" : ""}${x.toFixed(digits)}${unit}`;
}

export default function OutcomeCard({ outcome }: { outcome: ActionOutcome }) {
  const m = METRIC[outcome.metric] ?? { label: outcome.metric, scale: 1, unit: "" };
  // Improvement = reduction (every outcome metric is lower-is-better).
  const expected = outcome.expected_change == null ? null : -outcome.expected_change;
  const actual = outcome.actual_change == null ? null : -outcome.actual_change;
  const windowMin = outcome.window_s != null ? Math.round(outcome.window_s / 60) : null;
  return (
    <div className="border border-line bg-surface-2 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-2xs font-bold uppercase tracking-[0.14em] text-ink-faint">
          Outcome - {m.label}
        </span>
        <Badge variant="ok">Measured</Badge>
        <span className="flex-1" />
        <Badge variant={TONE[outcome.status]} dot={outcome.status === "observing"}>
          {outcome.status.replace(/_/g, " ")}
        </Badge>
      </div>
      <dl className="text-numeric grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-5">
        <div>
          <dt className="text-2xs text-ink-faint">Expected improvement</dt>
          <dd className="text-ink">
            {expected == null ? (
              <Tooltip content={outcome.expected_source ?? "no projection"}>
                <span className="cursor-help text-ink-faint">not projected</span>
              </Tooltip>
            ) : (
              <Tooltip content={`${outcome.expected_source ?? ""} | simulated (inferred) | 95% interval ` +
                `${num(outcome.expected_ci_high == null ? null : -outcome.expected_ci_high, m.scale, m.unit)} to ` +
                `${num(outcome.expected_ci_low == null ? null : -outcome.expected_ci_low, m.scale, m.unit)}`}>
                <span className="cursor-help">{num(expected, m.scale, m.unit)}</span>
              </Tooltip>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-2xs text-ink-faint">Actual improvement</dt>
          <dd className="text-ink">{outcome.status === "observing" ? "observing..." : num(actual, m.scale, m.unit)}</dd>
        </div>
        <div>
          <dt className="text-2xs text-ink-faint">Evaluation window</dt>
          <dd className="text-ink">
            {windowMin != null ? `${windowMin} sim-min` : "-"}
            <span className="block text-2xs text-ink-faint">
              {outcome.n_events_before ?? 0} to {outcome.n_events_after ?? "..."} events
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-2xs text-ink-faint">Result</dt>
          <dd className="text-ink">
            <Tooltip content={outcome.reason ?? "awaiting the end of the window"}>
              <span className="cursor-help">{outcome.status.replace(/_/g, " ")}</span>
            </Tooltip>
          </dd>
        </div>
        <div>
          <dt className="text-2xs text-ink-faint">Projection error</dt>
          <dd className="text-ink">
            {outcome.prediction_error == null ? "-"
              : num(outcome.prediction_error, m.scale, m.unit, 2)}
            {outcome.prediction_error != null && (
              <span className="block text-2xs text-ink-faint">
                {/* observed change minus projected; lower-is-better metric */}
                {outcome.prediction_error < 0 ? "better than projected"
                  : outcome.prediction_error > 0 ? "worse than projected" : "as projected"}
              </span>
            )}
          </dd>
        </div>
      </dl>
      <p className="mt-2 text-2xs leading-relaxed text-ink-faint">
        Deterministic evaluation against configs/alert_policies.yaml. The live twin replays
        recorded flow, so this is a station-level observation; line throughput cannot respond
        to an action there.
      </p>
    </div>
  );
}
