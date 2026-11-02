import { describe, expect, it, vi } from "vitest";

import { JarvisWakeEngine, type JarvisWakeInference } from "./wake-engine";

/** Speech and wake scores per frame; everything after the lists is silence and no wake. */
function scripted(speech: number[], wake: number[]): JarvisWakeInference {
  return {
    observeSpeech: async (_frame, range) => ({ ...range, speechProbability: speech.shift() ?? 0.1, completedMonotonicMs: 0 }),
    classifyWake: async () => ({ wakeProbability: wake.shift() ?? 0 }),
  };
}

describe("JarvisWakeEngine", () => {
  it("does not activate from one low-confidence hit", async () => {
    const detected = vi.fn();
    const value = new JarvisWakeEngine({ inference: scripted(Array(20).fill(0.9), [0.2, 0.05]), onDetected: detected, onError: vi.fn() });
    value.push(new Float32Array(15_360));
    await value.idle();
    value.push(new Float32Array(1_920));
    await value.idle();
    value.push(new Float32Array(1_920));
    await value.idle();
    expect(detected).not.toHaveBeenCalled();
  });

  it("stops and reports an inference failure once", async () => {
    const errors = vi.fn();
    const value = new JarvisWakeEngine({
      inference: { observeSpeech: async () => { throw new Error("bad model"); }, classifyWake: async () => ({ wakeProbability: 0 }) },
      onDetected: vi.fn(),
      onError: errors,
    });
    value.push(new Float32Array(3_840));
    await value.idle();
    value.push(new Float32Array(3_840));
    await value.idle();
    expect(errors).toHaveBeenCalledOnce();
  });

  it("zeroes state and destroys inference exactly once", async () => {
    const destroy = vi.fn();
    const value = new JarvisWakeEngine({ inference: { ...scripted([], []), destroy }, onDetected: vi.fn(), onError: vi.fn() });
    value.push(new Float32Array(1_920));
    await value.destroy();
    await value.destroy();
    expect(destroy).toHaveBeenCalledOnce();
  });

  it("delivers the bounded pre-roll as the first chunks, then each later sample once", async () => {
    const chunks: Array<{ sequence: number; first: number }> = [];
    const starts = vi.fn();
    const value = new JarvisWakeEngine({
      inference: scripted(Array(20).fill(0.9), [0.9]),
      onDetected: vi.fn(),
      onCandidateStart: starts,
      onCandidateChunk: (chunk) => chunks.push({ sequence: chunk.sequence, first: chunk.samples[0] }),
      onError: vi.fn(),
      speechLookbackMs: 0,
      preRollPaddingMs: 100,
    });
    value.push(new Float32Array(17_280).fill(1));
    await value.idle();
    expect(starts).toHaveBeenCalledWith(0.9);
    // 100 ms of pre-roll = 1,600 samples = five 320-sample chunks.
    expect(chunks.map((chunk) => chunk.first)).toEqual([1, 1, 1, 1, 1]);
    value.push(new Float32Array(640).fill(2));
    await value.idle();
    expect(chunks.map((chunk) => chunk.sequence)).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(chunks.slice(5).map((chunk) => chunk.first)).toEqual([2, 2]);
  });

  it("keeps split candidate audio open independently of capture age and utterance duration", async () => {
    const chunks: Float32Array[] = [];
    const close = vi.fn();
    const classifyWake = vi.fn(async () => ({ wakeProbability: 0.9 }));
    const value = new JarvisWakeEngine({
      inference: {
        observeSpeech: async (_frame, range) => ({ ...range, speechProbability: 0.9, completedMonotonicMs: 0 }),
        classifyWake,
      },
      onDetected: vi.fn(),
      onCandidateChunk: (chunk) => chunks.push(chunk.samples.slice()),
      onCandidateClose: close,
      onError: vi.fn(),
    });

    value.push(new Float32Array(17_280).fill(1), 1_000);
    await value.idle();
    const initialChunkCount = chunks.length;
    value.push(new Float32Array(640).fill(2), 2_080);
    await value.idle();

    expect(classifyWake).toHaveBeenCalledOnce();
    expect(close).not.toHaveBeenCalled();
    expect(chunks).toHaveLength(initialChunkCount + 2);
    expect([...chunks.at(-1)!]).toEqual([...new Float32Array(320).fill(2)]);
  });

  it("accepts repeated low-confidence split wake hits during recent speech", async () => {
    const wakeScores = [0.2, 0.22];
    const detected = vi.fn();
    const value = new JarvisWakeEngine({
      inference: {
        observeSpeech: async (_frame, range) => ({ ...range, speechProbability: 0.9, completedMonotonicMs: 0 }),
        classifyWake: async () => ({ wakeProbability: wakeScores.shift() ?? 0 }),
      },
      onDetected: detected,
      onError: vi.fn(),
    });

    // Fill the first wake window without also advancing through a second
    // overlapping classification in the same drain cycle.
    value.push(new Float32Array(15_360));
    await value.idle();
    value.push(new Float32Array(1_920));
    await value.idle();
    expect(detected).not.toHaveBeenCalled();
    value.push(new Float32Array(1_920));
    await value.idle();
    expect(detected).toHaveBeenCalledWith(0.22);
  });

  it("accepts a strong split wake hit just after VAD speech ends", async () => {
    const speechProbabilities = [
      ...Array.from({ length: 9 }, () => 0.9),
      0.1,
    ];
    const wakeScores = [0, 0.9];
    const detected = vi.fn();
    const value = new JarvisWakeEngine({
      inference: {
        observeSpeech: async (_frame, range) => ({
          ...range,
          speechProbability: speechProbabilities.shift() ?? 0.1,
          completedMonotonicMs: 0,
        }),
        classifyWake: async () => ({ wakeProbability: wakeScores.shift() ?? 0 }),
      },
      onDetected: detected,
      onError: vi.fn(),
    });

    value.push(new Float32Array(17_280));
    await value.idle();
    value.push(new Float32Array(1_920));
    await value.idle();
    expect(detected).toHaveBeenCalledWith(0.9);
  });

  it("requires consecutive negative split VAD frames and never pads an endpoint", async () => {
    const probabilities = [
      ...Array.from({ length: 9 }, () => 0.9),
      0.1,
      0.5,
      0.1,
      0.1,
      0.9,
    ];
    const chunks: Float32Array[] = [];
    const endpoints: unknown[] = [];
    const value = new JarvisWakeEngine({
      inference: {
        observeSpeech: async (_frame, range) => ({
          ...range,
          speechProbability: probabilities.shift() ?? 0.9,
          completedMonotonicMs: 0,
        }),
        classifyWake: async () => ({ wakeProbability: 0.9 }),
      },
      onDetected: vi.fn(),
      onCandidateChunk: (chunk) => chunks.push(chunk.samples.slice()),
      onCandidateEndpoint: (endpoint) => endpoints.push(endpoint),
      onError: vi.fn(),
      endpointSilenceMs: 240,
    });

    value.push(new Float32Array(17_280).fill(1));
    await value.idle();
    // A negative frame followed by a hysteresis frame is not 240 ms silence.
    value.push(new Float32Array(3_840).fill(2));
    await value.idle();
    expect(endpoints).toHaveLength(0);
    value.push(new Float32Array(1_920).fill(3));
    await value.idle();
    expect(endpoints).toHaveLength(0);
    value.push(new Float32Array(1_920).fill(4));
    await value.idle();
    expect(endpoints).toHaveLength(1);

    const countAtEndpoint = chunks.length;
    value.push(new Float32Array(100).fill(5));
    await value.idle();
    expect(chunks).toHaveLength(countAtEndpoint);
    value.push(new Float32Array(220).fill(6));
    await value.idle();
    expect(chunks).toHaveLength(countAtEndpoint + 1);
    expect([...chunks.at(-1)!]).toEqual([
      ...new Float32Array(100).fill(5),
      ...new Float32Array(220).fill(6),
    ]);
  });
});
