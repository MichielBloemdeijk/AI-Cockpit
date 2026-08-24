import { describe, expect, it, vi } from "vitest";
import { restartJarvisVad, type JarvisVadInstance } from "./jarvis-vad";

describe("Jarvis VAD adapter", () => {
  it("restarts MicVAD after pause instead of only reconnecting its stream", async () => {
    const vad: JarvisVadInstance = { start: vi.fn(async () => undefined), pause: vi.fn(), resume: vi.fn(async () => undefined), destroy: vi.fn() };
    await restartJarvisVad(vad, true);
    expect(vad.start).toHaveBeenCalledOnce();
    expect(vad.resume).not.toHaveBeenCalled();
  });

  it("does not restart a stopped microphone", async () => {
    const vad: JarvisVadInstance = { start: vi.fn(async () => undefined), pause: vi.fn(), resume: vi.fn(async () => undefined), destroy: vi.fn() };
    await restartJarvisVad(vad, false);
    expect(vad.start).not.toHaveBeenCalled();
  });
});
