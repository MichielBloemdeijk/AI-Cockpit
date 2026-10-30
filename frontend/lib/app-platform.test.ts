import { describe, expect, it, vi } from "vitest";

import { bridgeHttpRequest, createAppBridge, type AppSession } from "./app-platform";

const session: AppSession = {
  slug: "notes", title: "Notes", version: 1, description: "", collections: ["note"], jarvis_summary: "",
  url: "/apps/notes", token: "token-1", frame_url: "/api/app-host/notes/1/index.html",
};

function fakeFrame() {
  const posted: unknown[] = [];
  return { window: { postMessage: (message: unknown) => posted.push(message) } as unknown as Window, posted };
}

function message(source: Window, data: unknown) {
  return { source, data } as MessageEvent;
}

describe("app bridge", () => {
  it("maps SDK calls onto the scoped data routes", () => {
    const put = bridgeHttpRequest("notes", { cockpit: "request", id: 1, method: "data.put",
      params: { collection: "note", key: "a b", value: { text: "x" }, expectedRevision: 0 } });
    expect(put.url).toBe("/api/app-platform/data/notes/note/a%20b");
    expect(JSON.parse(String(put.init.body))).toEqual({ value: { text: "x" }, expected_revision: 0 });

    const query = bridgeHttpRequest("notes", { cockpit: "request", id: 2, method: "data.query",
      params: { collection: "note", where: { pinned: true }, order: "-updated_at", limit: 5 } });
    expect(query.url).toBe("/api/app-platform/data/notes/note?where=%7B%22pinned%22%3Atrue%7D&order=-updated_at&limit=5");
  });

  it("answers only its own frame, uses the app token and renews it once on 401", async () => {
    const frame = fakeFrame();
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response("{}", { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ records: [{ key: "a" }] }), { status: 200 }));
    const renewSession = vi.fn(async () => ({ ...session, token: "token-2" }));
    const handler = createAppBridge({ session, frame: () => frame.window, renewSession, fetcher });

    handler(message({} as Window, { cockpit: "request", id: 9, method: "data.query", params: { collection: "note" } }));
    handler(message(frame.window, { cockpit: "ready" }));
    handler(message(frame.window, { cockpit: "request", id: 1, method: "data.query", params: { collection: "note" } }));
    await vi.waitFor(() => expect(frame.posted).toHaveLength(2));

    expect(fetcher).toHaveBeenCalledTimes(2);
    const [, firstInit] = fetcher.mock.calls[0];
    expect(firstInit.headers.Authorization).toBe("AppPlatform token-1");
    expect(firstInit.credentials).toBe("omit");
    expect(fetcher.mock.calls[1][1].headers.Authorization).toBe("AppPlatform token-2");
    expect(frame.posted).toEqual([
      { cockpit: "hello", slug: "notes", title: "Notes", version: 1 },
      { cockpit: "response", id: 1, ok: true, result: [{ key: "a" }] },
    ]);
  });

  it("returns null for a missing record and reports conflicts with their status", async () => {
    const frame = fakeFrame();
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response("{}", { status: 404 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Expected revision 0, found 1" }), { status: 409 }));
    const handler = createAppBridge({ session, frame: () => frame.window, renewSession: vi.fn(), fetcher });

    handler(message(frame.window, { cockpit: "request", id: 1, method: "data.get", params: { collection: "note", key: "a" } }));
    handler(message(frame.window, { cockpit: "request", id: 2, method: "data.put",
      params: { collection: "note", key: "a", value: {}, expectedRevision: 0 } }));
    await vi.waitFor(() => expect(frame.posted).toHaveLength(2));

    expect(frame.posted).toContainEqual({ cockpit: "response", id: 1, ok: true, result: null });
    expect(frame.posted).toContainEqual({ cockpit: "response", id: 2, ok: false, status: 409,
      error: "Expected revision 0, found 1" });
  });
});
