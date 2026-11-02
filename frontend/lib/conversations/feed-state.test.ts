import { describe, expect, it } from "vitest";

import {
  appendCommittedTimelineMessage,
  mergeConversationFeed,
} from "./feed-state";
import type { ChatMessage } from "./state-types";

describe("conversation feed state", () => {
  it("updates a re-recorded message in place", () => {
    const heard: ChatMessage = {
      id: "assistant:m1",
      activityId: "assistant:m1",
      runId: "run-1",
      role: "assistant",
      content: "I heard: tomorow",
    };

    const next = appendCommittedTimelineMessage([heard], { ...heard, content: "I heard: tomorrow" });

    expect(next).toEqual([expect.objectContaining({ activityId: "assistant:m1", content: "I heard: tomorrow" })]);
  });

  it("inserts committed timeline rows before the live row", () => {
    const currentMessages: ChatMessage[] = [
      {
        id: "persisted-user-1",
        role: "user",
        content: "build app",
      },
      {
        id: "live-local:run-1:chunk",
        activityId: "assistant:run-1:final",
        runId: "run-1",
        role: "assistant",
        content: "Still",
        streaming: true,
      },
    ];

    const nextMessages = appendCommittedTimelineMessage(currentMessages, {
      id: "user:run-1:u2",
      activityId: "user:run-1:u2",
      runId: "run-1",
      role: "user",
      content: "and also this",
    });

    expect(nextMessages.map((message) => message.id)).toEqual([
      "persisted-user-1",
      "user:run-1:u2",
      "live-local:run-1:chunk",
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
      "run-1",
    );

    expect(merged).toEqual([
      expect.objectContaining({ id: "assistant:committed-1" }),
      expect.objectContaining({ id: "optimistic-user-1" }),
      expect.objectContaining({ id: "live-local:run-1:chunk" }),
    ]);
  });
});
