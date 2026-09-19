import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

// One table treatment for the four dense tables (containment, league table,
// instrumentation plan, audit log), which previously each hand-rolled their own
// <thead> styling and row borders.
//
// Wrapped in an overflow-x container: a 8-column table cannot shrink to 400px, and
// horizontal scroll on the table alone beats horizontal scroll on the page body.

export interface Column<Row> {
  key: string;
  header: ReactNode;
  /** Right-align figures, left-align text. */
  align?: "left" | "right";
  width?: string;
  cell: (row: Row, index: number) => ReactNode;
}

export default function DataTable<Row>({
  columns,
  rows,
  rowKey,
  empty = "No rows.",
  footer,
  className,
  maxHeight,
}: {
  columns: Column<Row>[];
  rows: Row[];
  rowKey: (row: Row, index: number) => string;
  empty?: ReactNode;
  footer?: ReactNode;
  className?: string;
  /** Enables a sticky header and scrolls the body beneath it. */
  maxHeight?: number;
}) {
  if (rows.length === 0) {
    return (
      <div className="py-8 text-center text-xs text-ink-faint">{empty}</div>
    );
  }

  return (
    <div
      className={cn("-mx-1 overflow-x-auto px-1", maxHeight && "overflow-y-auto", className)}
      style={maxHeight ? { maxHeight } : undefined}
    >
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="border-b border-line">
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                style={c.width ? { width: c.width } : undefined}
                className={cn(
                  "whitespace-nowrap pb-2 pr-3 font-medium uppercase tracking-[0.12em]",
                  "text-2xs text-ink-faint",
                  maxHeight && "sticky top-0 z-10 bg-surface",
                  c.align === "right" ? "text-right" : "text-left"
                )}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={rowKey(row, i)}
              className="border-b border-line/60 transition-colors duration-100 last:border-0 hover:bg-surface-2"
            >
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={cn(
                    "py-1.5 pr-3 align-top",
                    c.align === "right" && "text-right"
                  )}
                >
                  {c.cell(row, i)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {footer && (
          <tfoot>
            <tr className="border-t border-line-strong font-semibold">{footer}</tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}
