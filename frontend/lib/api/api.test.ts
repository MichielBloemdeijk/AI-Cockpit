import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./http";

import { login, getAccessConfig } from "./auth";
import { listConversations, streamChat, streamConversationEvents, type ConversationEventView, updateChatSettings } from "./conversations";
import { acceptJarvisTextTurn, closeJarvisLiveSession, createJarvisLiveCall, getJarvisState, getJarvisLogEvents, getJarvisLiveEvents, getJarvisSessionEvents, injectFakeJarvisEvent, submitJarvisLiveTelemetry, startFreshJarvisLog } from "./jarvis";
import { controlTask, controlTaskPool, getTask, getTaskWorkerHealth, getTaskEvents, listTasks, dispatchTask, continueTask } from "./tasks";


const jsonResponse = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

/** The parts of a request the backend contract cares about. */
function expectRequest(fetchMock: ReturnType<typeof vi.fn>, call: number, url: string,
  expected: { method?: string; body?: unknown; keepalive?: boolean } = {}) {
  const [actualUrl, init] = fetchMock.mock.calls[call - 1] as [string, RequestInit];
  expect(actualUrl).toBe(url);
  expect(init.method ?? "GET").toBe(expected.method ?? "GET");
  expect(init.credentials).toBe("include");
  if (expected.body !== undefined) expect(JSON.parse(String(init.body))).toEqual(expected.body);
  else expect(init.body).toBeUndefined();
  if (expected.keepalive) expect(init.keepalive).toBe(true);
}

describe("global task api", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("lists, dispatches, and loads first-class task identities", async () => {
    const fetchMock = vi.fn(async (..._args: unknown[]) => jsonResponse({ id: "task-1" }));
    vi.stubGlobal("fetch", fetchMock);

    await listTasks(["queued", "running"]);
    await dispatchTask({ goal: "Review", idempotency_key: "dispatch-1" });
    await getTask("task/1");

    expectRequest(fetchMock, 1, "/api/tasks?status=queued&status=running");
    expectRequest(fetchMock, 2, "/api/tasks", { method: "POST", body: { goal: "Review", idempotency_key: "dispatch-1" } });
    expectRequest(fetchMock, 3, "/api/tasks/task%2F1");
  });

  it("sends task controls with an idempotency key and the expected run and revision", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "cancel-key" });
    const fetchMock = vi.fn(async (..._args: unknown[]) => jsonResponse({ task: { id: "task-1" }, command: { id: "c" } }));
    vi.stubGlobal("fetch", fetchMock);

    await controlTask("task-1", "cancel", {}, "run-2", 4);

    expectRequest(fetchMock, 1, "/api/tasks/task-1/control", { method: "POST", body: {
      action: "cancel", parameters: {}, idempotency_key: "cancel-key", expected_run_id: "run-2", expected_control_revision: 4,
    } });
  });

  it("sends explicit result intent independently of execution preferences", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "followup-key" });
    const fetchMock = vi.fn(async (..._args: unknown[]) => jsonResponse({ result_url: "/apps/documents?collection=document&key=run-1" }));
    vi.stubGlobal("fetch", fetchMock);
    await dispatchTask({ idempotency_key: "test-dispatch", goal: "Report", result: { mode: "new" } });
    expect(JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body)).result).toEqual({ mode: "new" });
    await continueTask("task/1", "Edit", { mode: "update", result_id: "result-1", expected_revision: 2 });
    expect(fetchMock.mock.calls[1][0]).toBe("/api/tasks/task%2F1/turns");
    expect(JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body))).toEqual({ content: "Edit", idempotency_key: "followup-key",
      result: { mode: "update", result_id: "result-1", expected_revision: 2 } });
  });

  it("sends audited pool replacement with an idempotency key", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "pool-key" });
    const fetchMock = vi.fn(async (..._args: unknown[]) => jsonResponse({ id: "command-1" }));
    vi.stubGlobal("fetch", fetchMock);

    await controlTaskPool("replace_worker", "Replace stalled worker", "worker-1");

    expectRequest(fetchMock, 1, "/api/tasks/pool/control", { method: "POST", body: {
        action: "replace_worker", idempotency_key: "pool-key",
        reason: "Replace stalled worker", worker_id: "worker-1",
      } });
  });

  it("reads worker health from the task domain", async () => {
    const fetchMock = vi.fn(async (..._args: unknown[]) => jsonResponse({ status: "online", worker_id: "worker-1", heartbeat_at: "2026-08-27T12:00:00Z" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getTaskWorkerHealth()).resolves.toMatchObject({ status: "online" });
    expectRequest(fetchMock, 1, "/api/tasks/worker");
  });
});

describe("Live telemetry api", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("returns the accepted prefix count", async () => {
    const fetchMock = vi.fn(async (..._args: unknown[]) => jsonResponse({ accepted: 1 }));
    vi.stubGlobal("fetch", fetchMock);

    const accepted = await submitJarvisLiveTelemetry([{
      trace_id: "trace-1",
      client_sequence: 4,
      source: "browser",
      event_type: "turn.result",
      observed_at: "2026-09-06T00:00:00.000Z",
    }]);

    expect(accepted).toBe(1);
    expect(fetchMock).toHaveBeenCalledWith("/api/jarvis/live/telemetry", expect.objectContaining({
      keepalive: true,
    }));
  });
});

describe("Jarvis daily-log api", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads Jarvis state without creating a log and filters shared history", async () => {
    const fetchMock = vi.fn(async (..._args: unknown[]) => jsonResponse({ operational_day: "2026-08-27", state_revision: 0, current_log: null }));
    vi.stubGlobal("fetch", fetchMock);

    await getJarvisState();
    await listConversations(true, "jarvis");
    await listConversations(true, "all", true);

    expectRequest(fetchMock, 1, "/api/jarvis/state");
    expectRequest(fetchMock, 2, "/api/conversations?include_archived=true&kind=jarvis");
    expectRequest(fetchMock, 3, "/api/conversations?include_archived=true&kind=all&archived_only=true");
  });

  it("replays missed live events from a durable cursor", async () => {
    const fetchMock = vi.fn(async (..._args: unknown[]) => jsonResponse({ latest_sequence: 8, events: [{ sequence: 8 }] }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getJarvisLiveEvents(7)).resolves.toMatchObject({ latest_sequence: 8 });
    expectRequest(fetchMock, 1, "/api/jarvis/live/replay?after=7&limit=250");
  });

  it("sends idempotency keys for text turns and explicit fresh starts", async () => {
    const fetchMock = vi.fn(async (..._args: unknown[]) => jsonResponse({ current_log: { id: "jarvis-2" } }));
    vi.stubGlobal("fetch", fetchMock);

    await acceptJarvisTextTurn("Status check", "turn-key");
    const fresh = await startFreshJarvisLog("fresh-key");

    expectRequest(fetchMock, 1, "/api/jarvis/turns/text", { method: "POST", body: { content: "Status check", idempotency_key: "turn-key" } });
    expectRequest(fetchMock, 2, "/api/jarvis/logs/new", { method: "POST", body: { idempotency_key: "fresh-key" } });
    expect(fresh).toEqual({ id: "jarvis-2" });
  });

  it("replays independent domains and controls the fake provider lab", async () => {
    const fetchMock = vi.fn(async (..._args: unknown[]) => jsonResponse({ events: [], latest_sequence: 0, role: "controller", session: null }));
    vi.stubGlobal("fetch", fetchMock);

    await getJarvisLogEvents("log/1", 4);
    await getTaskEvents(8);
    await getJarvisSessionEvents("session/1", 3);
    await createJarvisLiveCall("browser-1", "controller", "fake", undefined, {
      time_zone: "Europe/Amsterdam",
      utc_offset_minutes: 120,
      observed_at: "2026-09-02T20:00:00.000Z",
    });
    await injectFakeJarvisEvent("session/1", {
      generation: 2,
      event_id: "event-1",
      kind: "user_speaking",
    });
    await closeJarvisLiveSession("session/1", 2);

    expectRequest(fetchMock, 1, "/api/jarvis/logs/log%2F1/events?after=4");
    expectRequest(fetchMock, 2, "/api/tasks/events?after=8");
    expectRequest(fetchMock, 3, "/api/jarvis/live/sessions/session%2F1/events?after=3");
    expectRequest(fetchMock, 4, "/api/jarvis/live/calls", { method: "POST", body: {
        controller_id: "browser-1",
        role: "controller",
        provider_adapter: "fake",
        client_context: {
          time_zone: "Europe/Amsterdam",
          utc_offset_minutes: 120,
          observed_at: "2026-09-02T20:00:00.000Z",
        },
      } });
    expectRequest(fetchMock, 5, "/api/jarvis/fake/sessions/session%2F1/events", { method: "POST", body: { generation: 2, event_id: "event-1", kind: "user_speaking" } });
    expectRequest(fetchMock, 6, "/api/jarvis/live/sessions/session%2F1?generation=2", { method: "DELETE", keepalive: true });
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
      vi.fn(async () => new Response(null, { status: 204 })),
    );

    const { login: developmentLogin } = await import("./auth");

    await developmentLogin("anything");

    expectRequest(vi.mocked(fetch), 1, "/api/auth/login", { method: "POST", body: { password: "anything" } });
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
      vi.fn(async () => jsonResponse({ detail: "Invalid credentials" }, 401)),
    );

    await expect(login("anything")).rejects.toMatchObject({ message: "Invalid password.", status: 401 });
    await expect(login("anything")).rejects.toBeInstanceOf(ApiError);
  });
});

describe("request timeouts", () => {
  it("fails fast when a page-load request times out", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((_input: string, init?: RequestInit) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }));
    vi.stubGlobal("fetch", fetchMock);

    const pending = expect(getAccessConfig()).rejects.toThrow("Request timed out. Please try again.");
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
    const fetchMock = vi.fn(async (..._args: unknown[]) => jsonResponse({
        available_models: ["anthropic/claude-sonnet-4-5", "moonshotai/kimi-k2.6"],
        defaults: { mode: "single", single_model: "anthropic/claude-sonnet-4-5", council_models: ["anthropic/claude-sonnet-4-5"], synthesizer_model: "anthropic/claude-sonnet-4-5" },
        task_agent_model: "moonshotai/kimi-k2.6",
      }));
    vi.stubGlobal("fetch", fetchMock);

    await updateChatSettings({
      defaults: {
        mode: "single",
        single_model: "anthropic/claude-sonnet-4-5",
        council_models: ["anthropic/claude-sonnet-4-5"],
        synthesizer_model: "anthropic/claude-sonnet-4-5",
      },
      task_agent_model: "moonshotai/kimi-k2.6",
    });

    expectRequest(fetchMock, 1, "/api/chat/settings", { method: "PUT", body: {
        defaults: {
          mode: "single",
          single_model: "anthropic/claude-sonnet-4-5",
          council_models: ["anthropic/claude-sonnet-4-5"],
          synthesizer_model: "anthropic/claude-sonnet-4-5",
        },
        task_agent_model: "moonshotai/kimi-k2.6",
      } });
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
      `data: ${JSON.stringify({ type: "event", event: { id: "event-1", run_id: "run-1", sequence: 1, branch_key: "main", parent_event_id: null, actor_kind: "assistant", event_type: "chat.timeline.item.committed", created_at: "2026-05-01T00:00:01Z", schema_version: 1, payload_json: { item_id: "assistant:run-1:1:text", role: "assistant", kind: "status", content: "Reading" } } })}\n\n`,
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
    });
    vi.stubGlobal("fetch", fetchMock);

    const chunks: string[] = [];
    const metadata: Array<{ conversation_id: string; run_id: string; model?: string }> = [];
    const events: Array<{ id: string; event_type: string }> = [];

    await new Promise<void>((resolve, reject) => {
      streamChat({
        messages: [{ role: "user", content: "hello" }],
        conversationId: "conv-1",
        branchKey: "feature-branch",
        model: "model-1",
        onMetadata: (data) => metadata.push(data),
        onEvent: (event) => events.push({ id: event.id, event_type: event.event_type }),
        onChunk: (chunk) => chunks.push(chunk),
        onDone: resolve,
        onError: (err) => reject(new Error(err)),
      });
    });

    expect(metadata).toEqual([{ type: "metadata", conversation_id: "conv-1", run_id: "run-1", model: "model-1" }]);
    expect(events).toEqual([{ id: "event-1", event_type: "chat.timeline.item.committed" }]);
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
    const fetchMock = vi.fn(async (..._args: unknown[]) => jsonResponse({
        conversation_id: "conversation / one",
        active_branch_key: "main",
        events: [polledEvent],
        artifacts: [],
        timeline_items: [],
        active_run: null,
      }));
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
    expectRequest(fetchMock, 1, "/api/conversations/conversation%20%2F%20one/events?branch_key=main&after_sequence=43");
    expect(events.map((event) => event.sequence)).toEqual([43, 44]);

    instance.onerror?.(new Event("error"));
    expect(errors[0]?.message).toBe("Conversation event stream disconnected");
    expect(close).toHaveBeenCalledTimes(1);

    controller.abort();
    expect(close).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
  });
});
