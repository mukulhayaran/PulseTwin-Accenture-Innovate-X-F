"use client";

// Ask Copilot: a natural, multi-turn conversation with the system (intent "ask"). A turn
// reads live plant data through the read-only API and retrieves matching passages from
// the engineering knowledge base (RAG). The agents are separate: one card each on
// /manager and /ops (AgentPanel), and their runs never appear in this thread list.
//
// Thread id lives in the URL HASH (`/copilot#t=<id>`), not a dynamic route and not
// localStorage:
//   * `next.config.ts` sets `output: "export"`, so there is no server to resolve
//     /copilot/[threadId] against;
//   * CLAUDE.md §7 bans localStorage in components -- persistent UI state is derived
//     server-side, and a thread id in the hash is shareable, which localStorage is not.
//
// This page pins itself to the SESSION's own role. /api/triage/runs accepts `manager` at
// minimum, and lib/routeAccess.ts already restricts entry to a manager, leadership or
// system session -- so the session role is always at or above that minimum and there is
// nothing left for a flat `setRole("manager")` to protect. It only did harm: pinning the
// literal "manager" CAPPED a higher session, so a leadership or system viewer was refused
// the leadership-gated panels (model health, drift, fallback) that their own role grants,
// and AiTrustPanel correctly reported "requires leadership" to someone who outranks it.
// The server still caps the acting role at the caller's own (`_cap_role`), so this pins
// rather than grants.

import { useCallback, useEffect, useState } from "react";
import {
  TriageRun, TriageThread, getHealth, listTriageThreads, setRole,
} from "@/lib/api";
import { usePersonaAccess } from "@/lib/usePersonaAccess";
import SignInGate from "@/components/shared/SignInGate";
import {
  AppShell, Badge, Button, PLANT_OPTIONS, Panel, Skeleton,
} from "@/components/ui";
import AiTrustPanel from "@/components/shared/AiTrustPanel";
import CopilotComposer from "@/components/copilot/CopilotComposer";
import CopilotThread from "@/components/copilot/CopilotThread";
import { useAuditToast } from "@/components/shared/AuditToast";

function readHash(): string | null {
  if (typeof window === "undefined") return null;
  const m = window.location.hash.match(/(?:^|[#&])t=([A-Za-z0-9_-]+)/);
  return m ? m[1] : null;
}

// `#q=` is how the shell's search box hands a typed question over (ADR-047). It seeds
// the composer and is then dropped from the URL, so a reload does not silently
// re-populate the box with a question the user already dealt with.
function readDraft(): string {
  if (typeof window === "undefined") return "";
  const m = window.location.hash.match(/(?:^|[#&])q=([^&]+)/);
  if (!m) return "";
  try {
    return decodeURIComponent(m[1]);
  } catch {
    return "";
  }
}

export default function CopilotPage() {
  const [plant, setPlant] = useState(PLANT_OPTIONS[0].value);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [threads, setThreads] = useState<TriageThread[]>([]);
  const [connected, setConnected] = useState(true);
  const [lastKnownAt, setLastKnownAt] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<TriageRun | null>(null);
  const [ready, setReady] = useState(false);
  const [draft, setDraft] = useState("");
  const { show, ToastElement } = useAuditToast();

  const access = usePersonaAccess("manager");
  const granted = access.status === "granted";
  const grantedRole = access.status === "granted" ? access.role : null;

  useEffect(() => {
    if (!granted || !grantedRole) return;
    // Pin the SESSION's own role, not a flat "manager". routeAccess already guarantees
    // entry is manager, leadership or system, so this is never below the minimum
    // /api/triage/runs accepts -- and unlike a hardcoded "manager" it does not CAP a
    // higher session, which silently 403'd the leadership-gated panels (model health,
    // drift, fallback) for a leadership or system viewer who outranks them.
    setRole(grantedRole);
    setThreadId(readHash());
    setDraft(readDraft());
    setReady(true);
  }, [granted, grantedRole]);

  // Keep the hash in step, so a conversation can be linked to and survives a reload.
  useEffect(() => {
    if (!ready || typeof window === "undefined") return;
    const next = threadId ? `#t=${threadId}` : "";
    if (window.location.hash !== next) {
      window.history.replaceState(null, "", `${window.location.pathname}${next}`);
    }
  }, [threadId, ready]);

  useEffect(() => {
    if (!ready) return;
    let stop = false;
    const tick = async () => {
      try {
        await getHealth();
        if (!stop) {
          setConnected(true);
          setLastKnownAt(new Date().toISOString());
        }
      } catch {
        if (!stop) setConnected(false);
      }
    };
    tick();
    const id = setInterval(tick, 10_000);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, [ready]);

  const loadThreads = useCallback(() => {
    listTriageThreads(plant, 12, "ask")
      .then((t) => setThreads(t.threads))
      .catch(() => setThreads([]));
  }, [plant]);

  useEffect(() => {
    if (ready) loadThreads();
  }, [ready, loadThreads]);

  const onThreadChange = useCallback(
    (id: string | null) => {
      setThreadId(id);
      loadThreads();
    },
    [loadThreads]
  );

  if (access.status === "pending") return null;
  if (access.status === "anonymous") return <SignInGate view="Copilot" required={["manager", "leadership", "system"]} />;
  if (access.status === "denied") {
    return <SignInGate view="Copilot" required={["manager", "leadership", "system"]} current={access.role} />;
  }

  return (
    <AppShell
      persona="Copilot"
      plant={plant}
      onPlantChange={(p) => {
        setPlant(p);
        // A thread is plant-scoped server-side (its carried values are plant facts), so
        // switching plants starts a new conversation rather than 409-ing on the next turn.
        setThreadId(null);
      }}
      role={grantedRole ?? "manager"}
      connected={connected}
      lastKnownAt={lastKnownAt}
      onRetry={() => setLastKnownAt(new Date().toISOString())}
      status={
        <Badge variant="ai" dot>
          read-only
        </Badge>
      }
      controls={
        <Button
          variant="outline"
          size="xs"
          onClick={() => onThreadChange(null)}
          disabled={!threadId}
        >
          New conversation
        </Button>
      }
    >
      {ToastElement}
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <Panel
          title="Ask Copilot"
          note="Talk to the plant in plain language. Each answer draws on live line data and on passages retrieved from the engineering knowledge base, and every number and quote is re-checked before you see it."
          bodyClassName="flex min-h-[60vh] flex-col"
        >
          {!ready ? (
            <div className="space-y-2">
              <Skeleton className="h-3 w-2/3" />
              <Skeleton className="h-3 w-full" />
            </div>
          ) : (
            <CopilotThread
              plant={plant}
              threadId={threadId}
              onThreadChange={onThreadChange}
              onAction={show}
              onTurnComplete={setLastRun}
              emptyHint="Ask anything about the plant — live status, risks, or procedures."
            >
              {(ask, busy) => (
                <CopilotComposer ask={ask} busy={busy} draft={draft} />
              )}
            </CopilotThread>
          )}
        </Panel>

        <div className="space-y-4">
          <AiTrustPanel plant={plant} run={lastRun} />

          <Panel title="Conversations" note="Earlier threads on this plant.">
            {threads.length === 0 ? (
              <p className="py-4 text-center text-2xs text-ink-faint">
                No conversations yet.
              </p>
            ) : (
              <ul className="scroll-fade-b max-h-[26rem] space-y-1 overflow-y-auto pb-4">
                {threads.map((t) => (
                  <li key={t.thread_id}>
                    <button
                      type="button"
                      onClick={() => setThreadId(t.thread_id)}
                      className={
                        "w-full rounded-md border px-2.5 py-1.5 text-left transition-colors " +
                        (t.thread_id === threadId
                          ? "border-ai/45 bg-ai/[0.07]"
                          : "border-line bg-surface-2 hover:border-line-strong")
                      }
                    >
                      <span className="block truncate text-2xs text-ink">{t.title}</span>
                      <span className="text-numeric mt-0.5 block text-2xs text-ink-faint">
                        {t.turn_count} turn{t.turn_count === 1 ? "" : "s"}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </AppShell>
  );
}
