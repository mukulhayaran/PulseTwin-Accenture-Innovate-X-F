"use client";

// The AI Operations Copilot.
//
// Thread id lives in the URL HASH (`/copilot#t=<id>`), not a dynamic route and not
// localStorage:
//   * `next.config.ts` sets `output: "export"`, so there is no server to resolve
//     /copilot/[threadId] against;
//   * CLAUDE.md §7 bans localStorage in components -- persistent UI state is derived
//     server-side, and a thread id in the hash is shareable, which localStorage is not.
//
// This page pins itself to `manager`, the minimum role /api/triage/runs accepts. The
// server caps the acting role at the caller's own anyway (`_cap_role`), so a lower role
// would simply make every gated tool return a 403 the agent then has to report.

import { useCallback, useEffect, useState } from "react";
import {
  TriageRun, TriageThread, getHealth, listTriageThreads, setRole,
} from "@/lib/api";
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

export default function CopilotPage() {
  const [plant, setPlant] = useState(PLANT_OPTIONS[0].value);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [threads, setThreads] = useState<TriageThread[]>([]);
  const [connected, setConnected] = useState(true);
  const [lastKnownAt, setLastKnownAt] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<TriageRun | null>(null);
  const [ready, setReady] = useState(false);
  const { show, ToastElement } = useAuditToast();

  useEffect(() => {
    setRole("manager");
    setThreadId(readHash());
    setReady(true);
  }, []);

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
    listTriageThreads(plant, 12)
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
      role="manager"
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
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <Panel
          title="Operations copilot"
          note="Ask in plain language. The agent calls the same read-only API you can, and every number it reports is re-checked against the tool results it saw."
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
            >
              {(ask, busy) => (
                <CopilotComposer plant={plant} ask={ask} busy={busy} />
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
              <ul className="space-y-1">
                {threads.map((t) => (
                  <li key={t.thread_id}>
                    <button
                      type="button"
                      onClick={() => setThreadId(t.thread_id)}
                      className={
                        "w-full rounded-md border px-2.5 py-1.5 text-left transition-colors " +
                        (t.thread_id === threadId
                          ? "border-ai/45 bg-ai/[0.07]"
                          : "border-line bg-surface-2/50 hover:border-line-strong")
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
