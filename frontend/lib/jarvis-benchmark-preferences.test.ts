import { describe, expect, it } from "vitest";
import {
  JARVIS_WAKE_GATE_STORAGE_KEY,
  loadJarvisWakeGateMode,
  resolveJarvisWakeGateMode,
  saveJarvisWakeGateMode,
} from "./jarvis-benchmark-preferences";

function storage(initial: string | null = null) {
  let value = initial;
  return {
    getItem: () => value,
    setItem: (_key: string, next: string) => { value = next; },
  };
}

describe("Jarvis benchmark gate preferences", () => {
  it("persists a valid selection and restores it after reload", () => {
    const state = storage();
    saveJarvisWakeGateMode("immediate", state);
    expect(loadJarvisWakeGateMode(state)).toBe("immediate");
    expect(JARVIS_WAKE_GATE_STORAGE_KEY).toBe("jarvis.developer.wakeGateMode.v1");
  });

  it("falls back safely for corrupt storage and gates immediate mode", () => {
    const state = storage("not-a-mode");
    expect(loadJarvisWakeGateMode(state)).toBe("transcript_rules");
    expect(resolveJarvisWakeGateMode(false, "immediate")).toBe("transcript_rules");
    expect(resolveJarvisWakeGateMode(true, "immediate")).toBe("immediate");
  });
});
