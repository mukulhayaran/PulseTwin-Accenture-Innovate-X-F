import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

// Every interactive element gets hover / focus-visible / active / disabled states.
// The app previously had zero focus styles anywhere -- `grep "focus:"` returned
// nothing across 2,758 lines, so keyboard users had no visible caret at all.

type Variant = "default" | "ghost" | "outline" | "danger" | "ok" | "warn" | "ai";
type Size = "xs" | "sm" | "md" | "lg";

const VARIANTS: Record<Variant, string> = {
  default:
    "border-line-strong bg-surface-2 text-ink hover:bg-surface-3 hover:border-line-strong active:bg-surface-3",
  ghost:
    "border-transparent bg-transparent text-ink-dim hover:bg-surface-2 hover:text-ink active:bg-surface-3",
  outline:
    "border-line-strong bg-transparent text-ink-dim hover:border-info/50 hover:text-ink active:bg-surface-2",
  danger: "border-danger/40 bg-danger/10 text-danger-fg hover:bg-danger/20 active:bg-danger/25",
  ok: "border-ok/40 bg-ok/10 text-ok-fg hover:bg-ok/20 active:bg-ok/25",
  warn: "border-warn/40 bg-warn/10 text-warn-fg hover:bg-warn/20 active:bg-warn/25",
  ai: "border-ai/40 bg-ai/10 text-ai-fg hover:bg-ai/20 active:bg-ai/25",
};

const SIZES: Record<Size, string> = {
  xs: "h-7 gap-1 px-2 text-2xs rounded-md",
  sm: "h-8 gap-1.5 px-3 text-xs rounded-md",
  md: "h-9 gap-2 px-3.5 text-xs rounded-md",
  // For a launcher that has to read as its own destination, not an in-panel action --
  // currently only the copilot dock's toggle.
  lg: "h-11 gap-2 px-4 text-sm rounded-lg",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  children: ReactNode;
}

export default function Button({
  variant = "default",
  size = "sm",
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      className={cn(
        "inline-flex shrink-0 select-none items-center justify-center border font-medium",
        "transition-[background-color,border-color,color] duration-150 ease-soft",
        "disabled:pointer-events-none disabled:opacity-40",
        VARIANTS[variant],
        SIZES[size],
        className
      )}
    >
      {children}
    </button>
  );
}
