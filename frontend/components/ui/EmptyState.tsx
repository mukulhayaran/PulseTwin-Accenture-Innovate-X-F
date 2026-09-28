// One empty state for the whole app (ADR-075), replacing six hand-written variants.
// An empty screen is a place to say what to do next, so `description` should name the
// next step. The dot texture is Magic UI's DotPattern, static and faded at the edges --
// no motion, because these sit on live control-room views.

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { DotPattern } from "@/components/magicui/dot-pattern";
import { IconCheck } from "./icons";

export default function EmptyState({
  title,
  description,
  tone = "neutral",
  className,
}: {
  title: string;
  description?: ReactNode;
  /** `ok` is the "all clear" state: nothing is wrong, rather than nothing is here. */
  tone?: "neutral" | "ok";
  className?: string;
}) {
  return (
    <div className={cn("relative flex flex-col items-center gap-2 overflow-hidden px-4 py-10 text-center", className)}>
      <DotPattern
        width={14}
        height={14}
        className="[mask-image:radial-gradient(closest-side,#000,transparent)]"
      />
      {tone === "ok" && (
        <span className="relative flex size-8 items-center justify-center rounded-full border border-ok/30 bg-ok/10 text-ok-fg">
          <IconCheck size={15} />
        </span>
      )}
      <p className="relative text-xs font-bold text-ink">{title}</p>
      {description && (
        <p className="relative max-w-64 text-2xs leading-relaxed text-ink-faint">{description}</p>
      )}
    </div>
  );
}
