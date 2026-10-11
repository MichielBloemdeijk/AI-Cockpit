import type { NextConfig } from "next";
import path from "node:path";

// One public shell using the normal maintained result components.
// Assets cannot collide with a full Cockpit export's /_next bundle.
const config: NextConfig = {
  output: "export",
  assetPrefix: "/_result-assets",
  env: { NEXT_PUBLIC_COCKPIT_STATIC_SHELL: "1" },
  turbopack: { root: path.resolve(process.cwd()) },
};
export default config;
