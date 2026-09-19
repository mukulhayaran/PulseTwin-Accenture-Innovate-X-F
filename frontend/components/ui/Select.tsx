"use client";

import * as RS from "@radix-ui/react-select";
import { cn } from "@/lib/cn";

// Replaces the bare <select> elements, which rendered as OS chrome -- a light-grey
// native dropdown on a #0a0c11 page, and the single most obviously unstyled thing in
// the app. Radix gives keyboard navigation, typeahead and focus management for free.

export interface SelectOption<T extends string> {
  value: T;
  label: string;
  hint?: string;
}

export default function Select<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
  className,
  size = "sm",
}: {
  value: T;
  options: SelectOption<T>[];
  onChange: (v: T) => void;
  ariaLabel: string;
  className?: string;
  size?: "sm" | "md";
}) {
  return (
    <RS.Root value={value} onValueChange={(v) => onChange(v as T)}>
      <RS.Trigger
        aria-label={ariaLabel}
        className={cn(
          "inline-flex items-center justify-between gap-2 rounded-md border border-line bg-surface-2",
          "font-medium text-ink transition-colors duration-150",
          "hover:border-line-strong data-[state=open]:border-info/50",
          size === "sm" ? "h-7 px-2.5 text-xs" : "h-8 px-3 text-xs",
          className
        )}
      >
        <RS.Value />
        <RS.Icon className="text-ink-faint">
          <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
            <path d="M2 4l3 3 3-3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </RS.Icon>
      </RS.Trigger>

      <RS.Portal>
        <RS.Content
          position="popper"
          sideOffset={6}
          className={cn(
            "z-50 overflow-hidden rounded-lg border border-line-strong bg-surface shadow-pop",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0"
          )}
        >
          <RS.Viewport className="p-1">
            {options.map((o) => (
              <RS.Item
                key={o.value}
                value={o.value}
                className={cn(
                  "relative cursor-pointer select-none rounded-md px-2.5 py-1.5 pr-8 text-xs outline-none",
                  "text-ink-dim data-[highlighted]:bg-surface-3 data-[highlighted]:text-ink",
                  "data-[state=checked]:text-ink"
                )}
              >
                <RS.ItemText>{o.label}</RS.ItemText>
                {o.hint && <span className="ml-2 text-2xs text-ink-faint">{o.hint}</span>}
                <RS.ItemIndicator className="absolute right-2.5 top-1/2 -translate-y-1/2 text-info-fg">
                  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
                    <path d="M1.5 5l2.5 2.5L8.5 2" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </RS.ItemIndicator>
              </RS.Item>
            ))}
          </RS.Viewport>
        </RS.Content>
      </RS.Portal>
    </RS.Root>
  );
}
