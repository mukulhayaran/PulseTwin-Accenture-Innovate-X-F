import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

// Both faces are SELF-HOSTED by next/font at build time -- no runtime request to
// Google, which matters because the static export ships to a CDN and the demo has
// been run offline. tailwind.config.ts used to name JetBrains Mono while nothing
// fetched it, so every machine silently rendered a different fallback.
const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains",
  display: "swap",
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "PulseTwin — Assembly Line Digital Twin",
  description: "Predictive quality and constraint intelligence for mixed-model assembly",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrains.variable}`}>
      <body className="bg-canvas text-ink antialiased">{children}</body>
    </html>
  );
}
