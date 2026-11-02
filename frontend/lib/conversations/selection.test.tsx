// @vitest-environment jsdom

import React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConversationDetail } from "../api/conversations";
import {
  buildConversationDetail,
  buildConversationEventsResponse,
  buildConversationSummary,
} from "../test-fixtures";

const apiMocks = vi.hoisted(() => ({
  checkAuth: vi.fn(async () => true),
  createConversation: vi.fn(),
  createConversationMessage: vi.fn(),
  getConversation: vi.fn(),
  getConversationEvents: vi.fn(),
  listConversations: vi.fn(),
  resendConversationBranch: vi.fn(),
  streamChat: vi.fn(),
}));

vi.mock("../api/auth", () => apiMocks);
vi.mock("../api/conversations", () => apiMocks);
vi.mock("../api/jarvis", () => apiMocks);
vi.mock("../api/knowledge", () => apiMocks);
vi.mock("../api/push", () => apiMocks);
vi.mock("../api/tasks", () => apiMocks);
vi.mock("./history", () => ({
  mapConversationMessages: vi.fn(() => []),
  mapConversationEventMessages: vi.fn(() => []),
}));

import { useChat } from "../hooks";

let latestHook: ReturnType<typeof useChat> | null = null;

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function Harness() {
  const hook = useChat();
  React.useEffect(() => {
    latestHook = hook;
  }, [hook]);
  return null;
}

type Pending = { resolve: (detail: ConversationDetail) => void; reject: (error: Error) => void };

describe("conversation selection ownership", () => {
  let container: HTMLDivElement;
  let root: Root;
  let pending: Map<string, Pending>;

  async function settle() {
    await act(async () => {
      for (let index = 0; index < 5; index++) await Promise.resolve();
    });
  }

  async function finishLoad(conversationId: string, branchKey = "main", title = `${conversationId}:${branchKey}`) {
    const load = pending.get(`${conversationId}:${branchKey}`);
    if (!load) throw new Error(`no pending load for ${conversationId}:${branchKey}`);
    pending.delete(`${conversationId}:${branchKey}`);
    load.resolve(buildConversationDetail(conversationId, { title, active_branch_key: branchKey }));
    await settle();
  }

  beforeEach(async () => {
    latestHook = null;
    vi.clearAllMocks();
    pending = new Map();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    apiMocks.listConversations.mockResolvedValue([
      buildConversationSummary("conv-a"),
      buildConversationSummary("conv-b"),
    ]);
    apiMocks.getConversation.mockImplementation((conversationId: string, branchKey: string) => (
      new Promise<ConversationDetail>((resolve, reject) => {
        pending.set(`${conversationId}:${branchKey}`, { resolve, reject });
      })
    ));
    apiMocks.getConversationEvents.mockImplementation(async (conversationId: string) => (
      buildConversationEventsResponse(conversationId)
    ));
    await act(async () => {
      root.render(<Harness />);
      await Promise.resolve();
    });
    await settle();
    await finishLoad("conv-a");
    expect(latestHook!.activeConversation?.id).toBe("conv-a");
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
      await Promise.resolve();
    });
    container.remove();
  });

  it.each([
    ["newer first", ["conv-b", "conv-a"]],
    ["older first", ["conv-a", "conv-b"]],
  ])("shows the last selected conversation when loads resolve %s", async (_label, order) => {
    await act(async () => { latestHook!.selectConversation("conv-b"); });
    await act(async () => { latestHook!.selectConversation("conv-a"); });
    await act(async () => { latestHook!.selectConversation("conv-b"); });
    await settle();
    // conv-a's load is the superseded one; it must not win whichever order they finish in.
    for (const conversationId of order) {
      if (pending.has(`${conversationId}:main`)) await finishLoad(conversationId, "main", `${conversationId} late`);
    }
    expect(latestHook!.activeConversationId).toBe("conv-b");
    expect(latestHook!.activeConversation?.id).toBe("conv-b");
    expect(latestHook!.loadingConversation).toBe(false);
  });

  it("ignores an older branch load that resolves after the newer one", async () => {
    await act(async () => { latestHook!.selectBranch("branch-2"); });
    await settle();
    const olderBranch = pending.get("conv-a:branch-2")!;
    pending.delete("conv-a:branch-2");
    await act(async () => { latestHook!.selectBranch("main"); });
    await settle();
    await finishLoad("conv-a", "main", "main view");
    olderBranch.resolve(buildConversationDetail("conv-a", { title: "stale branch", active_branch_key: "branch-2" }));
    await settle();
    expect(latestHook!.activeBranchKey).toBe("main");
    expect(latestHook!.activeConversation?.title).toBe("main view");
  });

  it("discards a load, and its error, after the selection is cleared", async () => {
    await act(async () => { latestHook!.selectConversation("conv-b"); });
    await settle();
    expect(latestHook!.loadingConversation).toBe(true);
    await act(async () => { latestHook!.clearMessages(); });
    pending.get("conv-b:main")!.reject(new Error("late failure"));
    await settle();
    expect(latestHook!.activeConversationId).toBeNull();
    expect(latestHook!.activeConversation).toBeNull();
    expect(latestHook!.loadingConversation).toBe(false);
    expect(latestHook!.actionError).toBeNull();
  });

  it("keeps a stream from writing into, or reselecting away from, a conversation chosen mid-stream", async () => {
    let callbacks: {
      onMetadata: (data: { conversation_id: string; run_id: string }) => void;
      onChunk: (chunk: string) => void;
      onDone: () => void;
    } | null = null;
    apiMocks.streamChat.mockImplementation((options: NonNullable<typeof callbacks>) => { callbacks = options; });

    let sending: Promise<void> | undefined;
    await act(async () => {
      sending = latestHook!.sendMessage("hello", latestHook!.activeConversation!.session_metadata!);
      await Promise.resolve();
    });
    await act(async () => { callbacks!.onMetadata({ conversation_id: "conv-a", run_id: "run-a" }); });
    await act(async () => { latestHook!.selectConversation("conv-b"); });
    await settle();
    await finishLoad("conv-b");
    const conversationBMessages = latestHook!.messages;

    await act(async () => {
      callbacks!.onMetadata({ conversation_id: "conv-a", run_id: "run-a" });
      callbacks!.onChunk("streamed into A");
      callbacks!.onDone();
      await sending;
    });
    await settle();

    expect(latestHook!.activeConversationId).toBe("conv-b");
    expect(latestHook!.activeConversation?.id).toBe("conv-b");
    expect(latestHook!.messages).toEqual(conversationBMessages);
    expect(latestHook!.messages.some((message) => message.content === "streamed into A")).toBe(false);
  });

  it("keeps a send on a branch on that branch", async () => {
    await act(async () => { latestHook!.selectBranch("branch-2"); });
    await settle();
    await finishLoad("conv-a", "branch-2");
    let onMetadata: ((data: { conversation_id: string; run_id: string }) => void) | null = null;
    apiMocks.streamChat.mockImplementation((options: { onMetadata: typeof onMetadata }) => { onMetadata = options.onMetadata; });
    await act(async () => {
      void latestHook!.sendMessage("on the branch", latestHook!.activeConversation!.session_metadata!);
      await Promise.resolve();
    });
    await act(async () => { onMetadata!({ conversation_id: "conv-a", run_id: "run-branch" }); });
    expect(latestHook!.activeBranchKey).toBe("branch-2");
    expect(apiMocks.streamChat).toHaveBeenCalledWith(expect.objectContaining({ branchKey: "branch-2" }));
  });
});
