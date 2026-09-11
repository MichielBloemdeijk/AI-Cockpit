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

  it("streams bounded pre-roll and subsequent chunks before one endpoint", async () => {
    const starts: Array<{ probability: number; samples: Float32Array }> = [];
    const chunks: Float32Array[] = [];
    const endpoints: unknown[] = [];
    const results: JarvisWakeInferenceResult[] = [
      { speechProbability: 0.9, wakeProbability: 0.9 },
      { speechProbability: 0.1, wakeProbability: 0 },
    ];
    const value = new JarvisWakeEngine({
      inference: { analyze: async () => results.shift() ?? { speechProbability: 0, wakeProbability: 0 } },
      onDetected: vi.fn(),
      onCandidateStart: (probability, preRoll) => starts.push({ probability, samples: preRoll }),
      onCandidateAudio: (samples) => chunks.push(samples),
      onCandidateEndpoint: (endpoint) => endpoints.push(endpoint),
      onUtterance: vi.fn(),
      onError: vi.fn(),
      batchSamples: 2,
      batchIntervalSamples: 2,
      speechLookbackMs: 0,
      preRollPaddingMs: 300,
      endpointSilenceMs: 0,
    });

    value.push(new Float32Array(4_800).fill(1));
    await value.idle();
    value.push(new Float32Array([2, 3]));
    await value.idle();

    expect(starts).toHaveLength(1);
    expect(starts[0].probability).toBe(0.9);
    expect(starts[0].samples.length).toBe(4_800);
    expect(chunks.map((chunk) => [...chunk])).toEqual([[2, 3]]);
    expect(endpoints).toEqual([{
      sampleRate: 16_000,
      totalSamples: 4_802,
      trailingSilenceMs: 0,
    }]);
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
      onUtterance: vi.fn(),
      onError: vi.fn(),
      maximumUtteranceSamples: 320,
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
      onUtterance: vi.fn(),
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
      onUtterance: vi.fn(),
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
      onUtterance: vi.fn(),
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
