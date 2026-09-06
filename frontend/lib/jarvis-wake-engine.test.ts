import { describe, expect, it, vi } from "vitest";

import {
  JarvisWakeEngine,
  type JarvisWakeInferenceResult,
} from "./jarvis-wake-engine";

function engine(results: JarvisWakeInferenceResult[]) {
  const detected = vi.fn();
  const utterances: Float32Array[] = [];
  const errors = vi.fn();
  const speechStarted = vi.fn();
  const speechStopped = vi.fn();
  const analyze = vi.fn(async () => results.shift() ?? {
    speechProbability: 0,
    wakeProbability: 0,
  });
  const value = new JarvisWakeEngine({
    inference: { analyze },
    onDetected: detected,
    onUtterance: (samples) => utterances.push(samples),
    onError: errors,
    onSpeechStart: speechStarted,
    onSpeechEnd: speechStopped,
    batchSamples: 4,
    batchIntervalSamples: 2,
    negativeVadCount: 1,
    maximumUtteranceSamples: 20,
  });
  return { value, detected, utterances, errors, analyze, speechStarted, speechStopped };
}

describe("JarvisWakeEngine", () => {
  it("keeps the bounded pre-roll and emits one utterance after speech ends", async () => {
    const wake = engine([
      { speechProbability: 0.9, wakeProbability: 0.9 },
      { speechProbability: 0.1, wakeProbability: 0 },
      { speechProbability: 0.1, wakeProbability: 0 },
    ]);

    wake.value.push(new Float32Array([1, 2, 3, 4]));
    await wake.value.idle();
    wake.value.push(new Float32Array([5, 6]));
    await wake.value.idle();
    wake.value.push(new Float32Array([7, 8]));
    await wake.value.idle();

    expect(wake.detected).toHaveBeenCalledOnce();
    expect(wake.utterances).toHaveLength(1);
    expect([...wake.utterances[0]]).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    wake.value.push(new Float32Array([9, 10]));
    await wake.value.idle();
    expect(wake.analyze).toHaveBeenCalledTimes(3);
    expect(wake.speechStarted).toHaveBeenCalledWith(0.9);
    expect(wake.speechStopped).toHaveBeenCalledWith(0.1);
  });

  it("accepts a strong wake hit shortly after speech instead of requiring the same frame", async () => {
    const wake = engine([
      { speechProbability: 0.9, wakeProbability: 0.02 },
      { speechProbability: 0.1, wakeProbability: 0.48 },
    ]);

    wake.value.push(new Float32Array([1, 2, 3, 4]));
    await wake.value.idle();
    wake.value.push(new Float32Array([5, 6]));
    await wake.value.idle();

    expect(wake.detected).toHaveBeenCalledWith(0.48);
  });

  it("accepts two low-confidence hits during recent speech", async () => {
    const wake = engine([
      { speechProbability: 0.9, wakeProbability: 0.19 },
      { speechProbability: 0.2, wakeProbability: 0.22 },
    ]);

    wake.value.push(new Float32Array([1, 2, 3, 4]));
    await wake.value.idle();
    wake.value.push(new Float32Array([5, 6]));
    await wake.value.idle();

    expect(wake.detected).toHaveBeenCalledWith(0.22);
  });

  it("does not activate from one low-confidence hit", async () => {
    const wake = engine([
      { speechProbability: 0.9, wakeProbability: 0.19 },
      { speechProbability: 0.2, wakeProbability: 0.05 },
    ]);

    wake.value.push(new Float32Array([1, 2, 3, 4]));
    await wake.value.idle();
    wake.value.push(new Float32Array([5, 6]));
    await wake.value.idle();

    expect(wake.detected).not.toHaveBeenCalled();
  });

  it("drops queued audio and reports inference failure once", async () => {
    const errors = vi.fn();
    const value = new JarvisWakeEngine({
      inference: { analyze: async () => { throw new Error("bad model"); } },
      onDetected: vi.fn(),
      onUtterance: vi.fn(),
      onError: errors,
      batchSamples: 2,
      batchIntervalSamples: 2,
    });
    value.push(new Float32Array([1, 2]));
    await value.idle();
    value.push(new Float32Array([3, 4]));
    await value.idle();
    expect(errors).toHaveBeenCalledOnce();
  });

  it("ends a wake utterance after 600 ms of trailing silence by default", async () => {
    const utterances: Float32Array[] = [];
    const results: JarvisWakeInferenceResult[] = [
      { speechProbability: 0.9, wakeProbability: 0.9 },
      ...Array.from({ length: 5 }, () => ({ speechProbability: 0.1, wakeProbability: 0 })),
    ];
    const value = new JarvisWakeEngine({
      inference: { analyze: async () => results.shift() ?? { speechProbability: 0, wakeProbability: 0 } },
      onDetected: vi.fn(),
      onUtterance: (samples) => utterances.push(samples),
      onError: vi.fn(),
      batchSamples: 1_920,
      batchIntervalSamples: 1_920,
      maximumUtteranceSamples: 20_000,
    });

    value.push(new Float32Array(1_920));
    await value.idle();
    for (let index = 0; index < 4; index += 1) {
      value.push(new Float32Array(1_920));
      await value.idle();
    }
    expect(utterances).toEqual([]);
    value.push(new Float32Array(1_920));
    await value.idle();
    expect(utterances).toHaveLength(1);
  });

  it("zeroes state and destroys inference exactly once", async () => {
    const destroy = vi.fn();
    const wake = engine([]);
    const value = new JarvisWakeEngine({
      inference: { analyze: wake.analyze, destroy },
      onDetected: vi.fn(),
      onUtterance: vi.fn(),
      onError: vi.fn(),
      batchSamples: 2,
      batchIntervalSamples: 2,
    });
    value.push(new Float32Array([1, 2]));
    await value.destroy();
    await value.destroy();
    expect(destroy).toHaveBeenCalledOnce();
  });
});
