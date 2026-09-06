import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "./route";

describe("Jarvis live stream proxy", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("forwards authentication and returns the upstream stream without consuming it", async () => {
    const upstreamBody = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("id: 42\nevent: jarvis\n"));
      },
    });
    const fetchMock = vi.fn().mockResolvedValue(new Response(upstreamBody, {
      headers: { "Content-Type": "text/event-stream; charset=utf-8" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(new Request(
      "http://phone.local/api/jarvis/live?cursor=41",
      { headers: { Cookie: "cockpit_session=token", Authorization: "Bearer device" } },
    ));
    const [url, init] = fetchMock.mock.calls[0] as [URL, RequestInit];
    const forwardedHeaders = init.headers as Headers;
    const reader = response.body?.getReader();
    const first = await reader?.read();

    expect(url.toString()).toBe("http://127.0.0.1:8000/api/jarvis/live?cursor=41");
    expect(forwardedHeaders.get("cookie")).toBe("cockpit_session=token");
    expect(forwardedHeaders.get("authorization")).toBe("Bearer device");
    expect(init.cache).toBe("no-store");
    expect(new TextDecoder().decode(first?.value)).toBe("id: 42\nevent: jarvis\n");
    expect(response.headers.get("cache-control")).toBe("no-cache, no-transform");

    await reader?.cancel();
  });
});
