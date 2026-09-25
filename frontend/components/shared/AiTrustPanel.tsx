"use client";

// "AI supports the operator. It does not silently control the factory."
//
// Everything on this panel is read from an endpoint, never asserted. Where a fact is not
// available it says so rather than showing a comfortable default -- a trust panel that
// invents its own reassurance is worse than no trust panel.

import { useEffect, useState } from "react";
import {
  DataQuality, ModelHealthLike, TriageRun,
  getDataQuality, getModelHealth, getRole, getTriageAvailability, roleAtLeast,
} from "@/lib/api";
import { Badge, Panel, Tooltip } from "@/components/ui";

export default function AiTrustPanel({
  plant,
  run,
}: {
  plant: string;
  // The most recent completed turn, if there is one. The panel works without it.
  run?: TriageRun | null;
}) {
  const [availability, setAvailability] = useState<{
    mode: string; reason: string;
  } | null>(null);
  const [health, setHealth] = useState<ModelHealthLike | null>(null);
  const [dq, setDq] = useState<DataQuality | null>(null);
  const [gated, setGated] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getTriageAvailability()
      .then((a) => !cancelled && setAvailability({ mode: a.mode, reason: a.reason }))
      .catch(() => !cancelled && setAvailability(null));
    // Model health is leadership-gated, and that is a FACT about this viewer's role, not
    // an error -- so it is decided from the role up front (as AppShell does for the shift
    // summary) rather than by provoking a 403 in the console. A failure for a viewer who
    // IS allowed is a real error and shows as "—", not as a permissions message.
    const allowed = roleAtLeast(getRole(), "leadership");
    setGated(!allowed);
    if (allowed) {
      getModelHealth(plant)
        .then((h) => !cancelled && setHealth(h))
        .catch(() => !cancelled && setHealth(null));
    }
    getDataQuality(plant)
      .then((d) => !cancelled && setDq(d))
      .catch(() => !cancelled && setDq(null));
    return () => {
      cancelled = true;
    };
  }, [plant]);

  const ver = run?.verification;
  const measured = run?.finding?.claims.filter((c) => c.provenance === "measured").length;
  const inferred = run?.finding?.claims.filter((c) => c.provenance === "inferred").length;

  return (
    <Panel
      title="AI trust"
      note="What produced the answer, how fresh it is, and what was checked."
      actions={
        availability ? (
          <Badge variant={availability.mode === "agent" ? "ai" : "neutral"} dot>
            {availability.mode === "agent" ? "Agent" : "Deterministic"}
          </Badge>
        ) : null
      }
    >
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5">
        {/* Gated: one sentence across both columns rather than four truncated copies
            of "requires leadership" -- the reader needs the reason once, legibly. */}
        {gated ? (
          <p className="col-span-2 border-l-2 border-line-strong pl-2.5 text-2xs leading-relaxed text-ink-faint">
            Model, age, drift and fallback status need the leadership or system role.
          </p>
        ) : (
          <>
        <Fact
          label="Model"
          value={health?.current_model?.id ?? "—"}
        />
        <Fact
          label="Model age"
          value={
            health?.current_model?.age_days != null
              ? `${health.current_model.age_days} d`
              : "—"
          }
        />
        <Fact
          label="Drift"
          value={health?.drift?.overall_status ?? "—"}
          tone={
            health?.drift?.overall_status === "healthy"
              ? "ok"
              : health?.drift?.overall_status
                ? "warn"
                : undefined
          }
        />
        <Fact
          label="Fallback"
          value={health?.fallback?.status ?? "—"}
          tone={health?.fallback?.status === "standby" ? "ok" : undefined}
          hint="The SPC fallback scores predictions if the model file is missing or throws. `standby` means the real model is serving."
        />
          </>
        )}
        <Fact
          label="Data quality"
          value={dq?.plant_score != null ? dq.plant_score.toFixed(3) : "—"}
          hint={dq?.notes?.[0] ?? dq?.method}
        />
        <Fact
          label="Not measurable"
          value={dq?.components_unavailable?.join(", ") || "—"}
          hint="Components the artifact cannot support are reported as unavailable rather than estimated."
        />

        <Fact
          label="Verdict"
          value={run?.status ? run.status.replace(/_/g, " ") : "no run yet"}
          tone={
            run?.status === "verified"
              ? "ok"
              : run?.status === "verified_with_redactions"
                ? "warn"
                : run?.status === "rejected" || run?.status === "failed"
                  ? "danger"
                  : undefined
          }
        />
        <Fact
          label="Claims verified"
          value={ver ? `${ver.claims_verified}/${ver.claims_total}` : "—"}
          hint="Every numeric claim is re-checked against the exact tool results the model was shown. Failures are removed, not flagged."
        />
        <Fact
          label="Measured / inferred"
          value={
            measured != null && inferred != null ? `${measured} / ${inferred}` : "—"
          }
          hint="An inferred value is reconstructed by the soft sensor and must carry a confidence interval."
        />
        <Fact
          label="Evidence"
          value={run?.tool_calls != null ? `${run.tool_calls} tool calls` : "—"}
        />
        <Fact
          label="Prediction at"
          value={run?.created_at ? String(run.created_at).slice(0, 19).replace("T", " ") : "—"}
        />
        <Fact
          label="Audit id"
          value={run?.run_id ?? "—"}
          hint="Every run is written to the audit log with the role that triggered it and the role its tools acted as."
        />
      </dl>

      <p className="mt-3 border-t border-line pt-2.5 text-2xs leading-relaxed text-ink-faint">
        The agent is read-only. Its tool registry contains no function that can change a
        model, a configuration or the line — the one non-GET tool posts to a simulator.
        Every recommendation needs a human to act on it.
      </p>
    </Panel>
  );
}

function Fact({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: string;
  tone?: "ok" | "warn" | "danger";
  hint?: string;
}) {
  const body = (
    <div>
      <dt className="text-2xs uppercase tracking-[0.12em] text-ink-faint">{label}</dt>
      <dd
        className={
          "text-numeric mt-0.5 truncate text-xs " +
          (tone === "ok"
            ? "text-ok-fg"
            : tone === "warn"
              ? "text-warn-fg"
              : tone === "danger"
                ? "text-danger-fg"
                : "text-ink")
        }
      >
        {value}
      </dd>
    </div>
  );
  return hint ? <Tooltip content={hint}>{body}</Tooltip> : body;
}
