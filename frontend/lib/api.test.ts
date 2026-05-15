import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createGeneratedApp,
  getGeneratedAppBySlug,
  listGeneratedApps,
  login,
  streamChat,
  updateChatSettings,
} from "./api";

describe("login", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("uses the same-origin api path in development without an explicit override", async () => {
    vi.resetModules();
    vi.stubEnv("NODE_ENV", "development");
    vi.stubGlobal("window", {
      location: {
        protocol: "http:",
        hostname: "ai-cockpit.tail1234.ts.net",
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
      } satisfies Partial<Response>),
    );

    const { login: developmentLogin } = await import("./api");

    await developmentLogin("anything");

    expect(fetch).toHaveBeenCalledWith("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ password: "anything" }),
    });
  });

  it("throws a backend-unreachable message for network failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("fetch failed")),
    );

    await expect(login("anything")).rejects.toThrow(
      "Unable to reach the backend. Check that the app is running.",
    );
  });

  it("keeps invalid password messaging only for 401 responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
      } satisfies Partial<Response>),
    );

    await expect(login("anything")).rejects.toThrow("Invalid password.");
  });
});

describe("generated app api", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists generated apps from the apps endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ id: "app-1", slug: "hello-world" }],
    } satisfies Partial<Response>);
    vi.stubGlobal("fetch", fetchMock);

    const result = await listGeneratedApps();

    expect(fetchMock).toHaveBeenCalledWith("/api/apps", expect.objectContaining({ credentials: "include", signal: expect.any(AbortSignal) }));
    expect(result).toEqual([{ id: "app-1", slug: "hello-world" }]);
  });

  it("creates generated apps through the registry endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id: "app-1", slug: "hello-world" }),
    } satisfies Partial<Response>);
    vi.stubGlobal("fetch", fetchMock);

    await createGeneratedApp({ title: "Hello World", status: "draft" });

    expect(fetchMock).toHaveBeenCalledWith("/api/apps", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ title: "Hello World", status: "draft" }),
    });
  });

  it("fetches app detail by slug", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id: "app-1", slug: "hello-world" }),
    } satisfies Partial<Response>);
    vi.stubGlobal("fetch", fetchMock);

    await getGeneratedAppBySlug("hello-world");

    expect(fetchMock).toHaveBeenCalledWith("/api/apps/slug/hello-world", { credentials: "include" });
  });

  it("fails fast when generated app loading times out", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((_input: string, init?: RequestInit) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }));
    vi.stubGlobal("fetch", fetchMock);

    const pending = expect(listGeneratedApps()).rejects.toThrow("Request timed out. Please try again.");
    await vi.advanceTimersByTimeAsync(10000);

    await pending;
    vi.useRealTimers();
  });
});

describe("chat settings api", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("updates settings with a dedicated task agent model", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        available_models: ["anthropic/claude-sonnet-4-5", "moonshotai/kimi-k2.6"],
        defaults: { mode: "single", single_model: "anthropic/claude-sonnet-4-5", council_models: ["anthropic/claude-sonnet-4-5"], synthesizer_model: "anthropic/claude-sonnet-4-5", tool_flags: { workspace_search: true, python_execution: true } },
        task_agent_model: "moonshotai/kimi-k2.6",
      }),
    } satisfies Partial<Response>);
    vi.stubGlobal("fetch", fetchMock);

    await updateChatSettings({
      defaults: {
        mode: "single",
        single_model: "anthropic/claude-sonnet-4-5",
        council_models: ["anthropic/claude-sonnet-4-5"],
        synthesizer_model: "anthropic/claude-sonnet-4-5",
        tool_flags: { workspace_search: true, python_execution: true },
      },
      task_agent_model: "moonshotai/kimi-k2.6",
    });

    expect(fetchMock).toHaveBeenCalledWith("/api/chat/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({
        defaults: {
          mode: "single",
          single_model: "anthropic/claude-sonnet-4-5",
          council_models: ["anthropic/claude-sonnet-4-5"],
          synthesizer_model: "anthropic/claude-sonnet-4-5",
          tool_flags: { workspace_search: true, python_execution: true },
        },
        task_agent_model: "moonshotai/kimi-k2.6",
      }),
    });
  });
});

describe("streamChat", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("sends branch-aware streaming chat requests and forwards SSE chunks", async () => {
    const encoder = new TextEncoder();
    const frames = [
      `data: ${JSON.stringify({ type: "metadata", conversation_id: "conv-1", run_id: "run-1", model: "model-1" })}\n\n`,
      `data: ${JSON.stringify({ type: "agent_stream", stream: { kind: "thought_delta", run_id: "run-1", step: 1, delta: "Looking " } })}\n\n`,
      `data: ${JSON.stringify({ type: "event", event: { id: "event-1", run_id: "run-1", sequence: 1, branch_key: "main", parent_event_id: null, actor_kind: "assistant", event_type: "agent.plan.created", created_at: "2026-05-01T00:00:01Z", schema_version: 1, payload_json: { summary: "Plan", steps: ["Step"], open_questions: [], assumptions: [] } } })}\n\n`,
      `data: ${JSON.stringify({ type: "chunk", content: "hello" })}\n\n`,
      `data: ${JSON.stringify({ type: "done" })}\n\n`,
    ];
    let index = 0;
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: {
        getReader: () => ({
          read: vi.fn(async () => {
            if (index >= frames.length) {
              return { done: true, value: undefined };
            }
            const value = encoder.encode(frames[index]);
            index += 1;
            return { done: false, value };
          }),
        }),
      },
    } satisfies Partial<Response>);
    vi.stubGlobal("fetch", fetchMock);

    const chunks: string[] = [];
    const metadata: Array<{ conversation_id: string; run_id: string; model?: string }> = [];
    const events: Array<{ id: string; event_type: string }> = [];
    const liveUpdates: Array<{ kind: string; step: number }> = [];

    await new Promise<void>((resolve, reject) => {
      streamChat({
        messages: [{ role: "user", content: "hello" }],
        conversationId: "conv-1",
        branchKey: "feature-branch",
        model: "model-1",
        onMetadata: (data) => metadata.push(data),
        onAgentStream: (update) => liveUpdates.push({ kind: update.kind, step: update.step }),
        onEvent: (event) => events.push({ id: event.id, event_type: event.event_type }),
        onChunk: (chunk) => chunks.push(chunk),
        onDone: resolve,
        onError: (err) => reject(new Error(err)),
      });
    });

    expect(metadata).toEqual([{ type: "metadata", conversation_id: "conv-1", run_id: "run-1", model: "model-1" }]);
    expect(liveUpdates).toEqual([{ kind: "thought_delta", step: 1 }]);
    expect(events).toEqual([{ id: "event-1", event_type: "agent.plan.created" }]);
    expect(chunks).toEqual(["hello"]);
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(String((init as RequestInit).body))).toMatchObject({
      conversation_id: "conv-1",
      branch_key: "feature-branch",
      stream: true,
      council_mode: false,
      model: "model-1",
    });
  });
});