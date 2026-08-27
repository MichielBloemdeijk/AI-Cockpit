import { describe, expect, it } from "vitest";

import {
  EMPTY_JARVIS_LIVE_STREAMS,
  mergeJarvisDomainReplay,
  resetJarvisConversationStream,
  resetJarvisSessionStream,
} from "./jarvis-live-state";

describe("Jarvis independent domain streams", () => {
  it("sorts reordered events, rejects duplicates, and advances only that domain", () => {
    const first = mergeJarvisDomainReplay(EMPTY_JARVIS_LIVE_STREAMS, "session", [
      { id: "s2", session_id: "s", sequence: 2, event_type: "provider.ready", payload: null, created_at: "2026-08-27T12:00:02Z" },
      { id: "s1", session_id: "s", sequence: 1, event_type: "session.connecting", payload: null, created_at: "2026-08-27T12:00:01Z" },
      { id: "s1", session_id: "s", sequence: 1, event_type: "session.connecting", payload: null, created_at: "2026-08-27T12:00:01Z" },
    ]);

    expect(first.cursors).toEqual({ conversation: 0, task: 0, session: 2 });
    expect(first.activity.map((item) => item.key)).toEqual(["session:s1", "session:s2"]);
    expect(mergeJarvisDomainReplay(first, "session", [])).toBe(first);
  });

  it("merges activity by display time without inventing a global cursor", () => {
    const conversation = mergeJarvisDomainReplay(EMPTY_JARVIS_LIVE_STREAMS, "conversation", [{
      id: "c1", run_id: null, sequence: 1, branch_key: "main", parent_event_id: null,
      actor_kind: "user", event_type: "conversation.user_message.accepted", created_at: "2026-08-27T12:00:03Z",
      schema_version: 1, payload_json: { content: "hello" },
    }]);
    const task = mergeJarvisDomainReplay(conversation, "task", [{
      id: "t9", sequence: 9, task_id: "task", attempt_id: null, event_type: "task.completed",
      payload: { summary: "done" }, causality: null, created_at: "2026-08-27T12:00:02Z",
    }]);

    expect(task.cursors).toEqual({ conversation: 1, task: 9, session: 0 });
    expect(task.activity.map((item) => item.domain)).toEqual(["task", "conversation"]);
  });

  it("resets log-scoped streams while preserving global task activity", () => {
    const withTask = mergeJarvisDomainReplay(EMPTY_JARVIS_LIVE_STREAMS, "task", [{
      id: "t1", sequence: 1, task_id: "task", attempt_id: null, event_type: "task.queued",
      payload: null, causality: null, created_at: "2026-08-27T12:00:00Z",
    }]);
    const reset = resetJarvisConversationStream({
      ...withTask,
      cursors: { conversation: 4, task: 1, session: 3 },
    });
    expect(reset.cursors).toEqual({ conversation: 0, task: 1, session: 0 });
    expect(reset.activity).toHaveLength(1);
  });

  it("resets a replaced provider session without resetting log or task cursors", () => {
    const reset = resetJarvisSessionStream({
      cursors: { conversation: 4, task: 8, session: 12 },
      activity: [
        { key: "conversation:c", domain: "conversation", sequence: 4, eventType: "turn", createdAt: "2026-08-27T12:00:00Z", payload: null },
        { key: "session:s", domain: "session", sequence: 12, eventType: "ready", createdAt: "2026-08-27T12:00:01Z", payload: null },
      ],
    });
    expect(reset.cursors).toEqual({ conversation: 4, task: 8, session: 0 });
    expect(reset.activity.map((item) => item.domain)).toEqual(["conversation"]);
  });
});
