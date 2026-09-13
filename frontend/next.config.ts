import type { NextConfig } from "next";
import { getAllowedDevOrigins, getRootEnvValue } from "./lib/dev-origins";

const jarvisWakeModelUrl = getRootEnvValue("NEXT_PUBLIC_JARVIS_WAKE_MODEL_URL");
const apiProxyUrl = process.env.AI_COCKPIT_API_PROXY_URL?.trim().replace(/\/$/, "")
  || "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  distDir: process.env.AI_COCKPIT_NEXT_DIST_DIR || ".next",
  typescript: {
    tsconfigPath: process.env.AI_COCKPIT_TSCONFIG_PATH || "tsconfig.json",
  },
  allowedDevOrigins: getAllowedDevOrigins(),
  devIndicators: false,
  env: jarvisWakeModelUrl
    ? { NEXT_PUBLIC_JARVIS_WAKE_MODEL_URL: jarvisWakeModelUrl }
    : undefined,
  async redirects() {
    return [
      {
        source: "/knowledge",
        destination: "/wiki",
        permanent: true,
      },
    ];
  },
  // Proxy /api/* to the FastAPI backend in development
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiProxyUrl}/api/:path*`,
      },
    ];
  },
  // For production static export served by FastAPI, use:
  // output: "export",
};

export default nextConfig;
