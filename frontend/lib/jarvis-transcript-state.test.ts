import { describe, expect, it } from "vitest";

import {
  mergeProvisionalTranscript,
  provisionalTranscriptIsPersisted,
} from "./jarvis-transcript-state";

describe("mergeProvisionalTranscript", () => {
  it("builds a live transcript from provider deltas", () => {
    const first = mergeProvisionalTranscript(null, { id: "item-1", delta: "Good " });
    const second = mergeProvisionalTranscript(first, { id: "item-1", delta: "morning" });

    expect(second).toEqual({ id: "item-1", text: "Good morning", afterMessageId: null });
  });

  it("does not regress when a slower durable transcript arrives", () => {
    const current = { id: "item-1", text: "Good morning", afterMessageId: "message-1" };

    expect(mergeProvisionalTranscript(current, {
      id: "item-1",
      transcript: "Good ",
    })).toBe(current);
  });

  it("allows a completed transcript to correct provisional text", () => {
    expect(mergeProvisionalTranscript(
      { id: "item-1", text: "Jarvis sand", afterMessageId: null },
      { id: "item-1", transcript: "Jarvis, send it.", replace: true },
    )).toEqual({ id: "item-1", text: "Jarvis, send it.", afterMessageId: null });
  });

  it("starts fresh when the provider item changes", () => {
    expect(mergeProvisionalTranscript(
      { id: "item-1", text: "First turn", afterMessageId: "message-1" },
      { id: "item-2", delta: "Second", afterMessageId: "message-2" },
    )).toEqual({ id: "item-2", text: "Second", afterMessageId: "message-2" });
  });

  it("preserves the durable-message anchor across updates for one provider item", () => {
    const first = mergeProvisionalTranscript(null, {
      id: "response-1",
      delta: "Reminder:",
      afterMessageId: "message-before-reminder",
    });
    const second = mergeProvisionalTranscript(first, {
      id: "response-1",
      delta: " stand up",
      afterMessageId: "newer-message-that-must-not-replace-the-anchor",
    });

    expect(second).toEqual({
      id: "response-1",
      text: "Reminder: stand up",
      afterMessageId: "message-before-reminder",
    });
  });
});

describe("provisionalTranscriptIsPersisted", () => {
  const messages = [
    { id: "old-assistant", role: "assistant", content: "Yes." },
    { id: "user-turn", role: "user", content: "Are you there?" },
    { id: "new-assistant", role: "assistant", content: "Yes." },
  ];

  it("hides a provisional bubble only when the same text was persisted after its anchor", () => {
    expect(provisionalTranscriptIsPersisted(messages, {
      id: "response-new",
      text: " Yes. ",
      afterMessageId: "user-turn",
    }, "assistant")).toBe(true);
  });

  it("does not hide a legitimate repeated reply when only the older copy is durable", () => {
    expect(provisionalTranscriptIsPersisted(messages.slice(0, 2), {
      id: "response-new",
      text: "Yes.",
      afterMessageId: "user-turn",
    }, "assistant")).toBe(false);
  });

  it("does not reconcile across roles or an unknown anchor", () => {
    expect(provisionalTranscriptIsPersisted(messages, {
      id: "user-item",
      text: "Yes.",
      afterMessageId: "user-turn",
    }, "user")).toBe(false);
    expect(provisionalTranscriptIsPersisted(messages, {
      id: "response-new",
      text: "Yes.",
      afterMessageId: "missing-message",
    }, "assistant")).toBe(false);
  });
});
