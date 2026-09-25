"use client";

// The copilot's input. Kept apart from the agents on purpose:
//
//  * CopilotComposer -- "Ask Copilot": a natural conversation with the system (intent
//    "ask"). Each turn reads the live plant through the read-only API AND retrieves
//    matching passages from the engineering knowledge base (RAG); every number and quote
//    is verified before it is shown. Used by /copilot and the dock.
//  * The agents are NOT here: each has its own card on /manager and /ops (AgentPanel.tsx).

import { useEffect, useState } from "react";
import { StartTriageArgs } from "@/lib/api";
import { Button } from "@/components/ui";

const MAX_CHARS = 500;

type Ask = (args: Omit<StartTriageArgs, "plant">) => void;

// Half about the running line, half about procedures -- the two sources a turn draws on.
// No station literals (CLAUDE.md §1): "the bottleneck" resolves against live data.
const SUGGESTIONS: string[] = [
  "What is causing the current bottleneck?",
  "Which vehicles are at risk right now?",
  "What changed during this shift?",
  "What is the torque verification procedure?",
  "When must a unit be held for containment?",
];

export default function CopilotComposer({
  ask,
  busy,
  compact,
  draft,
}: {
  ask: Ask;
  busy: boolean;
  compact?: boolean;
  /** Seeds the box — the shell's search sends a typed question here via `#q=`. */
  draft?: string;
}) {
  const [text, setText] = useState(draft ?? "");

  // Seed, do not control: arriving with a draft still lets the user edit or clear it
  // before sending, rather than dispatching a run the moment the page mounts.
  useEffect(() => {
    if (draft) setText(draft);
  }, [draft]);

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
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              disabled={busy}
              onClick={() => ask({ question: s, intent: "ask" })}
              className="border border-line bg-surface-2 px-2 py-1 text-2xs text-ink-dim transition-colors hover:border-line-strong hover:text-ink disabled:opacity-45"
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
          placeholder="Ask anything about the plant — live status, risks, procedures…"
          aria-label="Ask the copilot"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") send();
          }}
          className="h-9 min-w-0 flex-1 border border-line-strong bg-canvas px-3 text-sm text-ink placeholder:text-ink-faint hover:border-ink focus-visible:border-primary disabled:opacity-45"
        />
        <Button variant="primary" size="sm" disabled={busy || !text.trim()} onClick={send}>
          {busy ? "Working…" : "Ask"}
        </Button>
      </div>
    </div>
  );
}
