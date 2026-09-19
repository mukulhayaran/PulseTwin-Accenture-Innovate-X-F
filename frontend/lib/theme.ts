// The single source of truth for colour OUTSIDE CSS.
//
// Recharts takes hex strings (`stroke="#6b7280"`), not class names, so chart colour
// could not come from Tailwind. The result was 30 hardcoded hex literals scattered
// across three chart sites, each drifting independently. Everything here mirrors the
// @theme block in app/globals.css -- change one, change both.

export const theme = {
  canvas: "#0a0c11",
  surface: "#111419",
  surface2: "#171b22",
  surface3: "#1e232c",
  line: "#232834",
  lineStrong: "#2f3644",

  ink: "#e6e9ef",
  inkDim: "#a0a8b4",
  inkFaint: "#6b7482",

  ok: "#10b981",
  okFg: "#34d399",
  danger: "#ef4444",
  dangerFg: "#f87171",
  warn: "#f59e0b",
  warnFg: "#fbbf24",
  info: "#3b82f6",
  infoFg: "#60a5fa",
  ai: "#8b5cf6",
  aiFg: "#a78bfa",
} as const;

// Categorical series colour, in the order a multi-series chart should consume it.
// Ordered for distinguishability at small sizes, not by hue wheel.
export const series = [
  theme.info,
  theme.ai,
  theme.ok,
  theme.warn,
  theme.danger,
  theme.inkDim,
] as const;

// Shared Recharts axis/grid props. Previously each chart re-declared its own AXIS
// const, and IngestionLagChart had none at all.
export const axisProps = {
  stroke: theme.inkFaint,
  tick: { fill: theme.inkFaint, fontSize: 10, fontFamily: "var(--font-mono)" },
  tickLine: false,
  axisLine: { stroke: theme.line },
} as const;

export const gridProps = {
  stroke: theme.line,
  strokeDasharray: "2 4",
  vertical: false,
} as const;
