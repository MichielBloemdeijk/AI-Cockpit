import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest } from "./http";

/** A fetch that never answers until its signal aborts, like a stalled backend. */
function stalledFetch() {
  return vi.fn((_input: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
    init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
  }));
}

describe("finite request transport", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("settles a stalled request at its deadline, so later polling can run", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", stalledFetch());
    const pending = expect(apiRequest("/api/jarvis/state", { timeoutMs: 1000 })).rejects.toMatchObject({ kind: "timeout" });
    await vi.advanceTimersByTimeAsync(1000);
    await pending;
  });

  it("keeps a caller's abort distinguishable from a timeout", async () => {
    vi.stubGlobal("fetch", stalledFetch());
    const controller = new AbortController();
    const pending = apiRequest("/api/jarvis/state", { signal: controller.signal });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ kind: "aborted", status: null });
  });

  it("keeps the deadline over a body that never finishes", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(async (_input: string, init?: RequestInit) => ({
      ok: true,
      status: 200,
      text: () => new Promise<string>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
      }),
    })));
    const pending = expect(apiRequest("/api/tasks/1", { timeoutMs: 500 })).rejects.toMatchObject({ kind: "timeout" });
    await vi.advanceTimersByTimeAsync(500);
    await pending;
  });

  it("carries status and backend detail as structured data", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Expected revision 3" }), { status: 409 })));
    const error = await apiRequest("/api/tasks/1/control", { method: "POST", body: {} }).catch((reason) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ kind: "http", status: 409, detail: "Expected revision 3", message: "Expected revision 3" });

    const validation = [{ loc: ["body", "goal"], msg: "Field required" }];
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: validation }), { status: 422 })));
    await expect(apiRequest("/api/tasks", { method: "POST", body: {} }))
      .rejects.toMatchObject({ status: 422, detail: validation, message: "Request failed (HTTP 422)." });

    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 401 })));
    await expect(apiRequest("/api/auth/status")).rejects.toMatchObject({ status: 401, detail: null });
  });

  it("reports an unreachable backend as a network error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed"); }));
    await expect(apiRequest("/api/jarvis/state")).rejects.toMatchObject({ kind: "network" });
  });

  it("sends JSON bodies and accepts empty responses", async () => {
    const fetchMock = vi.fn(async (..._args: unknown[]) => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(apiRequest("/api/jarvis/devices/1", { method: "DELETE" })).resolves.toBeUndefined();
    await apiRequest("/api/tasks", { method: "POST", body: { goal: "Report" } });
    const init = fetchMock.mock.calls[1][1] as RequestInit;
    expect(init).toMatchObject({ method: "POST", credentials: "include", headers: { "Content-Type": "application/json" } });
    expect(JSON.parse(String(init.body))).toEqual({ goal: "Report" });
  });
});
