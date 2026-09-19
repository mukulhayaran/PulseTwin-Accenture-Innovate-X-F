"use client";

import type { ReactNode } from "react";
import * as RT from "@radix-ui/react-tooltip";
import { cn } from "@/lib/cn";

// Replaces `title=` attributes, which wait ~1s, cannot be styled, and never appear for
// keyboard users. Radix opens on focus as well as hover.

export function TooltipProvider({ children }: { children: ReactNode }) {
  return (
    <RT.Provider delayDuration={200} skipDelayDuration={300}>
      {children}
    </RT.Provider>
  );
}

export default function Tooltip({
  content,
  children,
  side = "top",
  className,
}: {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  className?: string;
}) {
  if (!content) return <>{children}</>;

  return (
    <RT.Root>
      <RT.Trigger asChild>{children}</RT.Trigger>
      <RT.Portal>
        <RT.Content
          side={side}
          sideOffset={6}
          collisionPadding={8}
          className={cn(
            "z-50 max-w-72 rounded-lg border border-line-strong bg-surface px-2.5 py-1.5",
            "text-2xs leading-relaxed text-ink-dim shadow-pop",
            className
          )}
        >
          {content}
          <RT.Arrow className="fill-surface" width={10} height={5} />
        </RT.Content>
      </RT.Portal>
    </RT.Root>
  );
}
