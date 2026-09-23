// Writes public/config.json from NEXT_PUBLIC_API_BASE at build time, so the committed
// file (ADR-028) can stay the localhost default for local dev while a hosted build (e.g.
// Vercel, with NEXT_PUBLIC_API_BASE set in its project env vars) gets the real backend
// URL baked into the exported static file. A no-op when the env var is unset, so
// `npm run build` without it behaves exactly as before.
const fs = require("fs");
const path = require("path");

const apiBase = process.env.NEXT_PUBLIC_API_BASE;
if (apiBase) {
  const target = path.join(__dirname, "..", "public", "config.json");
  fs.writeFileSync(target, JSON.stringify({ apiBase }) + "\n");
  console.log(`write-runtime-config: public/config.json -> apiBase=${apiBase}`);
}
