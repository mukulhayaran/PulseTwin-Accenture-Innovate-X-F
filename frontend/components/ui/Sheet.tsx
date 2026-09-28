"use client";

// shadcn/ui `sheet`, reworked onto the kit (ADR-075): Radix Dialog gives the focus
// trap, focus return, Esc, scroll lock and aria-modal that the hand-rolled drawers
// lacked. 0px corners, hairline edge, no shadow -- it is anchored to the viewport
// edge, not floating. Enter-only motion; reduced motion collapses it (globals.css).

import * as React from "react";
import * as SheetPrimitive from "@radix-ui/react-dialog";
import { cn } from "@/lib/cn";
import { IconClose } from "./icons";

const Sheet = SheetPrimitive.Root;
const SheetTrigger = SheetPrimitive.Trigger;
const SheetClose = SheetPrimitive.Close;

function SheetContent({
  className,
  children,
  side = "right",
  showCloseButton = true,
  onOpenAutoFocus,
  onCloseAutoFocus,
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Content> & {
  side?: "right" | "left";
  showCloseButton?: boolean;
}) {
  // Both sheets in the app open through state (a "Why?" button, the menu button), not a
  // Radix <SheetTrigger>, and Radix then has no trigger to hand focus back to -- it fell
  // to <body> (measured). Record whatever had focus at the moment the sheet opens --
  // Radix fires onOpenAutoFocus just BEFORE it moves focus inside -- and hand it back.
  const opener = React.useRef<HTMLElement | null>(null);
  return (
    <SheetPrimitive.Portal>
      <SheetPrimitive.Overlay
        data-slot="sheet-overlay"
        className="fixed inset-0 z-50 bg-ink/30 animate-overlay-in"
      />
      <SheetPrimitive.Content
        data-slot="sheet-content"
        className={cn(
          "fixed inset-y-0 z-50 flex h-full w-full flex-col overflow-y-auto bg-canvas outline-none",
          side === "right"
            ? "right-0 border-l border-line animate-sheet-in-right"
            : "left-0 border-r border-line animate-sheet-in-left",
          className
        )}
        {...props}
        onOpenAutoFocus={(e) => {
          opener.current = document.activeElement as HTMLElement | null;
          onOpenAutoFocus?.(e);
        }}
        onCloseAutoFocus={(e) => {
          onCloseAutoFocus?.(e);
          if (e.defaultPrevented || !opener.current?.isConnected) return;
          e.preventDefault();
          opener.current.focus();
        }}
      >
        {children}
        {showCloseButton && (
          <SheetPrimitive.Close
            className="absolute right-3 top-3 grid size-8 place-items-center rounded-none text-ink-dim transition-colors hover:bg-surface-2 hover:text-ink"
          >
            <IconClose size={16} />
            <span className="sr-only">Close</span>
          </SheetPrimitive.Close>
        )}
      </SheetPrimitive.Content>
    </SheetPrimitive.Portal>
  );
}

function SheetHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="sheet-header" className={cn("flex flex-col gap-1 pr-10", className)} {...props} />;
}

function SheetTitle({ className, ...props }: React.ComponentProps<typeof SheetPrimitive.Title>) {
  return (
    <SheetPrimitive.Title
      data-slot="sheet-title"
      className={cn("text-base font-bold text-ink", className)}
      {...props}
    />
  );
}

function SheetDescription({
  className,
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Description>) {
  return (
    <SheetPrimitive.Description
      data-slot="sheet-description"
      className={cn("text-xs text-ink-dim", className)}
      {...props}
    />
  );
}

export { Sheet, SheetTrigger, SheetClose, SheetContent, SheetHeader, SheetTitle, SheetDescription };
