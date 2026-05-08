// @vitest-environment jsdom

import React from "react";
import { createRoot, Root } from "react-dom/client";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

vi.mock("./api", () => apiMocks);
vi.mock("./chat-history", () => ({
  mapConversationMessages: vi.fn(() => []),
  mapConversationEventMessages: vi.fn((events: Array<{ id: string; run_id: string | null; event_type: string }>) => events.map((event) => ({
    id: `event:${event.id}`,
    runId: event.run_id,
    role: "assistant",
    kind: "plan",
    title: event.event_type,
    content: event.event_type,
  }))),
}));

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

function conversationDetail(conversationId: string) {
  return {
    id: conversationId,
    title: "Conversation",
    mode_hint: "single",
    session_metadata: {
      mode: "single",
      single_model: "anthropic/claude-sonnet-4-5",
      council_models: ["anthropic/claude-sonnet-4-5"],
      synthesizer_model: "anthropic/claude-sonnet-4-5",
      tool_flags: { workspace_search: true, python_execution: true },
    },
    workspace_path: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    archived_at: null,
    last_message_preview: null,
    latest_run_status: "completed",
    active_branch_key: "main",
    branches: [],
    workspace: null,
    messages: [],
  };
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
    apiMocks.getConversation.mockImplementation(async (conversationId: string) => conversationDetail(conversationId));
    apiMocks.getConversationEvents.mockResolvedValue({ events: [], artifacts: [] });
    apiMocks.streamChat.mockImplementation((opts: {
      onMetadata?: (data: { conversation_id: string; run_id: string; model?: string }) => void;
      onAgentStream?: (update: { kind: string; run_id: string; step: number; delta?: string; content?: string }) => void;
      onEvent?: (event: { id: string; run_id: string | null; event_type: string }) => void;
      onChunk: (chunk: string) => void;
      onDone: () => void;
    }) => {
      opts.onMetadata?.({ conversation_id: "conv-1", run_id: "run-1", model: "anthropic/claude-sonnet-4-5" });
      opts.onAgentStream?.({ kind: "thought_delta", run_id: "run-1", step: 1, delta: "Looking " });
      opts.onAgentStream?.({ kind: "thought_delta", run_id: "run-1", step: 1, delta: "around" });
      opts.onAgentStream?.({ kind: "progress", run_id: "run-1", step: 1, content: "Looking around" });
      opts.onAgentStream?.({ kind: "thought_done", run_id: "run-1", step: 1, content: "Looking around" });
      opts.onEvent?.({ id: "event-1", run_id: "run-1", event_type: "agent.plan.created" });
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

  it("keeps the optimistic assistant stream visible until the stream finishes", async () => {
    const sessionMetadata = {
      mode: "single" as const,
      single_model: "anthropic/claude-sonnet-4-5",
      council_models: ["anthropic/claude-sonnet-4-5"],
      synthesizer_model: "anthropic/claude-sonnet-4-5",
      tool_flags: { workspace_search: true, python_execution: true },
    };

    let sendPromise: Promise<void> | undefined;
    await act(async () => {
      sendPromise = latestHook!.sendMessage("Stream a reply", sessionMetadata);
      await Promise.resolve();
    });

    expect(apiMocks.streamChat).toHaveBeenCalledTimes(1);
    expect(apiMocks.getConversation).toHaveBeenCalledWith("conv-1", "main");
    expect(latestHook!.activeConversationId).toBe("conv-1");
    expect(latestHook!.messages.some((message) => message.id === "live-agent:progress:run-1:1" && message.content === "Looking around")).toBe(true);
    expect(latestHook!.messages.some((message) => message.id === "live-agent:thought:run-1:1" && message.content === "Looking around")).toBe(true);
    expect(latestHook!.agentStatus).toMatchObject({
      title: "Step 1",
      content: "Looking around",
      tone: "info",
      runId: "run-1",
      active: true,
    });
    expect(latestHook!.messages.some((message) => message.id === "event:event-1" && message.content === "agent.plan.created")).toBe(true);
    expect(latestHook!.messages.some((message) => message.role === "assistant" && message.content === "hello world")).toBe(true);

    await act(async () => {
      finishStream?.();
      await sendPromise;
    });

    expect(apiMocks.getConversation).toHaveBeenCalledWith("conv-1", "main");
  });

  it("suppresses low-signal tool-intent live thought and progress rows", async () => {
    apiMocks.streamChat.mockImplementation((opts: {
      onMetadata?: (data: { conversation_id: string; run_id: string; model?: string }) => void;
      onAgentStream?: (update: { kind: string; run_id: string; step: number; delta?: string; content?: string }) => void;
      onEvent?: (event: { id: string; run_id: string | null; event_type: string }) => void;
      onChunk: (chunk: string) => void;
      onDone: () => void;
    }) => {
      opts.onMetadata?.({ conversation_id: "conv-1", run_id: "run-2", model: "anthropic/claude-sonnet-4-5" });
      opts.onAgentStream?.({ kind: "thought_delta", run_id: "run-2", step: 1, delta: "Use tool file_read" });
      opts.onAgentStream?.({ kind: "progress", run_id: "run-2", step: 1, content: "Use tool file_read" });
      opts.onAgentStream?.({ kind: "thought_done", run_id: "run-2", step: 1, content: "Use tool file_read" });
      opts.onEvent?.({ id: "event-2", run_id: "run-2", event_type: "agent.tool.called" });
      finishStream = opts.onDone;
    });

    const sessionMetadata = {
      mode: "single" as const,
      single_model: "anthropic/claude-sonnet-4-5",
      council_models: ["anthropic/claude-sonnet-4-5"],
      synthesizer_model: "anthropic/claude-sonnet-4-5",
      tool_flags: { workspace_search: true, python_execution: true },
    };

    let sendPromise: Promise<void> | undefined;
    await act(async () => {
      sendPromise = latestHook!.sendMessage("Stream a reply", sessionMetadata);
      await Promise.resolve();
    });

    expect(latestHook!.messages.some((message) => message.id === "live-agent:progress:run-2:1")).toBe(false);
    expect(latestHook!.messages.some((message) => message.id === "live-agent:thought:run-2:1")).toBe(false);
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

  it("polls the active conversation while the latest run is still running", async () => {
    vi.useFakeTimers();

    apiMocks.listConversations.mockResolvedValue([
      {
        id: "conv-1",
        title: "Conversation",
        mode_hint: "single",
        session_metadata: conversationDetail("conv-1").session_metadata,
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
