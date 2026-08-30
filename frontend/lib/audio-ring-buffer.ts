/**
 * Fixed-capacity, in-memory PCM history. The buffer never grows and can be
 * explicitly zeroed when microphone processing stops.
 */
export class Float32RingBuffer {
  private readonly samples: Float32Array;
  private writeIndex = 0;
  private sampleCount = 0;
  private destroyed = false;

  constructor(readonly capacity: number) {
    if (!Number.isInteger(capacity) || capacity <= 0) {
      throw new Error("Audio ring-buffer capacity must be a positive integer");
    }
    this.samples = new Float32Array(capacity);
  }

  get length(): number {
    return this.sampleCount;
  }

  push(input: Float32Array): void {
    this.assertActive();
    if (!input.length) return;

    if (input.length >= this.capacity) {
      this.samples.set(input.subarray(input.length - this.capacity));
      this.writeIndex = 0;
      this.sampleCount = this.capacity;
      return;
    }

    const firstLength = Math.min(input.length, this.capacity - this.writeIndex);
    this.samples.set(input.subarray(0, firstLength), this.writeIndex);
    const remainder = input.length - firstLength;
    if (remainder) this.samples.set(input.subarray(firstLength), 0);
    this.writeIndex = (this.writeIndex + input.length) % this.capacity;
    this.sampleCount = Math.min(this.capacity, this.sampleCount + input.length);
  }

  snapshot(): Float32Array {
    this.assertActive();
    const output = new Float32Array(this.sampleCount);
    if (!this.sampleCount) return output;

    const start = (this.writeIndex - this.sampleCount + this.capacity) % this.capacity;
    const firstLength = Math.min(this.sampleCount, this.capacity - start);
    output.set(this.samples.subarray(start, start + firstLength));
    if (firstLength < this.sampleCount) {
      output.set(this.samples.subarray(0, this.sampleCount - firstLength), firstLength);
    }
    return output;
  }

  clear(): void {
    this.assertActive();
    this.zero();
  }

  destroy(): void {
    if (this.destroyed) return;
    this.zero();
    this.destroyed = true;
  }

  private zero(): void {
    this.samples.fill(0);
    this.writeIndex = 0;
    this.sampleCount = 0;
  }

  private assertActive(): void {
    if (this.destroyed) throw new Error("Audio ring buffer has been destroyed");
  }
}
