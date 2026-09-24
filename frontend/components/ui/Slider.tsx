"use client";

import * as RSlider from "@radix-ui/react-slider";
import { cn } from "@/lib/cn";

// Replaces <input type="range" className="accent-signal-blue">, which renders as OS
// chrome and has no keyboard affordance beyond the browser default. Radix gives
// arrow/page/home/end handling and a real focus ring.

export default function Slider({
  label,
  value,
  min,
  max,
  step = 1,
  display,
  onChange,
  className,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  /** Formatted current value, shown against the label. */
  display: string;
  onChange: (v: number) => void;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <span className="text-2xs font-bold uppercase tracking-[0.14em] text-ink-faint">
          {label}
        </span>
        <span className="text-numeric text-xs font-bold text-info-fg">{display}</span>
      </div>
      <RSlider.Root
        aria-label={label}
        value={[value]}
        min={min}
        max={max}
        step={step}
        onValueChange={([v]) => onChange(v)}
        className="relative flex h-4 w-full touch-none select-none items-center"
      >
        <RSlider.Track className="relative h-1 w-full grow overflow-hidden rounded-full bg-surface-3">
          <RSlider.Range className="absolute h-full bg-info" />
        </RSlider.Track>
        <RSlider.Thumb
          className={cn(
            "block size-3.5 rounded-full border-2 border-info bg-canvas",
            "transition-[transform,box-shadow] duration-150 ease-soft",
            "hover:scale-110 active:scale-95",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-info-fg"
          )}
        />
      </RSlider.Root>
      <div className="mt-1 flex justify-between text-2xs text-ink-faint">
        <span className="text-numeric">{min}</span>
        <span className="text-numeric">{max}</span>
      </div>
    </div>
  );
}
