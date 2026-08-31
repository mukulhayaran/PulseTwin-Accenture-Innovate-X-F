import type { NextConfig } from "next";

// Static export: zero server RAM, deployable to any CDN.
// The API base is NOT baked in here -- it is fetched at runtime from /config.json
// (ADR-028), so pointing the UI at a different backend needs no rebuild.
const config: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  eslint: { ignoreDuringBuilds: true },
};
export default config;
