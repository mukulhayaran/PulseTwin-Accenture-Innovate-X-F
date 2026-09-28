"use client";

// The Incident Commander's PERSISTED state for one incident (ADR-067, ADR-069): steps,
// evidence with provenance, competing hypotheses, twin experiments, genealogy trace, the
// recommendation and its actions. Nothing here computes a number -- it formats them.
// Read-only, mounted on /supervisor (ADR-071): decisions live in the manager's Decision
// Queue, so this shows what was recommended and what was decided, never a control.

import { useMemo, useState } from "react";
import { type Evidence, type Hypothesis, type IncidentDetail, type RecOption } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Badge, Panel } from "@/components/ui";
import AiStages from "@/components/incidents/AiStages";

const PROVENANCE: Record<Evidence["provenance"], { label: string; variant: any }> = {
  measured: { label: "MEASURED", variant: "ok" },
  inferred: { label: "INFERRED", variant: "warn" },
  simulated: { label: "SIMULATED", variant: "ai" },
  manual: { label: "MANUAL", variant: "info" },
  missing: { label: "MISSING", variant: "neutral" },
};

const STATUS_VARIANT: Record<string, any> = {
  supported: "ok", investigating: "info", proposed: "neutral", weakened: "warn",
  rejected: "neutral", unresolved: "warn",
};

const GOVERNOR_VARIANT: Record<string, any> = {
  HUMAN_APPROVAL_REQUIRED: "info", AUTO_EXECUTE_ALLOWED: "ok", RECOMMEND_ONLY: "neutral",
  INVESTIGATION_ONLY: "warn", BLOCKED: "danger",
};

const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const signed = (v: number | null | undefined, d = 2) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(d)}`;
const clock = (ts?: string | null) => (ts ? String(ts).slice(11, 19) : "—");

export function IncidentWorkspace({ detail, similar }: { detail: IncidentDetail; similar: any[] }) {
  const { incident, investigation: inv, recommendation: rec, actions } = detail;
  const st = inv?.state;
  const evById = useMemo(() => new Map((st?.evidence ?? []).map((e) => [e.evidence_id, e])), [st]);
  const a = st?.assessment;
  const running = inv && !["completed", "failed"].includes(inv.status);

  return (
    <>
      <div className="panel-surface flex flex-wrap items-start justify-between gap-3 p-4">
        <div className="min-w-0">
          <div className="text-numeric text-xs font-bold text-ink-faint">{incident.incident_id}</div>
          <h1 className="mt-1 text-xl font-bold text-ink">{incident.title}</h1>
          <p className="mt-1 text-xs text-ink-faint">
            {st?.objective ?? "awaiting investigation"} · opened by {incident.source}
            {st?.window ? ` · evidence window ${clock(st.window.start_ts)}–${clock(st.window.end_ts)} (simulated)` : ""}
          </p>
          {incident.notes && (
            <p className="mt-2 border-l-2 border-line-strong pl-2 text-xs text-ink-dim">
              Operator note (shown as data, never executed): “{incident.notes}”
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={incident.severity === "high" ? "danger" : "warn"}>severity {incident.severity}</Badge>
          {inv && <Badge variant={inv.planner_mode === "llm" ? "ai" : "neutral"}>planner: {inv.planner_mode}</Badge>}
          {inv && <Badge variant={running ? "info" : "ok"} dot={!!running}>{inv.status.replace(/_/g, " ")}</Badge>}
        </div>
      </div>

      {detail.stages && <AiStages stages={detail.stages} />}

      {detail.explanation?.length > 0 && (
        // ADR-070: the whole incident in plain words, generated server-side from this
        // incident's own record -- every sentence traces to a stored field.
        <Panel title="In simple words" note="written from this incident's own records">
          <dl className="space-y-2.5">
            {detail.explanation.map((sec) => (
              <div key={sec.title} className="grid grid-cols-1 gap-1 md:grid-cols-[9rem_minmax(0,1fr)]">
                <dt className="text-2xs font-bold uppercase tracking-[0.12em] text-ink-faint">{sec.title}</dt>
                <dd className="whitespace-pre-line text-xs leading-relaxed text-ink">{sec.text}</dd>
              </div>
            ))}
          </dl>
        </Panel>
      )}

      {inv?.planner_mode === "deterministic" && (st?.fallbacks?.length ?? 0) > 0 && (
        <div className="border border-warn/40 bg-warn/8 px-3 py-2 text-xs text-warn-fg">
          AI provider unavailable or its plan was rejected — running deterministic investigation mode.
          ({st!.fallbacks[st!.fallbacks.length - 1].reason})
        </div>
      )}

      {!inv ? (
        <Panel title="Investigation Timeline"><p className="text-xs text-ink-faint">Not started.</p></Panel>
      ) : (
        <div className="grid grid-cols-1 gap-4 2xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <Panel title="Investigation Timeline" note="Each step: who the commander chose and why.">
            <ol className="space-y-2">
              {inv.steps.map((s) => (
                <li key={s.step_id} className="flex gap-2.5">
                  <span className="text-numeric mt-0.5 w-5 shrink-0 text-2xs font-bold text-ink-faint">{s.seq}</span>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-xs font-bold text-ink">{(s.agent ?? s.kind).replace(/_/g, " ")}</span>
                      <Badge variant="neutral">{s.kind}</Badge>
                      {s.plan_source === "llm" && <Badge variant="ai">LLM plan</Badge>}
                      {s.plan_valid === 0 && <Badge variant="danger">plan rejected</Badge>}
                    </div>
                    <p className="mt-0.5 text-2xs leading-relaxed text-ink-dim">{s.rejection_reason ?? s.summary}</p>
                    {s.plan?.required_evidence?.length > 0 && (
                      <p className="text-numeric mt-0.5 text-2xs text-ink-faint">{s.plan!.required_evidence.join(" · ")}</p>
                    )}
                  </div>
                </li>
              ))}
              {running && st?.current_step && (
                <li className="flex gap-2.5 text-2xs text-info-fg">
                  <span className="w-5">→</span>{st.current_step.reason ?? st.current_step.agent}
                </li>
              )}
            </ol>
          </Panel>
          <Panel title="Evidence timeline" note="Provenance on every item. Missing evidence is shown, not hidden.">
            <ul className="max-h-[26rem] space-y-2 overflow-y-auto pr-1">
              {st!.evidence.map((e) => (
                <li key={e.evidence_id} className="border-b border-line pb-2 last:border-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-numeric text-2xs text-ink-faint">{clock(e.observed_at)}</span>
                    <Badge variant={PROVENANCE[e.provenance].variant}>{PROVENANCE[e.provenance].label}</Badge>
                    <span className="text-2xs font-bold text-ink-dim">{e.specialist.replace("_", " ")}</span>
                    <span className="text-numeric text-2xs text-ink-faint">{e.probe}{e.station_id != null ? `@S${e.station_id}` : ""}</span>
                  </div>
                  <p className="mt-0.5 text-xs text-ink">{e.summary}</p>
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      )}

      {st && st.hypotheses.length > 0 && (
        <Panel
          title="Competing hypotheses"
          note="softmax(ln prior + Σ quality × weight) — weights from effect sizes, never from a model"
        >
          <div className="space-y-2.5">
            {st.hypotheses.map((h) => <HypothesisBar key={h.key} h={h} evById={evById} />)}
          </div>
          {Object.keys(st.sensor_maturity ?? {}).length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2 text-2xs text-ink-faint">
              Sensor maturity:
              {Object.entries(st.sensor_maturity).map(([sid, m]) => (
                <Badge key={sid} variant={m === "HIGH" ? "ok" : m === "MEDIUM" ? "info" : "warn"}>S{sid} {m}</Badge>
              ))}
            </div>
          )}
        </Panel>
      )}

      {st && st.experiments.length > 0 && (
        <Panel title="Digital-twin experiments" tone="ai" note="Paired seeds on isolated twin copies; 95% bootstrap interval">
          <div className="space-y-2">
            {st.experiments.map((x: any) => (
              <div key={x.experiment_id ?? x.label} className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line pb-2 last:border-0">
                <div className="min-w-0">
                  <div className="text-xs font-bold text-ink">{x.label}</div>
                  <div className="text-2xs text-ink-faint">
                    tests “{x.hypothesis_title}” at S{x.station_id}
                    {x.context ? ` · reproduces ${x.context.scenario} (${x.context.basis})` : ""}
                  </div>
                </div>
                <div className="text-right">
                  {x.objective_delta ? (
                    <div className="text-numeric text-sm font-bold text-ai-fg">
                      {signed(x.objective_delta.mean)} <span className="text-2xs font-normal text-ink-faint">{x.objective_metric}</span>
                    </div>
                  ) : <Badge variant="info" dot>{x.status}</Badge>}
                  {x.objective_delta && (
                    <div className="text-numeric text-2xs text-ink-faint">
                      CI {signed(x.objective_delta.ci_low)} … {signed(x.objective_delta.ci_high)} · {x.effect}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Panel>
      )}

      {st?.trace && (
        <Panel title="Genealogy trace" note="Backwards from the gate, over the run's own unit history">
          <div className="grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
            <Fact k="Deviation began" v={clock(st.trace.deviation_onset_ts)} />
            <Fact k="First defective unit entered" v={clock(st.trace.first_defective_unit_entry_ts)} />
            <Fact k="First defect observed" v={clock(st.trace.first_defect_observed_ts)} />
            <Fact k="In-flight units exposed" v={String(st.trace.potentially_affected_count ?? 0)} />
          </div>
          {st.trace.exposure_split?.defect_rate_after != null && (
            <p className="mt-2 text-2xs text-ink-dim">
              Units through S{st.trace.station_id} after the shift failed {pct(st.trace.exposure_split.defect_rate_after)} vs{" "}
              {pct(st.trace.exposure_split.defect_rate_before)} before (z = {st.trace.exposure_split.z}).
            </p>
          )}
        </Panel>
      )}

      {rec && rec.status === "withheld" && (
        // The floor's task when the commander will not recommend: the manual check it asks
        // for. Read-only here -- the request itself is approved by a manager.
        <Panel title="Manual check requested" tone="warn" note="the AI recommendation is withheld until this is done">
          <ul className="space-y-1 text-xs text-ink">
            {rec.options.filter((o) => o.action_type === "manual_inspection" || o.action_type === "sensor_calibration")
              .map((o) => <li key={o.option_id} className="font-bold">{o.title}</li>)}
          </ul>
          <p className="mt-2 text-2xs text-ink-faint">{a?.reason}</p>
        </Panel>
      )}
      {rec && <RecommendationCard rec={rec} a={a} actions={actions} />}

      {actions.length > 0 && (
        <Panel title="Enterprise actions & outcome" note="Reference adapters: shaped like CMMS/MES/QMS calls, no external system connected">
          <div className="space-y-3">
            {actions.map((x) => (
              <div key={x.action_id} className="border border-line p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <span className="text-xs font-bold text-ink">{x.action_type.replace(/_/g, " ")}</span>
                    <span className="text-numeric ml-2 text-2xs text-ink-faint">{x.action_id} · {x.adapter.toUpperCase()} {x.external_reference ?? ""}</span>
                  </div>
                  <Badge variant={x.status === "executed" ? "ok" : "info"}>{x.status}</Badge>
                </div>
                {x.outcome && (
                  <div className="mt-2 grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
                    <Fact k="Outcome" v={x.outcome.status.replace(/_/g, " ")} />
                    <Fact k={`${x.outcome.metric} before → after`} v={`${x.outcome.baseline_value?.toFixed?.(3) ?? "—"} → ${x.outcome.actual_value?.toFixed?.(3) ?? "…"}`} />
                    <Fact k="Predicted change (twin)" v={x.outcome.expected_change != null ? signed(x.outcome.expected_change, 3) : "no analogue"} />
                    <Fact k="Prediction error" v={x.outcome.prediction_error != null ? signed(x.outcome.prediction_error, 3) : "—"} />
                  </div>
                )}
              </div>
            ))}
          </div>
        </Panel>
      )}

      {similar.length > 0 && (
        <Panel title="Operational memory" note="Hybrid retrieval: structured filter + TF-IDF. Context only — never a policy input.">
          <div className="space-y-2">
            {similar.map((m) => (
              <div key={m.case_id} className="border-b border-line pb-2 text-xs last:border-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-bold text-ink">{m.plant_id} {m.station_label}</span>
                  <Badge variant="neutral">similarity {pct(m.similarity)}</Badge>
                  {m.human_decision && <Badge variant="info">{m.human_decision}</Badge>}
                  {m.outcome_status && <Badge variant="ok">{m.outcome_status}</Badge>}
                </div>
                <p className="mt-0.5 text-2xs text-ink-dim">
                  root cause: {m.root_cause ?? "unresolved"}{m.selected_action ? ` · action: ${m.selected_action}` : ""}
                  {m.decision_note ? ` · note: “${m.decision_note}”` : ""}
                </p>
                {m.important_differences?.map((d: string) => (
                  <p key={d} className="text-2xs text-warn-fg">≠ {d}</p>
                ))}
              </div>
            ))}
          </div>
        </Panel>
      )}
    </>
  );
}

function HypothesisBar({ h, evById }: { h: Hypothesis; evById: Map<string, Evidence> }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button className="w-full text-left" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <div className="flex items-center justify-between gap-3">
          <span className="truncate text-xs font-bold text-ink">{h.title}</span>
          <span className="flex shrink-0 items-center gap-2">
            <Badge variant={STATUS_VARIANT[h.status] ?? "neutral"}>{h.status}</Badge>
            <span className="text-numeric w-10 text-right text-xs font-bold text-ink">{pct(h.confidence)}</span>
          </span>
        </div>
        <div className="mt-1 h-2 bg-surface-3">
          <div className="h-2 bg-ai transition-[width] duration-500" style={{ width: `${Math.max(1, h.confidence * 100)}%` }} />
        </div>
      </button>
      {open && (
        <div className="mt-1.5 space-y-0.5 pl-2 text-2xs">
          <p className="text-ink-faint">{h.description} Prior {pct(h.prior)}.</p>
          {h.contributions.map((c) => (
            <p key={c.evidence_id} className={c.delta > 0 ? "text-ok-fg" : "text-danger-fg"}>
              {c.delta > 0 ? "✓" : "✗"} {signed(c.delta)} — {evById.get(c.evidence_id)?.summary}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

function RecommendationCard({ rec, a, actions }: {
  rec: NonNullable<IncidentDetail["recommendation"]>; a: Record<string, any> | null | undefined;
  actions: any[];
}) {
  const decidable = ["proposed", "withheld", "no_action"].includes(rec.status);
  const withheld = rec.status === "withheld";
  return (
    <Panel
      title={withheld ? "Root cause unresolved — recommendation withheld" : rec.status === "no_action" ? "No fault supported — no work order" : "AI recommendation"}
      tone={withheld ? "warn" : "info"}
      note={`status: ${rec.status.replace(/_/g, " ")}`}
    >
      <div className="grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
        <Fact k="Root cause" v={rec.root_cause_title ?? "unresolved"} />
        <Fact k="Confidence" v={pct(rec.confidence)} />
        <Fact k="Assessment" v={rec.assessment.replace(/_/g, " ")} />
        <Fact k="Sensor maturity" v={a?.sensor_maturity ?? "—"} />
      </div>
      {a?.reason && <p className="mt-2 text-2xs text-ink-faint">{a.reason}</p>}

      {rec.rationale && (
        <div className="mt-3">
          <div className="text-2xs font-bold uppercase tracking-[0.14em] text-ink-faint">Why this recommendation?</div>
          <ol className="mt-1 space-y-0.5 text-xs">
            {rec.rationale.points.map((p: any, i: number) => (
              <li key={p.evidence_id} className="text-ink-dim">
                {i + 1}. {p.contradicts ? <span className="text-ink-faint">[against {p.contradicts}] </span> : null}{p.text}
              </li>
            ))}
          </ol>
          <p className="mt-1 text-xs font-bold text-ink">{rec.rationale.therefore}</p>
        </div>
      )}

      <div className="mt-4 space-y-2">
        {rec.options.map((o) => (
          <OptionRow key={o.option_id} o={o} />
        ))}
      </div>

      {rec.status === "proposed" && (
        <p className="mt-3 text-2xs text-ink-faint">Awaiting a manager decision in the Decision Queue.</p>
      )}
      {!decidable && rec.decision_note && (
        <p className="mt-3 text-2xs text-ink-faint">Decided by {rec.decided_by_role}: “{rec.decision_note}”</p>
      )}
      {actions.length === 0 && !decidable && <p className="mt-2 text-2xs text-ink-faint">No action coordinated.</p>}
    </Panel>
  );
}

function OptionRow({ o }: { o: RecOption }) {
  const [open, setOpen] = useState(false);
  const g = o.governor;
  const impact = o.expected_impact;
  return (
    <div className="border border-line p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-numeric text-2xs font-bold text-ink-faint">{o.option_id}</span>
            <span className="text-xs font-bold text-ink">{o.title}</span>
          </div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            <Badge variant={GOVERNOR_VARIANT[g.decision] ?? "neutral"}>{g.decision.replace(/_/g, " ")}</Badge>
            <Badge variant="neutral">authority {g.granted_level}{g.required_level ? ` / needs ${g.required_level}` : ""}</Badge>
            <Badge variant={o.risk === "medium" ? "warn" : "neutral"}>risk {o.risk}</Badge>
            {o.adapter !== "none" && <Badge variant="neutral">{o.adapter.toUpperCase()}</Badge>}
            {!o.reversible && <Badge variant="warn">irreversible</Badge>}
          </div>
        </div>
        <div className="text-right text-2xs text-ink-faint">
          {impact ? (
            <div>
              <span className="text-numeric text-xs font-bold text-ai-fg">{signed(impact.objective_delta?.mean)}</span>{" "}
              {impact.objective_metric} <Badge variant="ai">SIMULATED</Badge>
              <div className="text-numeric">CI {signed(impact.objective_delta?.ci_low)} … {signed(impact.objective_delta?.ci_high)}</div>
            </div>
          ) : o.adapter !== "none" && o.adapter !== "notification" ? <div>impact not simulated</div> : null}
          {o.cost && (
            <div className="text-numeric">≈ ${Math.round(o.cost.value_usd).toLocaleString()} <Badge variant="neutral">ESTIMATED</Badge></div>
          )}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button className="label-upper text-2xs text-ink-dim hover:text-primary" onClick={() => setOpen((v) => !v)}>
          {open ? "Hide detail" : "Detail ›"}
        </button>
      </div>
      {open && (
        <div className="mt-2 space-y-2 text-2xs text-ink-dim">
          <ul className="space-y-0.5">{g.reasons.map((r) => <li key={r}>· {r}</li>)}</ul>
          {o.cost && <p className="text-ink-faint">{o.cost.formula}. Sources: {o.cost.sources.join("; ")}</p>}
          {o.maintenance_plan && (
            <table className="w-full text-left">
              <thead><tr className="text-ink-faint">
                <th className="py-1">When</th><th>Hours</th><th>Lost units</th><th>Defect exposure</th><th>P(failure)</th><th>Expected cost</th>
              </tr></thead>
              <tbody className="text-numeric">
                {o.maintenance_plan.options.map((p: any) => (
                  <tr key={p.label} className={cn(p.label === o.maintenance_plan!.recommended && "font-bold text-ink")}>
                    <td className="py-0.5 font-sans">{p.label}</td><td>{p.hours_until}</td><td>{p.lost_units}</td>
                    <td>{p.expected_defect_exposure ?? "—"}</td><td>{pct(p.failure_probability)}</td>
                    <td>${Math.round(p.expected_cost_usd).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {o.maintenance_plan && <p className="text-ink-faint">Rule: {o.maintenance_plan.rule}. Bases: lost units ESTIMATED, exposure {o.maintenance_plan.options[1]?.bases?.defect_exposure ?? "—"}, failure probability ESTIMATED.</p>}
        </div>
      )}
    </div>
  );
}

function Fact({ k, v }: { k: string; v: string }) {
  return (
    <div className="min-w-0">
      <div className="text-2xs font-bold uppercase tracking-[0.14em] text-ink-faint">{k}</div>
      <div className="text-numeric mt-0.5 truncate text-sm font-bold text-ink">{v}</div>
    </div>
  );
}
