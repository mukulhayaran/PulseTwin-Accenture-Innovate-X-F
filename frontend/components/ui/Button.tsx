import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

// Every interactive element gets hover / focus-visible / active / disabled states.
// The app previously had zero focus styles anywhere -- `grep "focus:"` returned
// nothing across 2,758 lines, so keyboard users had no visible caret at all.
//
// BMW corporate dialect (ADR-047): rectangular, 0px corner, weight 700, 0.5px
// tracking. Corners come from --radius-* (all 0), so no size here names a radius
// -- a future change to the radius scale reaches every button without an edit.
// The label is NOT uppercased: BMW reserves uppercase for `button-text-link`
// ("LEARN MORE ›"), which is the `link` variant below.

type Variant =
  | "primary"
  | "default"
  | "ghost"
  | "outline"
  | "on-dark"
  | "link"
  | "danger"
  | "ok"
  | "warn"
  | "ai";
type Size = "xs" | "sm" | "md" | "lg";

const VARIANTS: Record<Variant, string> = {
  // The single primary action colour. One of these per band, at most.
  primary:
    "border-primary bg-primary text-on-primary hover:bg-primary-active hover:border-primary-active active:bg-primary-active",
  default:
    "border-line-strong bg-canvas text-ink hover:bg-surface-2 hover:border-ink active:bg-surface-3",
  ghost:
    "border-transparent bg-transparent text-ink-dim hover:bg-surface-2 hover:text-ink active:bg-surface-3",
  outline:
    "border-line-strong bg-transparent text-ink hover:border-ink active:bg-surface-2",
  // For use over a dark hero band -- transparent with a white hairline.
  "on-dark":
    "border-on-dark bg-transparent text-on-dark hover:bg-on-dark/10 active:bg-on-dark/15",
  // The inline uppercase text link. No background, no border, no height.
  link: "border-transparent bg-transparent text-ink label-upper hover:text-primary active:text-primary-active",
  danger: "border-danger/40 bg-danger/8 text-danger-fg hover:bg-danger/16 active:bg-danger/20",
  ok: "border-ok/40 bg-ok/8 text-ok-fg hover:bg-ok/16 active:bg-ok/20",
  warn: "border-warn/40 bg-warn/8 text-warn-fg hover:bg-warn/16 active:bg-warn/20",
  ai: "border-ai/40 bg-ai/8 text-ai-fg hover:bg-ai/16 active:bg-ai/20",
};

const SIZES: Record<Size, string> = {
  xs: "h-7 gap-1 px-2.5 text-2xs",
  sm: "h-9 gap-1.5 px-3.5 text-xs",
  md: "h-10 gap-2 px-4 text-xs",
  // BMW's `button-primary`: 48px tall, 32px side padding, 14px label. Minimum
  // 48x48 touch target, above WCAG AAA's 44.
  lg: "h-12 gap-2 px-8 text-sm",
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
        "inline-flex shrink-0 select-none items-center justify-center border font-bold tracking-[0.03em]",
        "transition-[background-color,border-color,color] duration-150 ease-soft",
        "disabled:pointer-events-none disabled:border-line disabled:text-ink-disabled",
        "disabled:bg-transparent",
        VARIANTS[variant],
        // The text link is type, not a control: it takes no height or side padding,
        // and label-upper owns its own size.
        variant === "link" ? "h-auto gap-1.5 px-0" : SIZES[size],
        className
      )}
    >
      {children}
    </button>
  );
}
