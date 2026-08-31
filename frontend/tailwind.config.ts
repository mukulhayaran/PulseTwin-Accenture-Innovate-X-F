import type { Config } from "tailwindcss";

// Control-room palette. Bloomberg terminal meets factory floor -- dense, dark,
// monospace numerals. Green = measured/healthy, amber = inferred/warning,
// red = alert, purple = model prediction.
export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: { primary: "#0a0e17", card: "#111827", surface: "#1f2937" },
        txt: { primary: "#e5e7eb", muted: "#6b7280" },
        signal: {
          green: "#10b981", red: "#ef4444", amber: "#f59e0b",
          blue: "#3b82f6", purple: "#8b5cf6",
        },
      },
      fontFamily: { mono: ["JetBrains Mono", "ui-monospace", "monospace"] },
    },
  },
  plugins: [],
} satisfies Config;
