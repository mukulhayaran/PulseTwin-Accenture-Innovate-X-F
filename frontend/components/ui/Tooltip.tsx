"use client";

import { cloneElement, isValidElement, type ReactElement, type ReactNode } from "react";
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

  // Radix opens on focus as well as hover -- but only if the trigger can actually BE
  // focused, and most triggers here are plain <span>/<div>/<label>. Without this, a
  // keyboard user never sees any of these tooltips, which defeats the whole reason for
  // replacing title= attributes. Adding tabIndex to the existing element (rather than
  // wrapping it) keeps the box model untouched, which matters for the fixed-width
  // header items. Elements that already declare a tabIndex, and native buttons, are
  // left alone.
  const trigger =
    isValidElement(children) &&
    (children as ReactElement<{ tabIndex?: number }>).props.tabIndex === undefined &&
    children.type !== "button"
      ? cloneElement(children as ReactElement<{ tabIndex?: number }>, { tabIndex: 0 })
      : children;

  return (
    <RT.Root>
      <RT.Trigger asChild>{trigger}</RT.Trigger>
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
