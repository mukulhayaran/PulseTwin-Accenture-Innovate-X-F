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
            // Dark navy, not a white plate: on a white page a white tooltip has to
            // earn its edge from shadow alone. The dark band is already in the
            // system (BMW `surface-dark`), so this reuses it rather than inventing
            // a floating-surface colour.
            "z-50 max-w-72 border border-surface-dark bg-surface-dark px-3 py-2",
            "text-2xs leading-relaxed text-on-dark-soft shadow-pop",
            className
          )}
        >
          {content}
          <RT.Arrow className="fill-surface-dark" width={10} height={5} />
        </RT.Content>
      </RT.Portal>
    </RT.Root>
  );
}
