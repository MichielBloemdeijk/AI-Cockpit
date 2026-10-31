/**
 * Client for the app platform (docs/jarvis-tasks/13-app-platform.md).
 *
 * Installed apps run in a sandboxed iframe without network access. The SDK in the
 * frame (`/api/app-host/_sdk/cockpit-app.js`) posts requests to this page; the
 * bridge below performs them with a token scoped to that one app and posts the
 * result back.
 */
import { resolveApiBase } from "./api";

const BASE = resolveApiBase();

export interface PlatformApp {
  slug: string;
  title: string;
  version: number;
  description: string;
  collections: string[];
  jarvis_summary: string;
  url: string;
}

export interface AppSession extends PlatformApp {
  token: string;
  frame_url: string;
}

export class AppPlatformError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // Keep the status text when the body is not JSON.
    }
    throw new AppPlatformError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

export async function listPlatformApps(): Promise<PlatformApp[]> {
  return readJson(await fetch(`${BASE}/api/app-platform/apps`, { credentials: "include", cache: "no-store" }));
}

export async function openAppSession(slug: string): Promise<AppSession> {
  return readJson(await fetch(`${BASE}/api/app-platform/apps/${encodeURIComponent(slug)}/session`, {
    method: "POST",
    credentials: "include",
  }));
}

type BridgeRequest = {
  cockpit: "request";
  id: number;
  method: "data.get" | "data.put" | "data.delete" | "data.query";
  params: {
    collection?: unknown;
    key?: unknown;
    value?: unknown;
    expectedRevision?: unknown;
    where?: unknown;
    order?: unknown;
    limit?: unknown;
  };
};

function segment(value: unknown, name: string): string {
  if (typeof value !== "string" || !value) throw new AppPlatformError(`${name} is required`, 400);
  return encodeURIComponent(value);
}

/** Build the HTTP request for one SDK call. Exported for tests. */
export function bridgeHttpRequest(slug: string, request: BridgeRequest): { url: string; init: RequestInit; nullOn404: boolean } {
  const { params } = request;
  const base = `${BASE}/api/app-platform/data/${encodeURIComponent(slug)}/${segment(params.collection, "collection")}`;
  switch (request.method) {
    case "data.get":
      return { url: `${base}/${segment(params.key, "key")}`, init: { method: "GET" }, nullOn404: true };
    case "data.put":
      return {
        url: `${base}/${segment(params.key, "key")}`,
        init: {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value: params.value, expected_revision: params.expectedRevision ?? null }),
        },
        nullOn404: false,
      };
    case "data.delete": {
      const query = typeof params.expectedRevision === "number" ? `?expected_revision=${params.expectedRevision}` : "";
      return { url: `${base}/${segment(params.key, "key")}${query}`, init: { method: "DELETE" }, nullOn404: false };
    }
    case "data.query": {
      const search = new URLSearchParams();
      if (params.where && typeof params.where === "object") search.set("where", JSON.stringify(params.where));
      if (typeof params.order === "string") search.set("order", params.order);
      if (typeof params.limit === "number") search.set("limit", String(params.limit));
      const query = search.toString();
      return { url: query ? `${base}?${query}` : base, init: { method: "GET" }, nullOn404: false };
    }
    default:
      throw new AppPlatformError(`Unknown method ${(request as { method: string }).method}`, 400);
  }
}

export interface AppBridgeOptions {
  session: AppSession;
  frame: () => Window | null;
  renewSession: () => Promise<AppSession>;
  fetcher?: typeof fetch;
}

/** Returns a `message` event handler that serves one app frame. */
export function createAppBridge({ session, frame, renewSession, fetcher = fetch }: AppBridgeOptions) {
  let current = session;

  async function authorized(url: string, init: RequestInit): Promise<Response> {
    const send = (token: string) => fetcher(url, {
      ...init,
      credentials: "omit",
      headers: { ...(init.headers as Record<string, string> | undefined), Authorization: `AppPlatform ${token}` },
    });
    const response = await send(current.token);
    if (response.status !== 401) return response;
    current = await renewSession();
    return send(current.token);
  }

  async function perform(request: BridgeRequest): Promise<unknown> {
    const { url, init, nullOn404 } = bridgeHttpRequest(current.slug, request);
    const response = await authorized(url, init);
    if (nullOn404 && response.status === 404) return null;
    const body = await readJson<Record<string, unknown>>(response);
    return request.method === "data.query" ? body.records : request.method === "data.delete" ? body.deleted : body;
  }

  return (event: MessageEvent) => {
    const target = frame();
    if (!target || event.source !== target || !event.data || typeof event.data !== "object") return;
    const message = event.data as { cockpit?: string };
    if (message.cockpit === "ready") {
      target.postMessage({ cockpit: "hello", slug: current.slug, title: current.title, version: current.version }, "*");
      return;
    }
    if (message.cockpit === "error") {
      // Reported so Jarvis can see and repair the app; losing one report is harmless.
      const { message: text, detail } = event.data as { message?: unknown; detail?: unknown };
      void authorized(`${BASE}/api/app-platform/apps/${encodeURIComponent(current.slug)}/errors`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: String(text ?? "").slice(0, 1000), detail: String(detail ?? "").slice(0, 4000) }),
      }).catch(() => undefined);
      return;
    }
    if (message.cockpit !== "request") return;
    const request = event.data as BridgeRequest;
    perform(request).then(
      (result) => target.postMessage({ cockpit: "response", id: request.id, ok: true, result }, "*"),
      (error: unknown) => target.postMessage({
        cockpit: "response",
        id: request.id,
        ok: false,
        status: error instanceof AppPlatformError ? error.status : 0,
        error: error instanceof Error ? error.message : "Request failed",
      }, "*"),
    );
  };
}
