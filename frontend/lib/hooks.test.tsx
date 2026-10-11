// @vitest-environment jsdom

import React from "react";
import { createRoot, Root } from "react-dom/client";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildConversationDetail,
  buildConversationEventsResponse,
  DEFAULT_SESSION_METADATA,
} from "./test-fixtures";

const apiMocks = vi.hoisted(() => ({
  answerTaskQuestion: vi.fn(),
  archiveConversation: vi.fn(),
  cancelTask: vi.fn(),
  checkAuth: vi.fn(async () => true),
  createConversation: vi.fn(),
  createConversationMessage: vi.fn(),
  createTask: vi.fn(),
  executeConversationTool: vi.fn(),
  getConversation: vi.fn(),
  getConversationEvents: vi.fn(),
  getTask: vi.fn(),
  listTasks: vi.fn(),
  listConversations: vi.fn(),
  resendConversationBranch: vi.fn(),
  resumeTask: vi.fn(),
  streamChat: vi.fn(),
  streamTaskEvents: vi.fn(),
}));

const chatHistoryMocks = vi.hoisted(() => ({
  mapConversationMessages: vi.fn(),
  mapConversationEventMessages: vi.fn(),
}));

vi.mock("./api", () => apiMocks);
vi.mock("./chat-history", () => ({
  mapConversationMessages: chatHistoryMocks.mapConversationMessages,
  mapConversationEventMessages: chatHistoryMocks.mapConversationEventMessages,
}));

chatHistoryMocks.mapConversationEventMessages.mockImplementation((events: Array<{ id: string; run_id: string | null; event_type: string }>) => events.map((event) => ({
    id: `event:${event.id}`,
    runId: event.run_id,
    role: "assistant",
    kind: "plan",
    title: event.event_type,
    content: event.event_type,
  })));

import { useChat } from "./hooks";

let latestHook: ReturnType<typeof useChat> | null = null;

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function Harness() {
  const hook = useChat();

  React.useEffect(() => {
    latestHook = hook;
  }, [hook]);

  return null;
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("useChat single-model streaming", () => {
  let container: HTMLDivElement;
  let root: Root;
  let finishStream: (() => void) | null;

  beforeEach(async () => {
    latestHook = null;
    finishStream = null;
    vi.clearAllMocks();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    apiMocks.listConversations.mockResolvedValue([]);
    apiMocks.getConversation.mockImplementation(async (conversationId: string) => buildConversationDetail(conversationId));
    apiMocks.getConversationEvents.mockResolvedValue(buildConversationEventsResponse("conv-1"));
    chatHistoryMocks.mapConversationMessages.mockReturnValue([]);
    apiMocks.streamChat.mockImplementation((opts: {
      onMetadata?: (data: { conversation_id: string; run_id: string; model?: string }) => void;
      onAgentStream?: (update: { kind: string; run_id: string; step: number; tool?: string; ok?: boolean; delta?: string; content?: string }) => void;
      onEvent?: (event: { id: string; run_id: string | null; event_type: string; payload_json?: Record<string, unknown>; created_at?: string; sequence?: number }) => void;
      onChunk: (chunk: string) => void;
      onDone: () => void;
    }) => {
      opts.onMetadata?.({ conversation_id: "conv-1", run_id: "run-1", model: "anthropic/claude-sonnet-4-5" });
      opts.onAgentStream?.({ kind: "thought_delta", run_id: "run-1", step: 1, delta: "Looking " });
      opts.onAgentStream?.({ kind: "thought_done", run_id: "run-1", step: 1, content: "Looking around" });
      opts.onAgentStream?.({ kind: "text_delta", run_id: "run-1", step: 1, delta: "Reading " });
      opts.onAgentStream?.({ kind: "text_done", run_id: "run-1", step: 1, content: "Reading frontend/lib/hooks.ts" });
      opts.onAgentStream?.({ kind: "tool_started", run_id: "run-1", step: 1, tool: "file_read", content: "Reading lib/hooks.ts" });
      opts.onAgentStream?.({ kind: "tool_progress", run_id: "run-1", step: 1, tool: "file_read", content: "Loaded 1200 bytes from frontend/lib/hooks.ts" });
      opts.onAgentStream?.({ kind: "tool_done", run_id: "run-1", step: 1, tool: "file_read", ok: true, content: "Finished reading lib/hooks.ts" });
      opts.onEvent?.({
        id: "event-1",
        run_id: "run-1",
        event_type: "chat.timeline.item.committed",
        sequence: 1,
        created_at: "2026-05-01T00:00:01Z",
        payload_json: {
          item_id: "assistant:run-1:1:text",
          role: "assistant",
          kind: "status",
          content: "Reading frontend/lib/hooks.ts",
          step: 1,
        },
      });
      opts.onEvent?.({
        id: "event-3",
        run_id: "run-1",
        event_type: "chat.timeline.item.committed",
        sequence: 3,
        created_at: "2026-05-01T00:00:03Z",
        payload_json: {
          item_id: "tool:run-1:1:file_read",
          role: "assistant",
          kind: "tool_result",
          title: "file read",
          label: "Tool result",
          content: "Finished reading lib/hooks.ts",
          step: 1,
        },
      });
      opts.onChunk("hello ");
      opts.onChunk("world");
      finishStream = opts.onDone;
    });

    await act(async () => {
      root.render(<Harness />);
      await Promise.resolve();
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
      await Promise.resolve();
    });
    container.remove();
  });

  it("retains compact activities while keeping only the final answer live", async () => {
    const sessionMetadata = DEFAULT_SESSION_METADATA;

    let sendPromise: Promise<void> | undefined;
    await act(async () => {
      sendPromise = latestHook!.sendMessage("Stream a reply", sessionMetadata);
      await Promise.resolve();
    });

    expect(apiMocks.streamChat).toHaveBeenCalledTimes(1);
    expect(apiMocks.getConversation).toHaveBeenCalledWith("conv-1", "main");
    expect(latestHook!.activeConversationId).toBe("conv-1");
    expect(latestHook!.messages).toContainEqual(expect.objectContaining({
      content: "Looking around",
      streaming: false,
      kind: "thought",
    }));
    expect(latestHook!.messages).toContainEqual(expect.objectContaining({
      content: "Reading frontend/lib/hooks.ts",
      streaming: false,
      kind: "status",
    }));
    expect(latestHook!.messages).toContainEqual(expect.objectContaining({
      content: "Finished reading lib/hooks.ts",
      streaming: false,
      kind: "tool_result",
    }));
    expect(latestHook!.messages.filter((message) => message.streaming)).toEqual([
      expect.objectContaining({
        role: "assistant",
        content: "hello world",
      }),
    ]);
    expect(latestHook!.agentStatus).toMatchObject({
      title: "Using a tool",
      content: "Loaded 1200 bytes from frontend/lib/hooks.ts",
      tone: "info",
      runId: "run-1",
      active: true,
    });

    await act(async () => {
      finishStream?.();
      await sendPromise;
    });

    expect(apiMocks.getConversation).toHaveBeenCalledWith("conv-1", "main");
  });

  it("shows the active tool as a wrench row during streaming", async () => {
    apiMocks.streamChat.mockImplementation((opts: {
      onMetadata?: (data: { conversation_id: string; run_id: string; model?: string }) => void;
      onAgentStream?: (update: { kind: string; run_id: string; step: number; tool?: string; ok?: boolean; delta?: string; content?: string }) => void;
      onEvent?: (event: { id: string; run_id: string | null; event_type: string }) => void;
      onChunk: (chunk: string) => void;
      onDone: () => void;
    }) => {
      opts.onMetadata?.({ conversation_id: "conv-1", run_id: "run-2", model: "anthropic/claude-sonnet-4-5" });
      opts.onAgentStream?.({ kind: "thought_delta", run_id: "run-2", step: 1, delta: "Use tool file_read" });
      opts.onAgentStream?.({ kind: "text_done", run_id: "run-2", step: 1, content: "Use tool file_read" });
      opts.onAgentStream?.({ kind: "summary_done", run_id: "run-2", step: 1, content: "Use tool file_read" });
      opts.onAgentStream?.({ kind: "thought_done", run_id: "run-2", step: 1, content: "Use tool file_read" });
      opts.onAgentStream?.({ kind: "tool_started", run_id: "run-2", step: 1, tool: "file_read", content: "Reading lib/hooks.ts" });
      opts.onEvent?.({ id: "event-2", run_id: "run-2", event_type: "agent.tool.called" });
      finishStream = opts.onDone;
    });

    const sessionMetadata = DEFAULT_SESSION_METADATA;

    let sendPromise: Promise<void> | undefined;
    await act(async () => {
      sendPromise = latestHook!.sendMessage("Stream a reply", sessionMetadata);
      await Promise.resolve();
    });

    expect(latestHook!.messages.some((message) => message.id.startsWith("live-agent:"))).toBe(false);
    expect(latestHook!.messages.some((message) => message.id === "event:event-2")).toBe(false);
    expect(latestHook!.messages).toContainEqual(expect.objectContaining({
      content: "Use tool file_read",
      kind: "thought",
      title: "Thought",
    }));
    expect(latestHook!.messages).toContainEqual(expect.objectContaining({
      content: "Reading lib/hooks.ts",
      streaming: true,
      kind: "tool_call",
      title: "file read",
    }));
    expect(latestHook!.messages.at(-1)).toMatchObject({
      role: "assistant",
      content: "Reading lib/hooks.ts",
      streaming: true,
      kind: "tool_call",
      title: "file read",
    });
    expect(latestHook!.agentStatus).toMatchObject({
      title: "Using a tool",
      tone: "info",
      runId: "run-2",
      active: true,
    });

    await act(async () => {
      finishStream?.();
      await sendPromise;
    });
  });

  it("keeps the assistant text visible when a tool starts before that text is committed", async () => {
    apiMocks.streamChat.mockImplementation((opts: {
      onMetadata?: (data: { conversation_id: string; run_id: string; model?: string }) => void;
      onAgentStream?: (update: { kind: string; run_id: string; step: number; tool?: string; ok?: boolean; delta?: string; content?: string }) => void;
      onEvent?: (event: { id: string; run_id: string | null; event_type: string; payload_json?: Record<string, unknown>; created_at?: string; sequence?: number }) => void;
      onDone: () => void;
    }) => {
      opts.onMetadata?.({ conversation_id: "conv-1", run_id: "run-2b", model: "anthropic/claude-sonnet-4-5" });
      opts.onAgentStream?.({ kind: "summary_done", run_id: "run-2b", step: 1, content: "Inspecting the hooks flow" });
      opts.onAgentStream?.({ kind: "tool_started", run_id: "run-2b", step: 1, tool: "file_read", content: "Reading lib/hooks.ts" });
      opts.onEvent?.({
        id: "event-2b",
        run_id: "run-2b",
        event_type: "chat.timeline.item.committed",
        sequence: 1,
        created_at: "2026-05-01T00:00:02Z",
        payload_json: {
          item_id: "status:run-2b:1",
          role: "assistant",
          kind: "status",
          content: "Inspecting the hooks flow",
          step: 1,
        },
      });
      finishStream = opts.onDone;
    });

    const sessionMetadata = DEFAULT_SESSION_METADATA;

    let sendPromise: Promise<void> | undefined;
    await act(async () => {
      sendPromise = latestHook!.sendMessage("Stream a reply", sessionMetadata);
      await Promise.resolve();
    });

    expect(latestHook!.messages.filter((message) => message.role === "assistant")).toEqual([
      expect.objectContaining({
        content: "Inspecting the hooks flow",
        streaming: false,
        kind: "status",
      }),
      expect.objectContaining({
        content: "Reading lib/hooks.ts",
        streaming: true,
        kind: "tool_call",
        title: "file read",
      }),
    ]);

    await act(async () => {
      finishStream?.();
      await sendPromise;
    });
  });

  it("preserves a prior local assistant message when a tool takes over before commit", async () => {
    apiMocks.streamChat.mockImplementation((opts: {
      onMetadata?: (data: { conversation_id: string; run_id: string; model?: string }) => void;
      onAgentStream?: (update: { kind: string; run_id: string; step: number; tool?: string; ok?: boolean; delta?: string; content?: string }) => void;
      onDone: () => void;
    }) => {
      opts.onMetadata?.({ conversation_id: "conv-1", run_id: "run-4", model: "anthropic/claude-sonnet-4-5" });
      opts.onAgentStream?.({ kind: "summary_done", run_id: "run-4", step: 1, content: "Planning the implementation" });
      opts.onAgentStream?.({ kind: "tool_started", run_id: "run-4", step: 1, tool: "file_read", content: "Reading app/page.tsx" });
      opts.onAgentStream?.({ kind: "summary_done", run_id: "run-4", step: 2, content: "Preparing the patch" });
      finishStream = opts.onDone;
    });

    const sessionMetadata = DEFAULT_SESSION_METADATA;

    let sendPromise: Promise<void> | undefined;
    await act(async () => {
      sendPromise = latestHook!.sendMessage("Stream a reply", sessionMetadata);
      await Promise.resolve();
    });

    expect(latestHook!.messages.filter((message) => message.role === "assistant")).toEqual([
      expect.objectContaining({
        content: "Planning the implementation",
        streaming: false,
        kind: "status",
      }),
      expect.objectContaining({
        content: "Reading app/page.tsx",
        streaming: true,
        kind: "tool_call",
      }),
      expect.objectContaining({
        content: "Preparing the patch",
        streaming: false,
        kind: "status",
      }),
    ]);

    await act(async () => {
      finishStream?.();
      await sendPromise;
    });
  });

  it("removes the stale live final row once the committed final message is loaded", async () => {
    let detailCallCount = 0;
    let eventsCallCount = 0;
    apiMocks.getConversation.mockImplementation(async (conversationId: string) => {
      detailCallCount += 1;
      return {
        ...buildConversationDetail(conversationId),
        latest_run_status: detailCallCount === 1 ? "running" : "completed",
      };
    });
    apiMocks.getConversationEvents.mockImplementation(async () => {
      eventsCallCount += 1;
      if (eventsCallCount === 1) {
        return {
          events: [],
          artifacts: [],
          timeline_items: [],
          active_run: {
            run_id: "run-final",
            status: "running",
            latest_sequence: 1,
            live_item: null,
          },
        };
      }
      return {
        events: [],
        artifacts: [],
        timeline_items: [
          {
            item_id: "assistant:run-final:final-1",
            run_id: "run-final",
            sequence: 1,
            role: "assistant",
            kind: "message",
            content: "All done",
            created_at: "2026-05-01T00:00:03Z",
          },
        ],
        active_run: null,
      };
    });
    apiMocks.streamChat.mockImplementation((opts: {
      onMetadata?: (data: { conversation_id: string; run_id: string; model?: string }) => void;
      onChunk: (chunk: string) => void;
      onDone: () => void;
    }) => {
      opts.onMetadata?.({ conversation_id: "conv-1", run_id: "run-final", model: "anthropic/claude-sonnet-4-5" });
      opts.onChunk("All done");
      finishStream = opts.onDone;
    });

    const sessionMetadata = DEFAULT_SESSION_METADATA;

    let sendPromise: Promise<void> | undefined;
    await act(async () => {
      sendPromise = latestHook!.sendMessage("Stream a reply", sessionMetadata);
      await Promise.resolve();
    });

    expect(latestHook!.messages.filter((message) => message.streaming)).toEqual([
      expect.objectContaining({
        role: "assistant",
        content: "All done",
      }),
    ]);

    await act(async () => {
      finishStream?.();
      await sendPromise;
    });

    expect(latestHook!.messages.filter((message) => message.role === "assistant")).toEqual([
      expect.objectContaining({
        content: "All done",
        streaming: false,
        kind: "message",
      }),
    ]);
    expect(latestHook!.messages.some((message) => message.streaming)).toBe(false);
  });

  it("keeps active-run event timeline rows out of history until the run completes", async () => {
    chatHistoryMocks.mapConversationMessages.mockReturnValue([
      {
        id: "persisted-user-1",
        role: "user",
        content: "Earlier request",
      },
      {
        id: "event:active-run-plan",
        runId: "run-3",
        role: "assistant",
        kind: "plan",
        content: "Plan ready",
      },
      {
        id: "toolrow:run-3:1:file_read:abc",
        runId: "run-3",
        role: "assistant",
        kind: "tool_call",
        content: "Reading files",
      },
    ]);
    apiMocks.getConversation.mockImplementation(async (conversationId: string) => ({
      ...buildConversationDetail(conversationId),
      latest_run_status: "running",
    }));
    apiMocks.streamChat.mockImplementation((opts: {
      onMetadata?: (data: { conversation_id: string; run_id: string; model?: string }) => void;
      onChunk: (chunk: string) => void;
      onDone: () => void;
    }) => {
      opts.onMetadata?.({ conversation_id: "conv-1", run_id: "run-3", model: "anthropic/claude-sonnet-4-5" });
      opts.onChunk("working");
      finishStream = opts.onDone;
    });

    const sessionMetadata = DEFAULT_SESSION_METADATA;

    let sendPromise: Promise<void> | undefined;
    await act(async () => {
      sendPromise = latestHook!.sendMessage("Stream a reply", sessionMetadata);
      await Promise.resolve();
    });

    expect(latestHook!.messages.some((message) => message.id === "persisted-user-1")).toBe(true);
    expect(latestHook!.messages.some((message) => message.id === "event:active-run-plan")).toBe(false);
    expect(latestHook!.messages.some((message) => message.id === "toolrow:run-3:1:file_read:abc")).toBe(false);
    expect(latestHook!.messages.filter((message) => message.streaming)).toEqual([
      expect.objectContaining({
        role: "assistant",
        content: "working",
      }),
    ]);
    expect(latestHook!.messages.filter((message) => message.role === "assistant")).toEqual([
      expect.objectContaining({
        content: "working",
        streaming: true,
      }),
    ]);

    await act(async () => {
      finishStream?.();
      await sendPromise;
    });
  });

  it("keeps the live assistant message visible while the initial conversation load is still pending", async () => {
    const pendingDetail = deferred<ReturnType<typeof buildConversationDetail>>();
    const pendingEvents = deferred<ReturnType<typeof buildConversationEventsResponse>>();
    apiMocks.getConversation.mockImplementationOnce(() => pendingDetail.promise);
    apiMocks.getConversationEvents.mockImplementationOnce(() => pendingEvents.promise);
    apiMocks.streamChat.mockImplementation((opts: {
      onMetadata?: (data: { conversation_id: string; run_id: string; model?: string }) => void;
      onChunk: (chunk: string) => void;
      onDone: () => void;
    }) => {
      opts.onMetadata?.({ conversation_id: "conv-1", run_id: "run-pending", model: "anthropic/claude-sonnet-4-5" });
      opts.onChunk("still visible");
      finishStream = opts.onDone;
    });

    const sessionMetadata = DEFAULT_SESSION_METADATA;

    let sendPromise: Promise<void> | undefined;
    await act(async () => {
      sendPromise = latestHook!.sendMessage("Stream a reply", sessionMetadata);
      await Promise.resolve();
    });

    expect(latestHook!.loadingConversation).toBe(false);
    expect(latestHook!.messages.filter((message) => message.streaming)).toEqual([
      expect.objectContaining({
        role: "assistant",
        content: "still visible",
      }),
    ]);

    pendingDetail.resolve(buildConversationDetail("conv-1"));
    pendingEvents.resolve(buildConversationEventsResponse("conv-1"));

    await act(async () => {
      await Promise.resolve();
      finishStream?.();
      await sendPromise;
    });
  });

  it("polls the active conversation while the latest run is still running", async () => {
    vi.useFakeTimers();

    apiMocks.listConversations.mockResolvedValue([
      {
        id: "conv-1",
        title: "Conversation",
        mode_hint: "single",
        session_metadata: buildConversationDetail("conv-1").session_metadata,
        workspace_path: null,
        created_at: "2026-05-01T00:00:00Z",
        updated_at: "2026-05-01T00:00:00Z",
        archived_at: null,
        last_message_preview: null,
        latest_run_status: "running",
      },
    ]);

    await act(async () => {
      root.unmount();
      await Promise.resolve();
    });

    root = createRoot(container);
    await act(async () => {
      root.render(<Harness />);
      await Promise.resolve();
    });

    expect(apiMocks.getConversation).toHaveBeenCalledWith("conv-1", "main");
    const initialGetConversationCalls = apiMocks.getConversation.mock.calls.length;
    const initialGetConversationEventsCalls = apiMocks.getConversationEvents.mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });

    expect(apiMocks.listConversations.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(apiMocks.getConversation.mock.calls.length).toBeGreaterThan(initialGetConversationCalls);
    expect(apiMocks.getConversationEvents.mock.calls.length).toBeGreaterThan(initialGetConversationEventsCalls);

    vi.useRealTimers();
  });
});
