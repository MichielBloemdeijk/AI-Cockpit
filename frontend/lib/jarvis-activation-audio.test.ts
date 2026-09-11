import { describe, expect, it, vi } from "vitest";
import { createJarvisActivationAudio } from "./jarvis-activation-audio";

describe("JarvisActivationAudio", () => {
  it("keeps delayed subscriptions ordered in exact 20 ms chunks", () => {
    const owner = createJarvisActivationAudio("activation-1");
    const first = Float32Array.from({ length: 160 }, (_, index) => index);
    const second = Float32Array.from({ length: 480 }, (_, index) => index + 160);
    owner.append(first, 10);
    owner.append(second, 20);
    const chunks: { sequence: number; start: number; end: number; samples: number[] }[] = [];
    owner.subscribe((chunk) => {
      chunks.push({
        sequence: chunk.sequence,
        start: chunk.startSample,
        end: chunk.endSample,
        samples: [...chunk.samples],
      });
    });

    expect(chunks.map(({ sequence, start, end }) => ({ sequence, start, end }))).toEqual([
      { sequence: 0, start: 0, end: 320 },
      { sequence: 1, start: 320, end: 640 },
    ]);
    expect(chunks[0].samples[0]).toBe(0);
    expect(chunks[1].samples[0]).toBe(320);
    expect(first.every((value) => value === 0)).toBe(true);
    expect(second.every((value) => value === 0)).toBe(true);
  });

  it("allows only one subscriber and zeroes retained PCM on close", () => {
    const owner = createJarvisActivationAudio("activation-2");
    const retained = Float32Array.from({ length: 320 }, () => 0.5);
    owner.append(retained);
    expect(() => owner.subscribe(() => undefined)).not.toThrow();
    expect(() => owner.subscribe(() => undefined)).toThrow(/one subscriber/i);
    owner.close("rejected");
    expect(owner.isClosed).toBe(true);
    expect(retained.every((value) => value === 0)).toBe(true);
  });

  it("reports and closes on a hard retained-audio backlog", async () => {
    const onDiagnostic = vi.fn();
    const owner = createJarvisActivationAudio("activation-3", {
      hardBacklogMs: 20,
      onDiagnostic,
    });
    const samples = new Float32Array(640);
    owner.append(samples);
    expect(owner.isClosed).toBe(true);
    expect(onDiagnostic).toHaveBeenCalledWith(expect.objectContaining({
      kind: "audio_backlog_overflow",
      activationId: "activation-3",
    }));
    await expect(owner.observeEndpoint()).rejects.toThrow(/closed/i);
  });

  it("replays every uncommitted range once to a replacement subscriber", () => {
    const owner = createJarvisActivationAudio("activation-retry");
    const firstAttempt: number[] = [];
    const unsubscribe = owner.subscribe((chunk) => firstAttempt.push(chunk.sequence));
    owner.append(new Float32Array(640).fill(1));
    unsubscribe();
    owner.append(new Float32Array(320).fill(2));

    const retry: Array<{ sequence: number; value: number }> = [];
    owner.subscribe((chunk) => retry.push({ sequence: chunk.sequence, value: chunk.samples[0] }));

    expect(firstAttempt).toEqual([0, 1]);
    expect(retry).toEqual([
      { sequence: 0, value: 1 },
      { sequence: 1, value: 1 },
      { sequence: 2, value: 2 },
    ]);
    owner.promoteToConversation();
    expect(owner.retainedSamples).toBe(0);
  });
});
