"use client";

// shadcn/ui `tabs`, reworked onto the kit (ADR-075). Same look as SegmentedControl --
// square, hairline, the selected tab inverts to ink -- but with real tablist / tab /
// tabpanel semantics and arrow-key navigation, for places that switch PANELS rather
// than filter a value. No sliding indicator: a layout animation here would re-measure
// on every SSE flush (CLAUDE.md §8).

import * as React from "react";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cn } from "@/lib/cn";

const Tabs = TabsPrimitive.Root;

function TabsList({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      className={cn("inline-flex shrink-0 items-center border border-line-strong bg-canvas", className)}
      {...props}
    />
  );
}

function TabsTrigger({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      data-slot="tabs-trigger"
      className={cn(
        "rounded-none px-3 py-1.5 text-2xs font-bold uppercase tracking-[0.08em] text-ink-faint",
        "transition-colors duration-150 hover:text-ink",
        "data-[state=active]:bg-ink data-[state=active]:text-canvas",
        className
      )}
      {...props}
    />
  );
}

function TabsContent({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content data-slot="tabs-content" className={cn("outline-none", className)} {...props} />;
}

export { Tabs, TabsList, TabsTrigger, TabsContent };
