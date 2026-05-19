import { describe, expect, it } from "vitest";

import {
  appendCommittedTimelineMessage,
  mergeConversationFeed,
  upsertLocalLiveMessage,
} from "./conversation-feed-state";
import type { ChatMessage } from "./chat-state-types";

describe("conversation feed state", () => {
  it("inserts committed timeline rows before the live row", () => {
    const currentMessages: ChatMessage[] = [
      {
        id: "persisted-user-1",
        role: "user",
        content: "build app",
      },
      {
        id: "live-local:run-1:tool:1:file_read",
        runId: "run-1",
        step: 1,
        role: "assistant",
        kind: "tool_call",
        title: "file read",
        content: "Reading lib/hooks.ts",
        streaming: true,
      },
    ];

    const nextMessages = appendCommittedTimelineMessage(currentMessages, {
      id: "assistant:run-1:summary-1",
      runId: "run-1",
      step: 1,
      role: "assistant",
      kind: "message",
      content: "Inspecting the hooks flow",
      streaming: false,
    });

    expect(nextMessages.map((message) => message.id)).toEqual([
      "persisted-user-1",
      "assistant:run-1:summary-1",
      "live-local:run-1:tool:1:file_read",
    ]);
  });

  it("preserves the prior live assistant message when a tool row takes over", () => {
    const currentMessages: ChatMessage[] = [
      {
        id: "live-local:run-1:summary:1",
        runId: "run-1",
        step: 1,
        role: "assistant",
        kind: "message",
        content: "Inspecting the hooks flow",
        streaming: true,
      },
    ];

    const nextMessages = upsertLocalLiveMessage(currentMessages, {
      id: "live-local:run-1:tool:1:file_read",
      runId: "run-1",
      step: 1,
      role: "assistant",
      kind: "tool_call",
      title: "file read",
      label: "Tool call",
      content: "Reading lib/hooks.ts",
      streaming: true,
    }, {
      preservePriorMessage: true,
    });

    expect(nextMessages).toEqual([
      expect.objectContaining({
        id: "pending-local:run-1:summary:1",
        content: "Inspecting the hooks flow",
        streaming: false,
      }),
      expect.objectContaining({
        id: "live-local:run-1:tool:1:file_read",
        content: "Reading lib/hooks.ts",
        streaming: true,
      }),
    ]);
  });

  it("keeps optimistic user input and one live row when merging loaded feed data", () => {
    const currentMessages: ChatMessage[] = [
      {
        id: "optimistic-user-1",
        role: "user",
        content: "build app",
      },
      {
        id: "assistant:old",
        role: "assistant",
        content: "Earlier reply",
      },
      {
        id: "live-local:run-1:chunk",
        runId: "run-1",
        role: "assistant",
        kind: "message",
        content: "Still working",
        streaming: true,
      },
    ];

    const merged = mergeConversationFeed(
      currentMessages,
      [
        {
          id: "assistant:committed-1",
          runId: "run-1",
          role: "assistant",
          kind: "message",
          content: "Committed reply",
        },
      ],
      null,
      "run-1",
    );

    expect(merged).toEqual([
      expect.objectContaining({ id: "assistant:committed-1" }),
      expect.objectContaining({ id: "optimistic-user-1" }),
      expect.objectContaining({ id: "live-local:run-1:chunk" }),
    ]);
  });
});