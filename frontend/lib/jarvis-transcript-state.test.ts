import { describe, expect, it } from "vitest";

import { mergeProvisionalTranscript } from "./jarvis-transcript-state";

describe("mergeProvisionalTranscript", () => {
  it("builds a live transcript from provider deltas", () => {
    const first = mergeProvisionalTranscript(null, { id: "item-1", delta: "Good " });
    const second = mergeProvisionalTranscript(first, { id: "item-1", delta: "morning" });

    expect(second).toEqual({ id: "item-1", text: "Good morning" });
  });

  it("does not regress when a slower durable transcript arrives", () => {
    const current = { id: "item-1", text: "Good morning" };

    expect(mergeProvisionalTranscript(current, {
      id: "item-1",
      transcript: "Good ",
    })).toBe(current);
  });

  it("allows a completed transcript to correct provisional text", () => {
    expect(mergeProvisionalTranscript(
      { id: "item-1", text: "Jarvis sand" },
      { id: "item-1", transcript: "Jarvis, send it.", replace: true },
    )).toEqual({ id: "item-1", text: "Jarvis, send it." });
  });

  it("starts fresh when the provider item changes", () => {
    expect(mergeProvisionalTranscript(
      { id: "item-1", text: "First turn" },
      { id: "item-2", delta: "Second" },
    )).toEqual({ id: "item-2", text: "Second" });
  });
});
