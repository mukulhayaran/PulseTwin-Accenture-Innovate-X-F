"use client";

// ADR-067 business impact. Every line carries its basis -- ACTUAL (observed on the running
// twin, a simulated plant), SIMULATED, ESTIMATED, PROJECTED or NOT_MEASURED -- and its
// source. No figure without one (CLAUDE.md §3, sourced economics).

import { useEffect, useState } from "react";
import { getBusinessImpact } from "@/lib/api";
import { Badge, Panel } from "@/components/ui";

const BASIS: Record<string, any> = {
  ACTUAL: "ok", SIMULATED: "ai", ESTIMATED: "info", PROJECTED: "warn", NOT_MEASURED: "neutral",
};

export default function BusinessImpact({ plant }: { plant: string }) {
  const [lines, setLines] = useState<any[] | null>(null);
  useEffect(() => {
    getBusinessImpact(plant).then((r) => setLines(r.lines)).catch(() => setLines(null));
  }, [plant]);
  return (
    <Panel title="AI operations: business impact" note="Each figure labelled by how it was obtained">
      {!lines ? (
        <p className="text-xs text-ink-faint">Unavailable for this role or plant.</p>
      ) : (
        <table className="w-full text-left text-xs">
          <tbody>
            {lines.map((l) => (
              <tr key={l.metric} className="border-b border-line last:border-0">
                <td className="py-2 pr-3 text-ink-dim">{l.metric.replace(/_/g, " ")}</td>
                <td className="text-numeric py-2 pr-3 text-right font-bold text-ink">
                  {l.value == null ? "—" : typeof l.value === "number" ? l.value.toLocaleString() : String(l.value)}
                </td>
                <td className="py-2 pr-3"><Badge variant={BASIS[l.basis] ?? "neutral"}>{l.basis}</Badge></td>
                <td className="py-2 text-2xs text-ink-faint">{l.source ?? l.note ?? ""} {l.n ? `(n=${l.n})` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
