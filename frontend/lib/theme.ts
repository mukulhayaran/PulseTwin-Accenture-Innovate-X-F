// The single source of truth for colour OUTSIDE CSS.
//
// Recharts takes hex strings (`stroke="#6b7280"`), not class names, so chart colour
// could not come from Tailwind. The result was 30 hardcoded hex literals scattered
// across three chart sites, each drifting independently. Everything here mirrors the
// @theme block in app/globals.css -- change one, change both.
//
// Light (BMW corporate) values since ADR-047. The `-fg` pair inverted meaning with the
// palette: on the dark build it was the BRIGHTENED variant for text on black, here it
// is the DARKENED variant for text on white. Charts should take the solid value for a
// mark and the `-fg` value for any label drawn over the plate.

export const theme = {
  canvas: "#ffffff",
  surface: "#ffffff",
  surface2: "#fafafa",
  surface3: "#f0f0f0",
  line: "#e6e6e6",
  lineStrong: "#cccccc",

  // The accent band (rail, landing bands, tooltips, chart tips). Light since
  // ADR-074; names kept so no call site changed. Mirrors globals.css.
  surfaceDark: "#f3f6fb",
  surfaceDark2: "#e9eef6",
  onDark: "#262626",
  onDarkSoft: "#4a4a4a",
  lineOnDark: "#dbe3ee",

  ink: "#262626",
  inkDim: "#4a4a4a",
  // Pinned just over WCAG AA on white (4.6:1) -- it carries every axis label and
  // table header. BMW's own #9a9a9a muted-soft measures 2.8:1 and is disabled-only.
  inkFaint: "#757575",
  inkDisabled: "#9a9a9a",

  // BMW blue: the single primary action colour. `info` is deliberately the same
  // hex -- the corporate system does not have two blues.
  primary: "#1c69d4",
  primaryActive: "#0653b6",
  onPrimary: "#ffffff",

  ok: "#16a34a",
  okFg: "#166534",
  danger: "#dc2626",
  dangerFg: "#b91c1c",
  warn: "#d97706",
  warnFg: "#92400e",
  info: "#1c69d4",
  infoFg: "#0653b6",
  ai: "#7c3aed",
  aiFg: "#6d28d9",

  // Signal text inside the accent band -- equal to the `-fg` values since ADR-074.
  okOnDark: "#166534",
  dangerOnDark: "#b91c1c",
  warnOnDark: "#92400e",
  infoOnDark: "#0653b6",
  aiOnDark: "#6d28d9",

  // Divider / badge role only. Never a CTA fill.
  mBlueLight: "#0066b1",
  mBlueDark: "#1c69d4",
  mRed: "#e22718",
} as const;

// Shared Recharts axis/grid props. Previously each chart re-declared its own AXIS
// const, and IngestionLagChart had none at all.
export const axisProps = {
  stroke: theme.inkFaint,
  tick: { fill: theme.inkFaint, fontSize: 10, fontFamily: "var(--font-mono)" },
  tickLine: false,
  axisLine: { stroke: theme.lineStrong },
} as const;

export const gridProps = {
  stroke: theme.line,
  strokeDasharray: "2 4",
  vertical: false,
} as const;
