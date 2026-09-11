import { describe, expect, it } from "vitest";

import { resolveJarvisWakeGateMode } from "./jarvis-wake-gate";

describe("resolveJarvisWakeGateMode", () => {
  it("defaults to transcript rules", () => {
    expect(resolveJarvisWakeGateMode(true, "transcript_rules")).toBe("transcript_rules");
  });

  it("allows immediate mode for developer benchmarks", () => {
    expect(resolveJarvisWakeGateMode(true, "immediate")).toBe("immediate");
  });

  it("guards immediate mode when developer mode is disabled", () => {
    expect(resolveJarvisWakeGateMode(false, "immediate")).toBe("transcript_rules");
  });
});
