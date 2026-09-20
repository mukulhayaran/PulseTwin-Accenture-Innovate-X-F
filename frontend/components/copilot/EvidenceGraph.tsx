"use client";

// The evidence chain: station -> signal -> window -> fingerprint -> risk -> units ->
// inspection.
//
// ENCODING DECISIONS, and why they are what they are:
//
//  * The chain is inherently ORDERED, so POSITION carries node kind -- one column per
//    stage, left to right. Giving each of the seven kinds its own hue would be a
//    seven-colour categorical palette doing a job that layout already does, and a
//    generated 7th hue is indistinguishable from an existing one under CVD.
//  * COLOUR therefore carries exactly one thing: provenance. Two values, using the
//    project's established status tokens (ok = measured, warn = inferred, CLAUDE.md §7).
//    Measured against the dark surface that pair separates at ΔE 10.6 protan / 21.2
//    normal, both above the floor.
//  * Colour is never alone. Inferred edges are also DASHED, inferred nodes carry the
//    `hatch-inferred` texture, and both are labelled in text -- so the distinction
//    survives full CVD, greyscale print and forced-colors.
//
// The graph is built in the BACKEND (serve/routers/intelligence.py), deterministically,
// so the copilot narrates a chain it cannot fabricate. It is an ASSOCIATION graph and
// says so on its face: nothing in this repository establishes causation.

import { useMemo, useState } from "react";
import { EvidenceEdge, EvidenceGraph as Graph, EvidenceNode } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Badge, Tooltip } from "@/components/ui";

// The chain order. A node whose kind is not listed is appended at the end rather than
// dropped -- a new node kind should look out of place, not disappear.
const STAGES: { kind: EvidenceNode["kind"]; label: string }[] = [
  { kind: "station", label: "Station" },
  { kind: "signal", label: "Signal" },
  { kind: "line_factor", label: "Line factor" },
  { kind: "window", label: "Window" },
  { kind: "fingerprint", label: "Fingerprint" },
  { kind: "risk", label: "Risk" },
  { kind: "unit_group", label: "Affected units" },
  { kind: "inspection", label: "Inspection" },
];

const COL_W = 148;
const NODE_H = 42;
const NODE_W = 124;
const GAP_Y = 12;
const PAD_Y = 26;

export default function EvidenceGraph({ graph }: { graph: Graph }) {
  const [hovered, setHovered] = useState<EvidenceEdge | null>(null);

  const layout = useMemo(() => {
    const used = STAGES.filter((s) => graph.nodes.some((n) => n.kind === s.kind));
    const byStage = used.map((s) => ({
      ...s,
      nodes: graph.nodes.filter((n) => n.kind === s.kind),
    }));
    const rows = Math.max(1, ...byStage.map((s) => s.nodes.length));
    const height = PAD_Y * 2 + rows * NODE_H + (rows - 1) * GAP_Y;

    const pos = new Map<string, { x: number; y: number }>();
    byStage.forEach((stage, col) => {
      const blockH = stage.nodes.length * NODE_H + (stage.nodes.length - 1) * GAP_Y;
      const top = (height - blockH) / 2;
      stage.nodes.forEach((n, i) => {
        pos.set(n.id, {
          x: col * COL_W,
          y: top + i * (NODE_H + GAP_Y),
        });
      });
    });

    return {
      stages: byStage,
      pos,
      width: Math.max(1, byStage.length) * COL_W - (COL_W - NODE_W),
      height,
    };
  }, [graph]);

  const measuredEdges = graph.edges.filter((e) => e.provenance === "measured").length;
  const inferredEdges = graph.edges.length - measuredEdges;

  return (
    <div>
      {/* legend: identity is never colour alone */}
      <div className="mb-2 flex flex-wrap items-center gap-3">
        <span className="flex items-center gap-1.5 text-2xs text-ink-dim">
          <svg width="20" height="6" aria-hidden>
            <line x1="0" y1="3" x2="20" y2="3" stroke="var(--color-ok-fg)" strokeWidth="2" />
          </svg>
          measured ({measuredEdges})
        </span>
        <span className="flex items-center gap-1.5 text-2xs text-ink-dim">
          <svg width="20" height="6" aria-hidden>
            <line
              x1="0" y1="3" x2="20" y2="3"
              stroke="var(--color-warn-fg)" strokeWidth="2" strokeDasharray="3 2"
            />
          </svg>
          inferred ({inferredEdges})
        </span>
        <Badge variant="warn">association, not cause</Badge>
      </div>

      <div className="overflow-x-auto">
        <svg
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          width={layout.width}
          height={layout.height}
          role="img"
          aria-label={`Evidence chain for ${graph.unit_id}: ${graph.nodes.length} nodes, ${graph.edges.length} associations`}
          className="max-w-none"
        >
          {/* edges first, so nodes sit on top of them */}
          <g>
            {graph.edges.map((e, i) => {
              const a = layout.pos.get(e.from);
              const b = layout.pos.get(e.to);
              if (!a || !b) return null;
              const x1 = a.x + NODE_W;
              const y1 = a.y + NODE_H / 2;
              const x2 = b.x;
              const y2 = b.y + NODE_H / 2;
              const mid = (x1 + x2) / 2;
              const inferred = e.provenance === "inferred";
              const active =
                hovered && hovered.from === e.from && hovered.to === e.to;
              return (
                <path
                  key={`${e.from}->${e.to}-${i}`}
                  d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
                  fill="none"
                  stroke={
                    inferred ? "var(--color-warn-fg)" : "var(--color-ok-fg)"
                  }
                  strokeWidth={active ? 2.5 : 1.5}
                  strokeOpacity={active ? 1 : 0.5}
                  strokeDasharray={inferred ? "4 3" : undefined}
                  onMouseEnter={() => setHovered(e)}
                  onMouseLeave={() => setHovered(null)}
                  className="cursor-help"
                />
              );
            })}
          </g>

          {/* nodes */}
          <g>
            {layout.stages.map((stage) =>
              stage.nodes.map((n) => {
                const p = layout.pos.get(n.id)!;
                const inferred = n.provenance === "inferred";
                return (
                  <g key={n.id} transform={`translate(${p.x},${p.y})`}>
                    <rect
                      width={NODE_W}
                      height={NODE_H}
                      rx="6"
                      fill="var(--color-surface-2)"
                      stroke={
                        inferred ? "var(--color-warn)" : "var(--color-line-strong)"
                      }
                      strokeWidth="1"
                    />
                    <text
                      x="8"
                      y="16"
                      className="fill-ink"
                      style={{ fontSize: 10, fontWeight: 500 }}
                    >
                      {truncate(String(n.label), 17)}
                    </text>
                    <text
                      x="8"
                      y="30"
                      className="fill-ink-faint"
                      style={{ fontSize: 9 }}
                    >
                      {nodeDetail(n)}
                    </text>
                  </g>
                );
              })
            )}
          </g>

          {/* stage headings */}
          <g>
            {layout.stages.map((stage, col) => (
              <text
                key={stage.kind}
                x={col * COL_W}
                y={12}
                className="fill-ink-faint"
                style={{ fontSize: 9, letterSpacing: "0.1em", textTransform: "uppercase" }}
              >
                {stage.label}
              </text>
            ))}
          </g>
        </svg>
      </div>

      {/* the hovered edge's evidence, in full */}
      <div className="mt-2 min-h-[2.75rem] rounded-md border border-line bg-surface-2/50 px-2.5 py-1.5">
        {hovered ? (
          <div className="space-y-0.5">
            <p className="text-2xs text-ink">
              <span className="text-numeric text-ink-dim">{hovered.from}</span>{" "}
              {hovered.relation.replace(/_/g, " ")}{" "}
              <span className="text-numeric text-ink-dim">{hovered.to}</span>
            </p>
            <p className="text-2xs text-ink-faint">
              source: {hovered.evidence_source}
              {hovered.window && ` · window: ${hovered.window}`}
              {hovered.confidence != null &&
                ` · confidence: ${hovered.confidence.toFixed(3)}`}
              {hovered.model && ` · model: ${hovered.model}`}
              {` · ${hovered.provenance}`}
            </p>
          </div>
        ) : (
          <p className="text-2xs text-ink-faint">
            Hover an edge to see its evidence source, window, provenance and model.
          </p>
        )}
      </div>

      <p className="mt-2 text-2xs leading-relaxed text-ink-faint">
        {graph.causality_statement}
      </p>
    </div>
  );
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function nodeDetail(n: EvidenceNode): string {
  const value = n.value as number | undefined;
  if (typeof value === "number") {
    const lo = n.ci_low as number | null | undefined;
    const hi = n.ci_high as number | null | undefined;
    const base = Number.isInteger(value) ? String(value) : value.toFixed(2);
    return lo != null && hi != null
      ? `${base} [${lo.toFixed(2)}–${hi.toFixed(2)}]`
      : base;
  }
  if (typeof n.count === "number") return `${n.count} units`;
  if (Array.isArray(n.shared_factors)) return (n.shared_factors as string[]).join(", ");
  if (typeof n.lead_time_min === "number")
    return `${(n.lead_time_min as number).toFixed(1)} min lead`;
  return n.provenance ? String(n.provenance) : "";
}

export function EvidenceGraphPanelNote({ graph }: { graph: Graph }) {
  return (
    <Tooltip content={graph.method}>
      <span className={cn("cursor-help text-2xs text-ink-faint")}>
        {graph.nodes.length} nodes · {graph.edges.length} associations
      </span>
    </Tooltip>
  );
}
