"use client";

// Sits above everything else in a persona view when the SSE/API connection to the
// simulator has dropped. Renders nothing at all (not even a hidden node) while
// connected, so it never reserves layout space -- a phantom offline bar would
// misalign every panel beneath it.

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import Button from "@/components/ui/Button";

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
  // `connected` flips true, and since the component renders nothing then, the interval
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

  const timeLabel = lastKnownAt ? new Date(lastKnownAt).toLocaleTimeString() : "unknown";
  const agoLabel = lastKnownAt ? relativeAgo(lastKnownAt, now) : "unknown";

  return (
    <AnimatePresence>
      {!connected && (
        <motion.div
          role="alert"
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.22, ease: [0.2, 0.8, 0.25, 1] }}
          className="sticky top-0 z-50 overflow-hidden border-b border-warn/50 bg-warn/[0.09] backdrop-blur-md"
        >
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-4 py-2">
            <span className="flex shrink-0 items-center gap-2 text-xs font-bold text-danger-fg">
              <motion.span
                className="size-2 rounded-full bg-danger"
                animate={{ opacity: [1, 0.25, 1] }}
                transition={{ duration: 1.4, repeat: Infinity }}
              />
              Twin offline
            </span>

            <span className="text-xs text-ink-dim">
              Last known state from{" "}
              <span className="text-numeric text-warn-fg">{timeLabel}</span>{" "}
              <span className="text-ink-faint">({agoLabel} ago)</span>
            </span>

            <span className="text-numeric text-2xs text-ink-faint">
              retrying in {secondsToRetry}s…
            </span>

            <Button
              variant="warn"
              size="xs"
              className="ml-auto"
              onClick={() => {
                onRetry();
                setSecondsToRetry(AUTO_RETRY_SECONDS);
              }}
            >
              Retry now
            </Button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
