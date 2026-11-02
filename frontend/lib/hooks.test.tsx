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
  checkAuth: vi.fn(async () => true),
  createConversation: vi.fn(),
  createConversationMessage: vi.fn(),
  createTask: vi.fn(),
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

vi.mock("./api/auth", () => apiMocks);
vi.mock("./api/conversations", () => apiMocks);
vi.mock("./api/jarvis", () => apiMocks);
vi.mock("./api/knowledge", () => apiMocks);
vi.mock("./api/push", () => apiMocks);
vi.mock("./api/tasks", () => apiMocks);
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
    apiMocks.streamChat.mockImplementation((opts: {
      onMetadata?: (data: { conversation_id: string; run_id: string; model?: string }) => void;
      onEvent?: (event: { id: string; run_id: string | null; event_type: string; payload_json?: Record<string, unknown>; created_at?: string; sequence?: number }) => void;
      onChunk: (chunk: string) => void;
      onDone: () => void;
    }) => {
      opts.onMetadata?.({ conversation_id: "conv-1", run_id: "run-1", model: "anthropic/claude-sonnet-4-5" });
      opts.onEvent?.({
        id: "event-1",
        run_id: "run-1",
        event_type: "chat.timeline.item.committed",
        sequence: 1,
        created_at: "2026-05-01T00:00:01Z",
        payload_json: {
          item_id: "user:message-u1",
          role: "user",
          kind: "message",
          content: "Stream a reply",
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

  it("retains committed timeline rows while keeping only the final answer live", async () => {
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
      activityId: "user:message-u1",
      content: "Stream a reply",
      streaming: false,
    }));
    expect(latestHook!.messages.filter((message) => message.streaming)).toEqual([
      expect.objectContaining({
        role: "assistant",
        content: "hello world",
      }),
    ]);

    await act(async () => {
      finishStream?.();
      await sendPromise;
    });

    expect(apiMocks.getConversation).toHaveBeenCalledWith("conv-1", "main");
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
      }),
    ]);
    expect(latestHook!.messages.some((message) => message.streaming)).toBe(false);
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
