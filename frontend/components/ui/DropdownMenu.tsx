"use client";

// shadcn/ui `dropdown-menu`, reworked onto the kit (ADR-075). Radix gives real menu
// semantics: arrow keys, typeahead, Esc, focus return. Only the parts the app uses are
// kept (no checkbox/radio/sub-menus -- add them when a menu needs them).

import * as React from "react";
import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import { cn } from "@/lib/cn";

const DropdownMenu = DropdownMenuPrimitive.Root;
const DropdownMenuTrigger = DropdownMenuPrimitive.Trigger;

function DropdownMenuContent({
  className,
  align = "end",
  sideOffset = 6,
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Content>) {
  return (
    <DropdownMenuPrimitive.Portal>
      <DropdownMenuPrimitive.Content
        data-slot="dropdown-menu-content"
        align={align}
        sideOffset={sideOffset}
        collisionPadding={12}
        className={cn(
          "z-50 min-w-56 rounded-none border border-line bg-canvas py-1 text-ink shadow-pop outline-none animate-pop-in",
          className
        )}
        {...props}
      />
    </DropdownMenuPrimitive.Portal>
  );
}

function DropdownMenuItem({
  className,
  variant = "default",
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Item> & { variant?: "default" | "danger" }) {
  return (
    <DropdownMenuPrimitive.Item
      data-slot="dropdown-menu-item"
      className={cn(
        "flex cursor-pointer select-none items-center gap-2 rounded-none px-3 py-2 text-sm outline-none",
        "data-[highlighted]:bg-surface-2 data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
        variant === "danger" ? "text-danger-fg" : "text-ink",
        className
      )}
      {...props}
    />
  );
}

function DropdownMenuLabel({
  className,
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Label>) {
  return (
    <DropdownMenuPrimitive.Label
      data-slot="dropdown-menu-label"
      className={cn("px-3 py-2 text-xs text-ink-dim", className)}
      {...props}
    />
  );
}

function DropdownMenuSeparator({
  className,
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Separator>) {
  return (
    <DropdownMenuPrimitive.Separator
      data-slot="dropdown-menu-separator"
      className={cn("my-1 h-px bg-line", className)}
      {...props}
    />
  );
}

export {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuLabel, DropdownMenuSeparator,
};
