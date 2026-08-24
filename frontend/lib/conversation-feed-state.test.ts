import { describe, expect, it } from "vitest";

import {
  appendCommittedTimelineMessage,
  mergeConversationFeed,
} from "./conversation-feed-state";
import type { ChatMessage } from "./chat-state-types";

describe("conversation feed state", () => {
  it("updates a background task card in place", () => {
    const queued: ChatMessage = {
      id: "task:run-task-1",
      activityId: "task:run-task-1",
      runId: "run-task-1",
      role: "assistant",
      kind: "task",
      title: "Repository review",
      content: "Queued",
      status: "queued",
    };

    const next = appendCommittedTimelineMessage([queued], {
      ...queued,
      content: "Reading documentation",
      status: "running",
      sections: [{ title: "Updates", items: ["Reading documentation"] }],
    });

    expect(next).toHaveLength(1);
    expect(next[0]).toEqual(expect.objectContaining({
      activityId: "task:run-task-1",
      status: "running",
      content: "Reading documentation",
    }));
  });

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

  it("replaces a completed activity at its original position", () => {
    const currentMessages: ChatMessage[] = [
      {
        id: "live-local:tool:run-1:1:file_read",
        activityId: "tool:run-1:1:file_read",
        runId: "run-1",
        step: 1,
        role: "assistant",
        kind: "tool_call",
        content: "Reading lib/hooks.ts",
        streaming: true,
      },
      {
        id: "live-local:tool:run-1:2:file_read",
        activityId: "tool:run-1:2:file_read",
        runId: "run-1",
        step: 2,
        role: "assistant",
        kind: "tool_call",
        content: "Reading lib/api.ts",
        streaming: true,
      },
    ];

    const nextMessages = appendCommittedTimelineMessage(currentMessages, {
      id: "tool:run-1:1:file_read",
      activityId: "tool:run-1:1:file_read",
      runId: "run-1",
      step: 1,
      role: "assistant",
      kind: "tool_result",
      title: "file read",
      label: "Tool result",
      content: "Read lib/hooks.ts",
      streaming: false,
    });

    expect(nextMessages).toEqual([
      expect.objectContaining({
        id: "tool:run-1:1:file_read",
        content: "Read lib/hooks.ts",
        streaming: false,
      }),
      expect.objectContaining({
        id: "live-local:tool:run-1:2:file_read",
        content: "Reading lib/api.ts",
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
