import type { NextConfig } from "next";
import { getAllowedDevOrigins, getRootEnvValue } from "./lib/dev-origins";

const jarvisWakeModelUrl = getRootEnvValue("NEXT_PUBLIC_JARVIS_WAKE_MODEL_URL");

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
  // Proxy /api/* to the FastAPI backend in development
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
    ];
  },
  // For production static export served by FastAPI, use:
  // output: "export",
};

export default nextConfig;
