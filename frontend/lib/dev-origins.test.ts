import type { NetworkInterfaceInfo } from "node:os";

import { describe, expect, it } from "vitest";

import {
  getAllowedDevOrigins,
  getConfiguredRemoteHosts,
  getRootEnvValue,
} from "./dev-origins";

function ipv4(address: string): NetworkInterfaceInfo {
  return {
    address,
    netmask: "255.255.255.0",
    family: "IPv4",
    mac: "00:00:00:00:00:00",
    internal: false,
    cidr: `${address}/24`,
  };
}

describe("getConfiguredRemoteHosts", () => {
  it("reads Tailscale hosts from the repo root env text", () => {
    const hosts = getConfiguredRemoteHosts({
      env: { NODE_ENV: "test" },
      rootEnvText: "TAILSCALE_HOST=sarah.tail1234.ts.net\n",
    });

    expect(hosts).toEqual(expect.arrayContaining(["sarah.tail1234.ts.net", "sarah"]));
  });

  it("normalizes comma-separated hosts with schemes and ports", () => {
    const hosts = getConfiguredRemoteHosts({
      env: {
        NODE_ENV: "test",
        TAILSCALE_HOST: "https://sarah.tail1234.ts.net,100.101.218.34:3000",
      },
      rootEnvText: "",
    });

    expect(hosts).toEqual(
      expect.arrayContaining(["sarah.tail1234.ts.net", "sarah", "100.101.218.34"]),
    );
  });
});

describe("getAllowedDevOrigins", () => {
  it("keeps local defaults and adds discovered interface addresses", () => {
    const origins = getAllowedDevOrigins({
      env: { NODE_ENV: "test" },
      rootEnvText: "",
      networkInterfaceMap: {
        Tailscale: [ipv4("100.101.218.34")],
      },
    });

    expect(origins).toEqual(
      expect.arrayContaining(["localhost", "127.0.0.1", "**.*", "100.101.218.34"]),
    );
  });
});

describe("getRootEnvValue", () => {
  it("reads a public frontend setting from repository root env text", () => {
    expect(getRootEnvValue("NEXT_PUBLIC_JARVIS_WAKE_MODEL_URL", {
      env: { NODE_ENV: "test" },
      rootEnvText: "NEXT_PUBLIC_JARVIS_WAKE_MODEL_URL=/jarvis-wake/models/jarvis.onnx\n",
    })).toBe("/jarvis-wake/models/jarvis.onnx");
  });

  it("prefers an explicit process environment value", () => {
    expect(getRootEnvValue("NEXT_PUBLIC_JARVIS_WAKE_MODEL_URL", {
      env: { NODE_ENV: "test", NEXT_PUBLIC_JARVIS_WAKE_MODEL_URL: "/custom/jarvis.onnx" },
      rootEnvText: "NEXT_PUBLIC_JARVIS_WAKE_MODEL_URL=/root/jarvis.onnx\n",
    })).toBe("/custom/jarvis.onnx");
  });
});
