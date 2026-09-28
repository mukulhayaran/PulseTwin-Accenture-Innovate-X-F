"use client";

// shadcn/ui `popover`, reworked onto the kit (ADR-075). Radix handles collision with
// the viewport edge, outside-click, Esc and focus return. `shadow-pop` is the system's
// one elevation, reserved for things that float free of the page.

import * as React from "react";
import * as PopoverPrimitive from "@radix-ui/react-popover";
import { cn } from "@/lib/cn";

const Popover = PopoverPrimitive.Root;
const PopoverTrigger = PopoverPrimitive.Trigger;

function PopoverContent({
  className,
  align = "end",
  sideOffset = 6,
  ...props
}: React.ComponentProps<typeof PopoverPrimitive.Content>) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        data-slot="popover-content"
        align={align}
        sideOffset={sideOffset}
        collisionPadding={12}
        className={cn(
          "z-50 w-72 rounded-none border border-line bg-canvas text-ink shadow-pop outline-none animate-pop-in",
          className
        )}
        {...props}
      />
    </PopoverPrimitive.Portal>
  );
}

export { Popover, PopoverTrigger, PopoverContent };
