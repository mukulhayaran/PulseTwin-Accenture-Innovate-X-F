"use client";

// shadcn/ui `dialog`, reworked onto the kit (ADR-075). Used where an action cannot be
// undone and must be confirmed first. 0px corners, hairline, `shadow-pop` because a
// modal genuinely floats free of the page (the only elevation the system allows).

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { cn } from "@/lib/cn";
import { IconClose } from "./icons";

const Dialog = DialogPrimitive.Root;
const DialogTrigger = DialogPrimitive.Trigger;
const DialogClose = DialogPrimitive.Close;

function DialogContent({
  className,
  children,
  showCloseButton = true,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content> & { showCloseButton?: boolean }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay
        data-slot="dialog-overlay"
        className="fixed inset-0 z-50 bg-ink/30 animate-overlay-in"
      />
      <DialogPrimitive.Content
        data-slot="dialog-content"
        className={cn(
          "fixed left-1/2 top-1/2 z-50 grid w-[calc(100%-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 gap-4",
          "rounded-none border border-line bg-canvas p-6 shadow-pop outline-none animate-overlay-in",
          className
        )}
        {...props}
      >
        {children}
        {showCloseButton && (
          <DialogPrimitive.Close
            className="absolute right-3 top-3 grid size-8 place-items-center rounded-none text-ink-dim transition-colors hover:bg-surface-2 hover:text-ink"
          >
            <IconClose size={16} />
            <span className="sr-only">Close</span>
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="dialog-header" className={cn("flex flex-col gap-1.5 pr-8", className)} {...props} />;
}

function DialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-footer"
      className={cn("flex flex-col-reverse gap-2 sm:flex-row sm:justify-end", className)}
      {...props}
    />
  );
}

function DialogTitle({ className, ...props }: React.ComponentProps<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title
      data-slot="dialog-title"
      className={cn("text-base font-bold text-ink", className)}
      {...props}
    />
  );
}

function DialogDescription({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Description>) {
  return (
    <DialogPrimitive.Description
      data-slot="dialog-description"
      className={cn("text-sm leading-relaxed text-ink-dim", className)}
      {...props}
    />
  );
}

export {
  Dialog, DialogTrigger, DialogClose, DialogContent, DialogHeader, DialogFooter,
  DialogTitle, DialogDescription,
};
