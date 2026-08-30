import { describe, expect, it } from "vitest";

import { Float32RingBuffer } from "./audio-ring-buffer";

describe("Float32RingBuffer", () => {
  it("returns samples in capture order before and after wrapping", () => {
    const buffer = new Float32RingBuffer(5);
    buffer.push(new Float32Array([1, 2, 3]));
    expect([...buffer.snapshot()]).toEqual([1, 2, 3]);

    buffer.push(new Float32Array([4, 5, 6]));
    expect([...buffer.snapshot()]).toEqual([2, 3, 4, 5, 6]);
    expect(buffer.length).toBe(5);
  });

  it("keeps only the newest samples from an oversized input", () => {
    const buffer = new Float32RingBuffer(3);
    buffer.push(new Float32Array([1, 2, 3, 4, 5]));
    expect([...buffer.snapshot()]).toEqual([3, 4, 5]);
  });

  it("clears retained audio and rejects access after destruction", () => {
    const buffer = new Float32RingBuffer(4);
    buffer.push(new Float32Array([1, 2, 3]));
    buffer.destroy();

    expect(buffer.length).toBe(0);
    expect(() => buffer.snapshot()).toThrow("destroyed");
    expect(() => buffer.push(new Float32Array([4]))).toThrow("destroyed");
    expect(() => buffer.destroy()).not.toThrow();
  });
});
