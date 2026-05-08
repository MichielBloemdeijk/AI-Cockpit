import { existsSync, readFileSync } from "node:fs";
import { networkInterfaces, type NetworkInterfaceInfo } from "node:os";
import { resolve } from "node:path";

type AllowedDevOriginsOptions = {
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  rootEnvText?: string;
  networkInterfaceMap?: NodeJS.Dict<NetworkInterfaceInfo[]>;
};

function extractEnvValue(text: string, name: string): string {
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }

    const separator = trimmed.indexOf("=");
    if (separator === -1 || trimmed.slice(0, separator).trim() !== name) {
      continue;
    }

    const rawValue = trimmed.slice(separator + 1).trim();
    if (
      (rawValue.startsWith('"') && rawValue.endsWith('"'))
      || (rawValue.startsWith("'") && rawValue.endsWith("'"))
    ) {
      return rawValue.slice(1, -1).trim();
    }
    return rawValue;
  }

  return "";
}

function readRootEnvText(cwd: string): string {
  const rootEnvPath = resolve(cwd, "..", ".env");
  if (!existsSync(rootEnvPath)) {
    return "";
  }
  return readFileSync(rootEnvPath, "utf8");
}

function normalizeHost(rawValue: string): string | null {
  const trimmed = rawValue.trim();
  if (!trimmed) {
    return null;
  }

  if (trimmed.includes("://")) {
    try {
      return new URL(trimmed).hostname || null;
    } catch {
      return null;
    }
  }

  const withoutPath = trimmed.split("/")[0]?.trim() ?? "";
  if (!withoutPath) {
    return null;
  }

  if (withoutPath.startsWith("[")) {
    const ipv6Match = withoutPath.match(/^\[([^\]]+)\](?::\d+)?$/);
    return ipv6Match?.[1] ?? null;
  }

  return withoutPath.replace(/:\d+$/, "") || null;
}

export function getConfiguredRemoteHosts(options: AllowedDevOriginsOptions = {}): string[] {
  const cwd = options.cwd ?? process.cwd();
  const env = options.env ?? process.env;
  const rootEnvText = options.rootEnvText ?? readRootEnvText(cwd);
  const candidates = [
    env.TAILSCALE_HOST,
    env.NEXT_PUBLIC_TAILSCALE_HOST,
    extractEnvValue(rootEnvText, "TAILSCALE_HOST"),
    extractEnvValue(rootEnvText, "NEXT_PUBLIC_TAILSCALE_HOST"),
  ];
  const hosts = new Set<string>();

  for (const candidate of candidates) {
    if (!candidate) {
      continue;
    }

    for (const rawHost of candidate.split(",")) {
      const normalizedHost = normalizeHost(rawHost);
      if (!normalizedHost) {
        continue;
      }

      hosts.add(normalizedHost);

      if (normalizedHost.includes(".")) {
        hosts.add(normalizedHost.split(".")[0]);
      }
    }
  }

  return Array.from(hosts);
}

export function getAllowedDevOrigins(options: AllowedDevOriginsOptions = {}): string[] {
  const origins = new Set<string>(["localhost", "127.0.0.1"]);
  const networkInterfaceMap = options.networkInterfaceMap ?? networkInterfaces();

  for (const entries of Object.values(networkInterfaceMap)) {
    for (const entry of entries ?? []) {
      if (entry.internal || entry.family !== "IPv4") {
        continue;
      }
      origins.add(entry.address);
    }
  }

  for (const host of getConfiguredRemoteHosts(options)) {
    origins.add(host);
  }

  return Array.from(origins);
}