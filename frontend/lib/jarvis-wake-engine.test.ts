import { describe, expect, it, vi } from "vitest";

import {
  JarvisWakeEngine,
  type JarvisWakeInferenceResult,
} from "./jarvis-wake-engine";

function engine(results: JarvisWakeInferenceResult[]) {
  const detected = vi.fn();
  const utterances: Float32Array[] = [];
  const errors = vi.fn();
  const analyze = vi.fn(async () => results.shift() ?? {
    speechProbability: 0,
    wakeProbability: 0,
  });
  const value = new JarvisWakeEngine({
    inference: { analyze },
    onDetected: detected,
    onUtterance: (samples) => utterances.push(samples),
    onError: errors,
    batchSamples: 4,
    batchIntervalSamples: 2,
    negativeVadCount: 1,
    maximumUtteranceSamples: 20,
  });
  return { value, detected, utterances, errors, analyze };
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
