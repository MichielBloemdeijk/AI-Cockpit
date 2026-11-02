import { describe, expect, it } from "vitest";

import type { CouncilResponse, TimelineItemView } from "./api";
import { buildCommittedConversationMessages, buildTimelineMessageFromEvent } from "./conversation-feed";
import { buildConversationDetail } from "./test-fixtures";

const at = "2026-10-29T12:00:00Z";
const usage = { prompt_tokens: 20, completion_tokens: 10, total_tokens: 30, cost: 0.004 };
const council: CouncilResponse = {
  run_id: "r1",
  model_responses: [
    { model: "m1", content: "one", usage: { total_tokens: 15, cost: 0.002 } },
    { model: "m2", content: "two", usage: { total_tokens: 15, cost: 0.002 } },
  ],
  synthesized: "synthesis",
  synthesizer_model: "m3",
  total_usage: usage,
};

function item(overrides: Partial<TimelineItemView>): TimelineItemView {
  return {
    item_id: "assistant:a1", run_id: "r1", sequence: 3, role: "assistant", kind: "message",
    content: "synthesis", created_at: at, badges: [], sections: [], ...overrides,
  };
}

describe("timeline feed", () => {
  it("shows a reloaded council answer with its model panel and usage", () => {
    const [user, answer] = buildCommittedConversationMessages(buildConversationDetail("c1"), [], [], [
      item({ item_id: "user:u1", sequence: 1, role: "user", content: "question" }),
      item({ usage, council }),
    ]);
    expect(user.councilData).toBeUndefined();
    expect(answer.councilData).toEqual(council);
    expect(answer.usage).toEqual(usage);
  });

  it("keeps council data and usage on a streamed committed item", () => {
    const message = buildTimelineMessageFromEvent({
      id: "e3", conversation_id: "c1", run_id: "r1", sequence: 3, event_type: "chat.timeline.item.committed",
      actor_kind: "synthesizer", branch_key: "main", parent_event_id: null, schema_version: 1, created_at: at,
      payload_json: { item_id: "assistant:a1", role: "assistant", kind: "message", content: "synthesis", usage, council },
    } as Parameters<typeof buildTimelineMessageFromEvent>[0]);
    expect(message?.councilData).toEqual(council);
    expect(message?.usage).toEqual(usage);
  });
});
