import { describe, expect, it, vi } from "vitest";

const sessions = vi.hoisted(() => [] as Array<{ url: string; released: boolean }>);

vi.mock("onnxruntime-web", () => ({
  env: { wasm: {} },
  Tensor: class {},
  InferenceSession: {
    create: async (url: string) => {
      if (url === "broken-wake") throw new Error("model missing");
      const session = { url, released: false, async release() { this.released = true; } };
      sessions.push(session);
      return session;
    },
  },
}));

import { HeyBuddyOnnxInference } from "./jarvis-hey-buddy-inference";

describe("HeyBuddyOnnxInference.create", () => {
  it("releases the sessions that loaded when another model fails", async () => {
    await expect(HeyBuddyOnnxInference.create({
      vadModelUrl: "vad",
      spectrogramModelUrl: "mel",
      embeddingModelUrl: "embedding",
      wakeModelUrl: "broken-wake",
      wasmBaseUrl: "/",
    } as Parameters<typeof HeyBuddyOnnxInference.create>[0])).rejects.toThrow("model missing");

    expect(sessions.map((session) => [session.url, session.released])).toEqual([
      ["vad", true], ["mel", true], ["embedding", true],
    ]);
  });
});
