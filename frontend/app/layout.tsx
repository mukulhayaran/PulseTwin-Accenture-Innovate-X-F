import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PulseTwin — Assembly Line Digital Twin",
  description: "Predictive quality and constraint intelligence for mixed-model assembly",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      {/* text-ink-dim, not text-ink: body copy runs at BMW's Light 300 in the softer
          body ink, and display type opts up to ink + 700 per element. */}
      <body className="bg-canvas text-ink-dim antialiased">{children}</body>
    </html>
  );
}
