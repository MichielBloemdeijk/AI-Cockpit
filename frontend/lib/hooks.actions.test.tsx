// @vitest-environment jsdom

import React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildConversationDetail,
  buildConversationEventsResponse,
  buildConversationSummary,
} from "./test-fixtures";

const apiMocks = vi.hoisted(() => ({
  archiveConversation: vi.fn(),
  checkAuth: vi.fn(async () => true),
  createConversation: vi.fn(),
  createConversationMessage: vi.fn(),
  executeConversationTool: vi.fn(),
  getConversation: vi.fn(),
  getConversationEvents: vi.fn(),
  listConversations: vi.fn(),
  resendConversationBranch: vi.fn(),
  streamChat: vi.fn(),
  unarchiveConversation: vi.fn(),
}));

vi.mock("./api", () => apiMocks);
vi.mock("./chat-history", () => ({
  mapConversationMessages: vi.fn(() => []),
  mapConversationEventMessages: vi.fn(() => []),
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

describe("useChat actions", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    latestHook = null;
    vi.clearAllMocks();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    apiMocks.listConversations.mockResolvedValue([]);
    apiMocks.getConversation.mockImplementation(async (conversationId: string) => buildConversationDetail(conversationId));
    apiMocks.getConversationEvents.mockResolvedValue(buildConversationEventsResponse("conv-1"));

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

  it("creates a conversation before running a tool when none is active", async () => {
    const sessionMetadata = buildConversationDetail("draft").session_metadata!;
    apiMocks.createConversation.mockResolvedValue({ conversation: { id: "conv-2" } });
    apiMocks.listConversations
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([
        buildConversationSummary("conv-2", { session_metadata: sessionMetadata }),
      ]);

    await act(async () => {
      await latestHook!.runTool({ tool: "workspace_search", value: "agent loop" }, sessionMetadata);
    });

    expect(apiMocks.createConversation).toHaveBeenCalledWith(undefined, "single", sessionMetadata);
    expect(apiMocks.executeConversationTool).toHaveBeenCalledWith("conv-2", {
      tool: "workspace_search",
      branch_key: "main",
      query: "agent loop",
      code: undefined,
    });
    expect(apiMocks.getConversation).toHaveBeenCalledWith("conv-2", "main");
    expect(latestHook!.activeConversationId).toBe("conv-2");
  });

  it("archives the active conversation and clears the selection", async () => {
    const sessionMetadata = buildConversationDetail("conv-1").session_metadata!;
    apiMocks.listConversations
      .mockResolvedValueOnce([
        buildConversationSummary("conv-1", { session_metadata: sessionMetadata }),
      ])
      .mockResolvedValueOnce([]);

    await act(async () => {
      root.unmount();
      await Promise.resolve();
    });

    root = createRoot(container);
    await act(async () => {
      root.render(<Harness />);
      await Promise.resolve();
    });

    expect(latestHook!.activeConversationId).toBe("conv-1");

    await act(async () => {
      await latestHook!.archiveActiveConversation();
    });

    expect(apiMocks.archiveConversation).toHaveBeenCalledWith("conv-1");
    expect(latestHook!.activeConversationId).toBeNull();
  });

  it("restores an archived conversation and keeps it selected", async () => {
    const archived = buildConversationSummary("conv-archived", {
      archived_at: "2026-05-02T00:00:00Z",
    });
    apiMocks.listConversations.mockResolvedValue([archived]);
    apiMocks.getConversation.mockResolvedValue(buildConversationDetail("conv-archived", {
      archived_at: "2026-05-02T00:00:00Z",
    }));

    await act(async () => {
      root.unmount();
      await Promise.resolve();
    });
    root = createRoot(container);
    await act(async () => {
      root.render(<Harness />);
      await Promise.resolve();
    });

    await act(async () => {
      await latestHook!.unarchiveActiveConversation();
    });

    expect(apiMocks.unarchiveConversation).toHaveBeenCalledWith("conv-archived");
    expect(latestHook!.activeConversationId).toBe("conv-archived");
    expect(apiMocks.getConversation).toHaveBeenCalledWith("conv-archived", "main");
  });

  it("switches to the returned branch after resend", async () => {
    const sessionMetadata = buildConversationDetail("conv-1").session_metadata!;
    apiMocks.listConversations.mockResolvedValue([
      buildConversationSummary("conv-1", { session_metadata: sessionMetadata }),
    ]);
    apiMocks.resendConversationBranch.mockResolvedValue({
      branch: { branch_key: "branch-2" },
    });

    await act(async () => {
      root.unmount();
      await Promise.resolve();
    });

    root = createRoot(container);
    await act(async () => {
      root.render(<Harness />);
      await Promise.resolve();
    });

    await act(async () => {
      await latestHook!.resendFromMessage("msg-1", "retry this");
    });

    expect(apiMocks.resendConversationBranch).toHaveBeenCalledWith("conv-1", {
      source_message_id: "msg-1",
      content: "retry this",
      parent_branch_key: "main",
    });
    expect(apiMocks.getConversation).toHaveBeenCalledWith("conv-1", "branch-2");
    expect(latestHook!.activeBranchKey).toBe("branch-2");
  });

  it("surfaces tool errors through actionError state", async () => {
    const sessionMetadata = buildConversationDetail("draft").session_metadata!;
    apiMocks.createConversation.mockResolvedValue({ conversation: { id: "conv-3" } });
    apiMocks.executeConversationTool.mockRejectedValue(new Error("Tool sandbox rejected the command"));

    await act(async () => {
      await latestHook!.runTool({ tool: "workspace_search", value: "agent loop" }, sessionMetadata);
    });

    expect(latestHook!.actionError).toMatchObject({
      scope: "tool",
      message: "Tool sandbox rejected the command",
    });
  });
});
