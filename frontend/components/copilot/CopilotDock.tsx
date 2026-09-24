"use client";

// The copilot, available from wherever the operator already is.
//
// It renders the SAME CopilotThread as /copilot, so a conversation started in the dock
// can be opened full-page and continued -- the thread id is the only state that has to
// travel, and it travels in the URL hash.
//
// Mounted on the persona views, which pin themselves to a role. `/api/triage/runs`
// requires `manager`, so on /supervisor the dock would 403 on every send; it therefore
// renders a short explanation instead of a broken input. That is the role gate doing its
// job visibly, which is more useful in a demo than hiding the button.

import { useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Role, StartTriageArgs, TriageRun, getRole, getSession } from "@/lib/api";
import { Badge, Button } from "@/components/ui";
import CopilotComposer from "./CopilotComposer";
import CopilotThread from "./CopilotThread";

const ALLOWED: Role[] = ["manager", "leadership", "system"];

export default function CopilotDock({
  plant,
  onAction,
}: {
  plant: string;
  onAction?: (msg: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [role, setRoleState] = useState<Role | null>(null);
  const [signedOut, setSignedOut] = useState(false);
  const [lastRun, setLastRun] = useState<TriageRun | null>(null);
  const reduce = useReducedMotion();

  // Read on mount rather than at module scope: the role lives in a module variable in
  // lib/api.ts and each persona page sets it in its own mount effect, so reading during
  // render can race that.
  useEffect(() => {
    setRoleState(getRole());
    setSignedOut(getSession() === null);
  }, []);

  const permitted = role != null && ALLOWED.includes(role);

  return (
    <>
      <div className="fixed bottom-6 right-6 z-40">
        <Button
          variant="ai"
          size="lg"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls="copilot-dock"
          className="rounded-full shadow-pop"
        >
          <svg
            aria-hidden
            width="18"
            height="18"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.4"
          >
            <path
              d="M2 4.5A2.5 2.5 0 0 1 4.5 2h7A2.5 2.5 0 0 1 14 4.5v4A2.5 2.5 0 0 1 11.5 11H7l-3.2 2.4a.5.5 0 0 1-.8-.4V11h-.5A2.5 2.5 0 0 1 2 8.5v-4Z"
              strokeLinejoin="round"
            />
          </svg>
          {open ? "Close copilot" : "Ask the copilot"}
        </Button>
      </div>

      <AnimatePresence>
        {open && (
          <motion.aside
            id="copilot-dock"
            initial={reduce ? false : { opacity: 0, y: 12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, y: 12, scale: 0.98 }}
            transition={{ duration: 0.2, ease: [0.2, 0.8, 0.25, 1] }}
            className="fixed bottom-24 right-6 z-40 flex max-h-[76vh] w-[min(28rem,calc(100vw-2rem))] flex-col rounded-xl border border-line-strong bg-surface shadow-pop"
          >
            <header className="flex items-center justify-between border-b border-line px-3 py-2">
              <div className="flex items-center gap-2">
                <span className="text-2xs font-bold uppercase tracking-[0.14em] text-ink-faint">
                  Copilot
                </span>
                <Badge variant="ai">read-only</Badge>
              </div>
              {threadId && (
                <a
                  href={`/copilot#t=${threadId}`}
                  className="text-2xs text-ink-faint underline-offset-2 hover:text-ink-dim hover:underline"
                >
                  Open full page
                </a>
              )}
            </header>

            <div className="flex min-h-0 flex-1 flex-col p-3">
              {!permitted ? (
                <div className="py-6 text-center">
                  <p className="text-xs text-ink-dim">
                    The copilot needs the manager role or above.
                  </p>
                  <p className="mt-1 text-2xs leading-relaxed text-ink-faint">
                    This view is pinned to{" "}
                    <span className="text-numeric">{role ?? "an unknown role"}</span>. The
                    server caps an agent&apos;s acting role at the caller&apos;s own, so a
                    run started here could not read anything you cannot.
                  </p>
                  {/* The floor view is open to anyone, so this dock is the first place a
                      signed-out visitor meets the gate. Pointing at /login is a shorter
                      answer than explaining a role they have no way to acquire from here. */}
                  {signedOut && (
                    <Link href="/copilot" className="mt-3 inline-block">
                      <Button variant="outline" size="xs">
                        Sign in to use the copilot
                      </Button>
                    </Link>
                  )}
                </div>
              ) : (
                <CopilotThread
                  plant={plant}
                  threadId={threadId}
                  onThreadChange={setThreadId}
                  onAction={onAction}
                  onTurnComplete={setLastRun}
                  compact
                >
                  {(ask: (a: Omit<StartTriageArgs, "plant">) => void, busy: boolean) => (
                    <CopilotComposer plant={plant} ask={ask} busy={busy} compact />
                  )}
                </CopilotThread>
              )}
            </div>
          </motion.aside>
        )}
      </AnimatePresence>
    </>
  );
}
