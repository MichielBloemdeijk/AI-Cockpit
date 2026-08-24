import { describe, expect, it } from "vitest";
import { isVoiceSpeechEvent, isVoiceTerminalEvent } from "./jarvis-event-policy";

describe("Jarvis event policy", () => {
  it("treats interactive cancellation, failure, and restart interruption as terminal speech events", () => {
    for (const eventType of ["run.cancelled", "run.failed", "run.interrupted"]) {
      expect(isVoiceTerminalEvent(eventType)).toBe(true);
      expect(isVoiceSpeechEvent(eventType)).toBe(true);
    }
  });

  it("does not treat question or progress events as terminal", () => {
    expect(isVoiceTerminalEvent("agent.question.asked")).toBe(false);
    expect(isVoiceTerminalEvent("task.progress")).toBe(false);
  });
});
