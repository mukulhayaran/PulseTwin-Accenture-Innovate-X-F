"use client";

// A hook rather than a controlled component: every call site that mutates state
// (feedback vote, what-if run, alert acknowledge) just wants to fire-and-forget a
// confirmation, not own a message/visible/timer triple itself. `useAuditToast()`
// owns the timer once; callers get a `show()` and drop `<ToastElement />` once in
// their tree.

import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";

const AUTO_DISMISS_MS = 2500;

function Toast({ message, onDismiss }: { message: string | null; onDismiss: () => void }) {
  return (
    <AnimatePresence>
      {message && (
        <motion.div
          role="status"
          onClick={onDismiss}
          initial={{ opacity: 0, y: 12, scale: 0.96 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 8, scale: 0.98 }}
          transition={{ duration: 0.2, ease: [0.2, 0.8, 0.25, 1] }}
          // Bottom-LEFT, not right: the copilot launcher (components/copilot/CopilotDock)
          // owns the bottom-right corner on every persona page, and grew enough (h-11,
          // pill, icon) that a bottom-right toast would sit on top of it.
          className="fixed bottom-5 left-5 z-50 flex cursor-pointer items-center gap-2 rounded-lg border border-ok/35 bg-surface px-3 py-2 text-xs text-ink shadow-pop"
        >
          <span className="flex size-4 shrink-0 items-center justify-center rounded-full bg-ok/15 text-2xs text-ok-fg">
            ✓
          </span>
          {message}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function useAuditToast() {
  const [message, setMessage] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const show = useCallback((msg: string = "Action logged") => {
    if (timer.current) clearTimeout(timer.current);
    setMessage(msg);
    timer.current = setTimeout(() => setMessage(null), AUTO_DISMISS_MS);
  }, []);

  const dismiss = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    setMessage(null);
  }, []);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  return { show, dismiss, ToastElement: <Toast message={message} onDismiss={dismiss} /> };
}

// Controlled variant for a call site that already owns its own visibility state
// rather than using the hook above.
interface AuditToastProps {
  message: string;
  visible: boolean;
  onDismiss: () => void;
}

export default function AuditToast({ message, visible, onDismiss }: AuditToastProps) {
  useEffect(() => {
    if (!visible) return;
    const t = setTimeout(onDismiss, AUTO_DISMISS_MS);
    return () => clearTimeout(t);
  }, [visible, onDismiss]);

  return <Toast message={visible ? message : null} onDismiss={onDismiss} />;
}
