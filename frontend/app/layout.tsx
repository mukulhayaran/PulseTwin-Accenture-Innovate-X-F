import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

// next/font downloads both faces AT BUILD TIME and emits them into the static export,
// so the shipped app makes no request to Google and renders identically everywhere --
// tailwind.config.ts used to name JetBrains Mono while nothing ever fetched it, leaving
// each machine on its own fallback.
//
// The trade-off is that `next build` itself now needs network access to fonts.googleapis
// .com. A fully air-gapped image build would have to vendor the .woff2 files into
// public/ and swap these calls for next/font/local; nothing else about the setup changes.
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
