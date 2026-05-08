import { describe, expect, it } from "vitest";

import {
  buildAgentStatusFromStreamUpdate,
  mergeAgentStatus,
  mergeLoadedMessages,
  upsertLiveAgentStreamMessages,
} from "./agent-stream-state";
import type { AgentStatus, ChatMessage } from "./chat-state-types";

describe("agent stream state helpers", () => {
  it("suppresses low-signal progress summaries", () => {
    const messages = upsertLiveAgentStreamMessages([], {
      kind: "progress",
      run_id: "run-1",
      step: 1,
      content: "Use tool file_read",
    }, null);

    expect(messages).toEqual([]);
    expect(buildAgentStatusFromStreamUpdate({
      kind: "progress",
      run_id: "run-1",
      step: 1,
      content: "Use tool file_read",
    })).toBeNull();
  });

  it("keeps a higher-priority status when merging", () => {
    const current: AgentStatus = {
      title: "Waiting for input",
      content: "Need approval",
      tone: "warning",
      runId: "run-1",
      active: false,
    };

    const next: AgentStatus = {
      title: "Using a tool",
      content: "Reading files",
      tone: "info",
      runId: "run-1",
      active: true,
    };

    expect(mergeAgentStatus(current, next)).toEqual(current);
  });

  it("retains streaming rows until a persisted assistant message exists", () => {
    const currentMessages: ChatMessage[] = [
      {
        id: "optimistic-user-1",
        role: "user",
        content: "hello",
        streaming: true,
      },
      {
        id: "optimistic-assistant-1",
        role: "assistant",
        content: "partial",
        streaming: true,
      },
    ];

    expect(mergeLoadedMessages(currentMessages, [{ id: "persisted-user", role: "user", content: "hello" }])).toEqual([
      { id: "persisted-user", role: "user", content: "hello" },
      {
        id: "optimistic-assistant-1",
        role: "assistant",
        content: "partial",
        streaming: true,
      },
    ]);

    expect(mergeLoadedMessages(currentMessages, [
      { id: "persisted-user", role: "user", content: "hello" },
      { id: "persisted-assistant", role: "assistant", content: "done" },
    ])).toEqual([
      { id: "persisted-user", role: "user", content: "hello" },
      { id: "persisted-assistant", role: "assistant", content: "done" },
    ]);
  });
});