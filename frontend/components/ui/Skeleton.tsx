import { cn } from "@/lib/cn";

// Panels used to render an empty box, or the bare word "Loading…", until their first
// fetch resolved. A skeleton holds the shape so the layout does not jump when data
// lands -- which on a page of stacked panels was a visible reflow cascade.

export default function Skeleton({
  className,
  style,
}: {
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <div
      aria-hidden
      style={style}
      className={cn("animate-pulse rounded-md bg-surface-2", className)}
    />
  );
}

/** Placeholder for a figure row: N label+value pairs. */
export function SkeletonStats({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="space-y-1.5">
          <Skeleton className="h-2.5 w-20" />
          <Skeleton className="h-5 w-24" />
          <Skeleton className="h-2 w-28" />
        </div>
      ))}
    </div>
  );
}

/** Placeholder for a table: N rows of C columns. */
export function SkeletonTable({ rows = 6, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="flex gap-3">
          {Array.from({ length: cols }, (_, c) => (
            <Skeleton
              key={c}
              className={cn("h-3", c === 0 ? "w-32 shrink-0" : "flex-1")}
              // Staggered opacity reads as depth rather than as one flat block.
              style={{ animationDelay: `${(r * cols + c) * 18}ms` } as React.CSSProperties}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

// Fixed heights rather than a computed Math.sin() curve: a full-precision float
// serialises differently on the server ("75.9463%") than on the client
// ("75.94632915310568%"), which React reports as a hydration mismatch and then
// re-renders the whole subtree on the client to repair. Integers cannot mismatch.
const BAR_HEIGHTS = [
  42, 68, 90, 55, 34, 76, 96, 61, 38, 83, 71, 47, 88, 59, 30, 74, 93, 52, 66, 80, 45, 62,
];

/** Placeholder sized to a chart body. */
export function SkeletonChart({ height = 230 }: { height?: number }) {
  return (
    <div className="flex items-end gap-1.5" style={{ height }} aria-hidden>
      {BAR_HEIGHTS.map((h, i) => (
        <Skeleton
          key={i}
          className="flex-1 rounded-sm"
          style={{ height: `${h}%`, animationDelay: `${i * 30}ms` }}
        />
      ))}
    </div>
  );
}
