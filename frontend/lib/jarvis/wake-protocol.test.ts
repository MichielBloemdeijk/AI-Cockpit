import { describe, expect, it } from "vitest";

import { defaultJarvisWakeModelConfiguration } from "./wake-protocol";

describe("Jarvis wake protocol defaults", () => {
  it("enables the benchmark endpoint and safety pre-roll defaults", () => {
    expect(defaultJarvisWakeModelConfiguration()).toMatchObject({
      endpointSilenceMs: 300,
      preRollPaddingMs: 300,
    });
  });
});
