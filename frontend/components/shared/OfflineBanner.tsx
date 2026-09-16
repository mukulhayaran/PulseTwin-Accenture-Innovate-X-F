"use client";

// Sits above everything else in a persona view when the SSE/API connection to the
// simulator has dropped. Renders nothing at all (not even a hidden node) while
// connected, so it never reserves layout space -- CLAUDE.md control-room aesthetic is
// dense already; a phantom offline bar would misalign every panel beneath it.

import { useEffect, useState } from "react";

interface OfflineBannerProps {
  connected: boolean;
  lastKnownAt: string | null;
  onRetry: () => void;
}

const AUTO_RETRY_SECONDS = 15;

function relativeAgo(iso: string, nowMs: number): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "unknown";
  const deltaS = Math.max(0, Math.round((nowMs - then) / 1000));
  if (deltaS < 60) return `${deltaS}s`;
  const deltaM = Math.floor(deltaS / 60);
  if (deltaM < 60) return `${deltaM}m ${deltaS % 60}s`;
  const deltaH = Math.floor(deltaM / 60);
  return `${deltaH}h ${deltaM % 60}m`;
}

export default function OfflineBanner({ connected, lastKnownAt, onRetry }: OfflineBannerProps) {
  const [now, setNow] = useState(() => Date.now());
  const [secondsToRetry, setSecondsToRetry] = useState(AUTO_RETRY_SECONDS);

  // Auto-retry ticker only runs while disconnected -- it fully resets the moment
  // `connected` flips true, and since the component returns null then, the interval
  // is torn down along with it.
  useEffect(() => {
    if (connected) return;
    setSecondsToRetry(AUTO_RETRY_SECONDS);
    const tick = setInterval(() => {
      setNow(Date.now());
      setSecondsToRetry((s) => {
        if (s <= 1) {
          onRetry();
          return AUTO_RETRY_SECONDS;
        }
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(tick);
    // onRetry is a stable callback from the parent's perspective for the lifetime of
    // one disconnect; re-running this effect on identity churn would restart the
    // countdown mid-flight for no reason.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected]);

  if (connected) return null;

  const timeLabel = lastKnownAt ? new Date(lastKnownAt).toLocaleTimeString() : "unknown";
  const agoLabel = lastKnownAt ? relativeAgo(lastKnownAt, now) : "unknown";

  return (
    <div className="sticky top-0 z-50 flex flex-wrap items-center gap-x-4 gap-y-1 border-b-2 border-signal-amber bg-bg-card px-4 py-2 font-mono text-[12px]">
      <span className="text-signal-red font-semibold">⚠ TWIN OFFLINE</span>
      <span className="text-txt-primary">
        Last known state from <span className="text-signal-amber">{timeLabel}</span>{" "}
        <span className="text-txt-muted">({agoLabel} ago)</span>
      </span>
      <span className="text-txt-muted text-[11px]">retrying in {secondsToRetry}s…</span>
      <button
        onClick={() => { onRetry(); setSecondsToRetry(AUTO_RETRY_SECONDS); }}
        className="ml-auto rounded border border-signal-amber px-2 py-0.5 text-signal-amber hover:bg-signal-amber/10"
      >
        [Retry Now]
      </button>
    </div>
  );
}
