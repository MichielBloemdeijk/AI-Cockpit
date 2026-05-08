import type { NextConfig } from "next";
import { getAllowedDevOrigins } from "./lib/dev-origins";

const nextConfig: NextConfig = {
  allowedDevOrigins: getAllowedDevOrigins(),
  devIndicators: false,
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
