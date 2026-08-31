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

  it("rejects a weak or non-addressed wake and accepts active follow-up speech", () => {
    expect(classifyJarvisAddress({
      ...base,
      transcript: "continue with the report",
      wakeScore: 0.51,
      speechContinuedMsAfterWake: 80,
    }).accepted).toBe(false);
    expect(classifyJarvisAddress({
      ...base,
      transcript: "continue with the report",
      followUpWindowOpen: true,
    }).reason).toBe("follow_up_window");
  });
});
