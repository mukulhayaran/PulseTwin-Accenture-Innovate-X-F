"use client";

// "Why is the AI recommending this?" -- concise, on demand (ADR-071). Every line is the
// investigation's persisted rationale or a stored twin experiment; nothing is composed here.

import { useEffect, useState } from "react";
import { type DecisionEvidence, getDecisionEvidence } from "@/lib/api";
import { Badge, Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, Skeleton } from "@/components/ui";

const signed = (v: number | null | undefined) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}`);

export default function DecisionEvidenceDrawer({ recId, title, onClose }: {
  recId: string | null; title: string; onClose: () => void;
}) {
  const [ev, setEv] = useState<DecisionEvidence | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!recId) return;
    setEv(null); setErr(null);
    getDecisionEvidence(recId).then(setEv).catch((e) => setErr(String(e?.message ?? e)));
  }, [recId]);
  // Radix Dialog (via Sheet) owns Esc, outside-click, the focus trap and returning focus
  // to the "Why?" button that opened it -- the hand-rolled drawer did only the first two.
  return (
    <Sheet open={!!recId} onOpenChange={(open) => { if (!open) onClose(); }}>
      <SheetContent className="max-w-lg p-5">
            <SheetHeader className="mb-4">
              <SheetDescription className="text-2xs font-bold uppercase tracking-[0.14em] text-ink-faint">
                Explain AI decision
              </SheetDescription>
              <SheetTitle>{title}</SheetTitle>
            </SheetHeader>
            {err && <p className="text-xs text-danger-fg">{err}</p>}
            {!ev && !err && <Skeleton className="h-48 w-full" />}
            {ev && (
              // LOOP criterion H, in the order a person would ask: what did it see, what did
              // it look at, what could it be, what was ruled out, what mattered, what did the
              // twin say, what is recommended, and why this needs you.
              <div className="space-y-5">
                {ev.observation && <Block title="Observation"><p className="text-xs text-ink">{ev.observation}</p></Block>}
                {ev.investigated.length > 0 && (
                  <Block title="What the AI investigated">
                    <ol className="space-y-1 text-xs text-ink-dim">
                      {ev.investigated.map((s, i) => (
                        <li key={i}><span className="font-bold text-ink">{s.specialist.replace(/_/g, " ")}</span> — {s.what}</li>
                      ))}
                    </ol>
                  </Block>
                )}
                {ev.explanations.length > 0 && (
                  <Block title="Possible explanations">
                    <ul className="space-y-1">
                      {ev.explanations.map((h) => (
                        <li key={h.title} className="flex items-center justify-between gap-2 text-xs">
                          <span className={h.status === "rejected" ? "text-ink-faint line-through" : "text-ink"}>{h.title}</span>
                          <span className="flex shrink-0 items-center gap-1.5">
                            <Badge variant={h.status === "supported" ? "ok" : h.status === "rejected" ? "neutral" : "warn"}>
                              {h.status === "rejected" ? "ruled out" : h.status}
                            </Badge>
                            <span className="text-numeric w-9 text-right text-ink-dim">{Math.round(h.confidence * 100)}%</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  </Block>
                )}
                <Block title="Evidence that mattered">
                  <ul className="space-y-1">
                    {ev.points.map((p, i) => (
                      <li key={i} className="text-xs text-ink">
                        <span className={p.supports ? "text-ok-fg" : "text-ink-faint"}>{p.supports ? "✓" : "✗"}</span>{" "}
                        {p.text}
                        {p.against && <span className="text-2xs text-ink-faint"> (rules out {p.against.replace(/_/g, " ")})</span>}
                      </li>
                    ))}
                  </ul>
                </Block>
                <Block title="Digital twin experiment">
                  {ev.twin_tests.length === 0 ? (
                    <p className="text-xs text-ink-faint">No twin test completed for this decision.</p>
                  ) : ev.twin_tests.map((t) => (
                    <div key={t.experiment_id} className="mb-2 border border-line p-2.5 text-xs">
                      <div className="font-bold text-ink">{t.label}</div>
                      <div className="text-2xs text-ink-faint">tests “{t.tests}” · baseline vs this change, paired seeds</div>
                      <div className="mt-1.5 grid grid-cols-2 gap-2">
                        <div><div className="text-2xs text-ink-faint">Baseline</div>
                          <div className="text-numeric font-bold text-ink">{t.baseline_uph != null ? `${t.baseline_uph.toFixed(1)} cars/h` : "—"}</div></div>
                        <div><div className="text-2xs text-ink-faint">Result ({t.metric})</div>
                          <div className="text-numeric font-bold text-ai-fg">{signed(t.mean)}</div>
                          <div className="text-numeric text-2xs text-ink-faint">95% range {signed(t.ci_low)} … {signed(t.ci_high)}</div></div>
                      </div>
                      <div className="mt-1 text-2xs text-ink-dim">
                        {t.throughput_delta != null && `Throughput ${signed(t.throughput_delta)} cars/h. `}Result {t.effect} the explanation.
                      </div>
                    </div>
                  ))}
                </Block>
                <Block title="Recommendation">
                  <p className="text-xs font-bold text-ink">{ev.recommendation ?? "—"}</p>
                  {ev.conclusion && <p className="mt-0.5 text-2xs text-ink-dim">{ev.conclusion}</p>}
                </Block>
                {ev.approval && (
                  <Block title="Why your approval is required">
                    <ul className="space-y-0.5 text-xs text-ink-dim">
                      {ev.approval.reasons.map((r) => <li key={r}>· {r}</li>)}
                    </ul>
                  </Block>
                )}
              </div>
            )}
      </SheetContent>
    </Sheet>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <div className="mb-1.5 text-2xs font-bold uppercase tracking-[0.14em] text-ink-faint">{title}</div>
      {children}
    </section>
  );
}
