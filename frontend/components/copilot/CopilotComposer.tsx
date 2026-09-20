"use client";

// The input: free text, suggested questions, and the named actions.
//
// Two rules this file exists to enforce:
//
//  * a named action posts an INTENT KEY, never a canned English sentence. The
//    deterministic path selects its plan on the same key, which is why every button here
//    still works with no provider credential.
//  * a station id is STRUCTURED. The what-if button takes the station from the live
//    constraint, never from regexing "S17" out of whatever was typed -- CLAUDE.md §1
//    bans station literals, and parsing prose is how a copilot simulates the wrong
//    station with complete confidence.

import { useEffect, useState } from "react";
import { Bottleneck, StartTriageArgs, TriageIntent, getBottleneck } from "@/lib/api";
import { Button } from "@/components/ui";

const MAX_CHARS = 500;

type Ask = (args: Omit<StartTriageArgs, "plant">) => void;

export default function CopilotComposer({
  plant,
  ask,
  busy,
  compact,
}: {
  plant: string;
  ask: Ask;
  busy: boolean;
  compact?: boolean;
}) {
  const [text, setText] = useState("");
  const [bn, setBn] = useState<Bottleneck | null>(null);

  useEffect(() => {
    let cancelled = false;
    getBottleneck(plant)
      .then((b) => !cancelled && setBn(b))
      .catch(() => !cancelled && setBn(null));
    return () => {
      cancelled = true;
    };
  }, [plant]);

  const station = bn?.current_station ?? null;
  const stationLabel = station != null ? `S${station}` : "the constraint";

  // Generated from the LIVE constraint, never hardcoded.
  const suggestions: string[] = [
    station != null
      ? `Why is ${stationLabel} showing elevated defect risk?`
      : "Why is defect risk increasing?",
    "What is causing the current production bottleneck?",
    "Which vehicles are potentially affected?",
    "What changed during the last 30 minutes?",
    "Which stations need investigation?",
  ];

  const actions: { intent: TriageIntent; label: string; needsStation?: boolean }[] = [
    { intent: "incident", label: "Investigate incident" },
    { intent: "whatif", label: `Run what-if for ${stationLabel}`, needsStation: true },
    { intent: "shift_intel", label: "Shift intelligence" },
    { intent: "handover", label: "Shift handover" },
  ];

  const send = () => {
    const q = text.trim();
    if (!q || busy) return;
    ask({ question: q, intent: "ask" });
    setText("");
  };

  return (
    <div className="mt-3 space-y-2 border-t border-line pt-3">
      {!compact && (
        <div className="flex flex-wrap gap-1.5">
          {suggestions.map((s) => (
            <button
              key={s}
              type="button"
              disabled={busy}
              onClick={() => ask({ question: s, intent: "ask" })}
              className="rounded-md border border-line bg-surface-2 px-2 py-1 text-2xs text-ink-dim transition-colors hover:border-line-strong hover:text-ink disabled:opacity-45"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      <div className="flex gap-2">
        <input
          value={text}
          maxLength={MAX_CHARS}
          disabled={busy}
          placeholder="Ask about the line…"
          aria-label="Ask the copilot"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") send();
          }}
          className="min-w-0 flex-1 rounded-md border border-line bg-surface-2 px-2.5 py-1.5 text-xs text-ink placeholder:text-ink-faint focus-visible:border-ai/50 disabled:opacity-45"
        />
        <Button variant="ai" size="sm" disabled={busy || !text.trim()} onClick={send}>
          {busy ? "Working…" : "Ask"}
        </Button>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {actions.map((a) => {
          const blocked = busy || (a.needsStation && station == null);
          return (
            <Button
              key={a.intent}
              variant="outline"
              size="xs"
              disabled={blocked}
              onClick={() =>
                ask({
                  intent: a.intent,
                  stationId: a.needsStation ? station : null,
                })
              }
            >
              {a.label}
            </Button>
          );
        })}
      </div>
    </div>
  );
}
