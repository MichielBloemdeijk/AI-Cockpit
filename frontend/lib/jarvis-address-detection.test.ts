import { describe, expect, it } from "vitest";

import { classifyJarvisAddress } from "./jarvis-address-detection";

const base = {
  wakeScore: 0.82,
  speechStartedMsBeforeWake: 450,
  speechContinuedMsAfterWake: 420,
  followUpWindowOpen: false,
};

describe("classifyJarvisAddress", () => {
  it.each([
    "Jarvis, remind me at six",
    "Hey Jarvis what is the current task doing?",
    "jarvis! sleep",
  ])("accepts a direct address: %s", (transcript) => {
    expect(classifyJarvisAddress({ ...base, transcript })).toMatchObject({
      accepted: true,
      reason: "direct_address",
    });
  });

  it.each([
    "I was talking about Jarvis yesterday",
    "The character called Jarvis is in the movie",
    "She mentioned Jarvis during lunch",
  ])("rejects an incidental mention: %s", (transcript) => {
    expect(classifyJarvisAddress({ ...base, transcript })).toMatchObject({
      accepted: false,
      reason: "incidental_mention",
    });
  });

  it("keeps the verifier permissive when transcription misses the wake word", () => {
    expect(classifyJarvisAddress({
      ...base,
      transcript: "continue with the report",
      wakeScore: 0.2,
      speechContinuedMsAfterWake: 80,
    })).toMatchObject({ accepted: true, reason: "wake_model_candidate" });
    expect(classifyJarvisAddress({
      ...base,
      transcript: "Jarvis",
      wakeScore: 0.19,
      speechContinuedMsAfterWake: 40,
    })).toMatchObject({ accepted: true, reason: "direct_address" });
    expect(classifyJarvisAddress({
      ...base,
      transcript: "continue with the report",
      followUpWindowOpen: true,
    }).reason).toBe("follow_up_window");
  });
});
