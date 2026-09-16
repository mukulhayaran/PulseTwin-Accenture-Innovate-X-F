"use client";

// A hook rather than a controlled component: every call site that mutates state
// (feedback vote, what-if run, alert acknowledge) just wants to fire-and-forget a
// confirmation, not own a message/visible/timer triple itself. `useAuditToast()`
// owns the timer once; callers get a `show()` and drop `<ToastElement />` once in
// their tree.

import { useCallback, useEffect, useRef, useState } from "react";

const AUTO_DISMISS_MS = 2500;

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

  const ToastElement = message ? (
    <div
      role="status"
      onClick={dismiss}
      className="fixed bottom-4 right-4 z-50 flex cursor-pointer items-center gap-2 rounded border border-signal-green/40 bg-bg-card px-3 py-2 font-mono text-[11px] text-txt-primary shadow-lg"
    >
      <span className="text-signal-green">✓</span>
      <span>{message}</span>
    </div>
  ) : null;

  return { show, dismiss, ToastElement };
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

  if (!visible) return null;

  return (
    <div
      role="status"
      onClick={onDismiss}
      className="fixed bottom-4 right-4 z-50 flex cursor-pointer items-center gap-2 rounded border border-signal-green/40 bg-bg-card px-3 py-2 font-mono text-[11px] text-txt-primary shadow-lg"
    >
      <span className="text-signal-green">✓</span>
      <span>{message}</span>
    </div>
  );
}
