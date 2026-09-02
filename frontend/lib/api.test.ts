import { afterEach, describe, expect, it, vi } from "vitest";

import {
  acceptJarvisTextTurn,
  cancelTask,
  closeJarvisRealtimeSession,
  createJarvisRealtimeCall,
  createGeneratedApp,
  getGeneratedAppBySlug,
  getJarvisState,
  getJarvisLogEvents,
  getJarvisSessionEvents,
  getTask,
  getTaskWorkerHealth,
  getTaskEvents,
  injectFakeJarvisEvent,
  listConversations,
  listGeneratedApps,
  listTasks,
  login,
  streamChat,
  streamConversationEvents,
  startFreshJarvisLog,
  dispatchTask,
  type ConversationEventView,
  updateChatSettings,
} from "./api";

describe("global task api", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("lists, dispatches, and loads first-class task identities", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id: "task-1" }),
    } satisfies Partial<Response>);
    vi.stubGlobal("fetch", fetchMock);

    await listTasks(["queued", "running"]);
    await dispatchTask({ goal: "Review", idempotency_key: "dispatch-1" });
    await getTask("task/1");

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/tasks?status=queued&status=running", {
      credentials: "include",
      cache: "no-store",
    });
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ goal: "Review", idempotency_key: "dispatch-1" }),
    });
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/tasks/task%2F1", {
      credentials: "include",
      cache: "no-store",
    });
  });

  it("sends an idempotency key for task actions", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "cancel-key" });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: "task-1" }) } satisfies Partial<Response>);
    vi.stubGlobal("fetch", fetchMock);

    await cancelTask("task-1");

    expect(fetchMock).toHaveBeenCalledWith("/api/tasks/task-1/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ idempotency_key: "cancel-key" }),
    });
  });

  it("reads worker health from the task domain", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: "online", worker_id: "worker-1", heartbeat_at: "2026-08-27T12:00:00Z" }),
    } satisfies Partial<Response>);
    vi.stubGlobal("fetch", fetchMock);

    await expect(getTaskWorkerHealth()).resolves.toMatchObject({ status: "online" });
    expect(fetchMock).toHaveBeenCalledWith("/api/tasks/worker", {
      credentials: "include",
      cache: "no-store",
    });
  });
});

describe("Jarvis daily-log api", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads Jarvis state without creating a log and filters shared history", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ operational_day: "2026-08-27", state_revision: 0, current_log: null }),
    } satisfies Partial<Response>);
    vi.stubGlobal("fetch", fetchMock);

    await getJarvisState();
    await listConversations(true, "jarvis");
    await listConversations(true, "all", true);

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/jarvis/state", {
      credentials: "include",
      cache: "no-store",
    });
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/conversations?include_archived=true&kind=jarvis",
      { credentials: "include", cache: "no-store" },
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/api/conversations?include_archived=true&kind=all&archived_only=true",
      { credentials: "include", cache: "no-store" },
    );
  });

  it("sends idempotency keys for text turns and explicit fresh starts", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ current_log: { id: "jarvis-2" } }),
    } satisfies Partial<Response>);
    vi.stubGlobal("fetch", fetchMock);

    await acceptJarvisTextTurn("Status check", "turn-key");
    const fresh = await startFreshJarvisLog("fresh-key");

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/jarvis/turns/text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ content: "Status check", idempotency_key: "turn-key" }),
    });
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/jarvis/logs/new", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ idempotency_key: "fresh-key" }),
    });
    expect(fresh).toEqual({ id: "jarvis-2" });
  });

  it("replays independent domains and controls the fake provider lab", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ events: [], latest_sequence: 0, role: "controller", session: null }),
    } satisfies Partial<Response>);
    vi.stubGlobal("fetch", fetchMock);

    await getJarvisLogEvents("log/1", 4);
    await getTaskEvents(8);
    await getJarvisSessionEvents("session/1", 3);
    await createJarvisRealtimeCall("browser-1", "controller", "fake", undefined, {
      time_zone: "Europe/Amsterdam",
      utc_offset_minutes: 120,
      observed_at: "2026-09-02T20:00:00.000Z",
    });
    await injectFakeJarvisEvent("session/1", {
      generation: 2,
      event_id: "event-1",
      kind: "user_speaking",
    });
    await closeJarvisRealtimeSession("session/1", 2);

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/jarvis/logs/log%2F1/events?after=4", {
      credentials: "include",
      cache: "no-store",
    });
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/tasks/events?after=8", {
      credentials: "include",
      cache: "no-store",
    });
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/jarvis/sessions/session%2F1/events?after=3", {
      credentials: "include",
      cache: "no-store",
    });
    expect(fetchMock).toHaveBeenNthCalledWith(4, "/api/jarvis/realtime/calls", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({
        controller_id: "browser-1",
        role: "controller",
        provider_adapter: "fake",
        client_context: {
          time_zone: "Europe/Amsterdam",
          utc_offset_minutes: 120,
          observed_at: "2026-09-02T20:00:00.000Z",
        },
      }),
    });
    expect(fetchMock).toHaveBeenNthCalledWith(5, "/api/jarvis/fake/sessions/session%2F1/events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ generation: 2, event_id: "event-1", kind: "user_speaking" }),
    });
    expect(fetchMock).toHaveBeenNthCalledWith(6, "/api/jarvis/realtime/sessions/session%2F1?generation=2", {
      method: "DELETE",
      credentials: "include",
    });
  });
});

describe("login", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("uses the same-origin API proxy in development without an explicit override", async () => {
    vi.resetModules();
    vi.stubEnv("NODE_ENV", "development");
    vi.stubGlobal("window", {
      location: {
        protocol: "http:",
        hostname: "ai-cockpit.tail1234.ts.net",
        port: "3000",
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
          closed: Promise.resolve(undefined),
          cancel: vi.fn(async () => undefined),
          read: vi.fn(async (): Promise<ReadableStreamReadResult<Uint8Array<ArrayBuffer>>> => {
            if (index >= frames.length) {
              return { done: true, value: undefined };
            }
            const value = encoder.encode(frames[index]) as Uint8Array<ArrayBuffer>;
            index += 1;
            return { done: false, value };
          }),
          releaseLock: vi.fn(),
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

describe("streamConversationEvents", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("uses native SSE with the durable cursor and closes cleanly", async () => {
    vi.useFakeTimers();
    const listeners = new Map<string, (event: Event) => void>();
    const close = vi.fn();
    class FakeEventSource {
      static instance: FakeEventSource | null = null;
      readonly url: string;
      readonly withCredentials: boolean;
      onerror: ((event: Event) => void) | null = null;

      constructor(url: string | URL, init?: EventSourceInit) {
        this.url = String(url);
        this.withCredentials = Boolean(init?.withCredentials);
        FakeEventSource.instance = this;
      }

      addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
        listeners.set(type, listener as (event: Event) => void);
      }

      close() { close(); }
    }
    vi.stubGlobal("EventSource", FakeEventSource);
    const polledEvent: ConversationEventView = {
      id: "event-44",
      run_id: "run-1",
      sequence: 44,
      branch_key: "main",
      parent_event_id: null,
      actor_kind: "assistant",
      event_type: "agent.run.completed",
      created_at: "2026-08-24T00:00:01Z",
      schema_version: 1,
      payload_json: { summary: "Done" },
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        conversation_id: "conversation / one",
        active_branch_key: "main",
        events: [polledEvent],
        artifacts: [],
        timeline_items: [],
        active_run: null,
      }),
    } satisfies Partial<Response>);
    vi.stubGlobal("fetch", fetchMock);
    const events: ConversationEventView[] = [];
    const errors: Error[] = [];

    const controller = streamConversationEvents("conversation / one", 42, (event) => events.push(event), (error) => errors.push(error));
    const instance = FakeEventSource.instance!;
    expect(instance.url).toBe("/api/conversations/conversation%20%2F%20one/stream?after_sequence=42");
    expect(instance.withCredentials).toBe(true);

    listeners.get("conversation.event")?.(new MessageEvent("conversation.event", {
      data: JSON.stringify({
        type: "event",
        event: {
          id: "event-43",
          run_id: "run-1",
          sequence: 43,
          branch_key: "main",
          parent_event_id: null,
          actor_kind: "assistant",
          event_type: "conversation.assistant.message.completed",
          created_at: "2026-08-24T00:00:00Z",
          schema_version: 1,
          payload_json: { content: "Done" },
        },
      }),
    }));
    expect(events).toHaveLength(1);
    expect(events[0].sequence).toBe(43);

    await vi.advanceTimersByTimeAsync(750);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/conversations/conversation%20%2F%20one/events?branch_key=main&after_sequence=43",
      { credentials: "include", cache: "no-store" },
    );
    expect(events.map((event) => event.sequence)).toEqual([43, 44]);

    instance.onerror?.(new Event("error"));
    expect(errors[0]?.message).toBe("Conversation event stream disconnected");
    expect(close).toHaveBeenCalledTimes(1);

    controller.abort();
    expect(close).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
  });
});
