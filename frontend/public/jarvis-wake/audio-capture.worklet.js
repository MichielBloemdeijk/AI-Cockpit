class JarvisWakeAudioCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.targetSampleRate = options.processorOptions.targetSampleRate;
    this.outputSize = Math.round(this.targetSampleRate / 50);
    this.inputSize = Math.round(sampleRate / 50);
    this.input = new Float32Array(this.inputSize);
    this.inputLength = 0;
    this.paused = false;
    this.port.onmessage = (event) => {
      this.paused = event.data?.type === "pause";
    };
  }

  flush() {
    const output = new Float32Array(this.outputSize);
    const ratio = sampleRate / this.targetSampleRate;
    for (let index = 0; index < output.length; index += 1) {
      const source = index * ratio;
      const lower = Math.floor(source);
      const upper = Math.min(lower + 1, this.input.length - 1);
      const fraction = source - lower;
      output[index] = this.input[lower] * (1 - fraction) + this.input[upper] * fraction;
    }
    this.port.postMessage(output, [output.buffer]);
  }

  push(samples) {
    let offset = 0;
    while (offset < samples.length) {
      const writable = Math.min(samples.length - offset, this.input.length - this.inputLength);
      this.input.set(samples.subarray(offset, offset + writable), this.inputLength);
      this.inputLength += writable;
      offset += writable;
      if (this.inputLength === this.input.length) {
        this.flush();
        this.inputLength = 0;
      }
    }
  }

  process(inputs) {
    if (!this.paused && inputs[0]?.[0]) this.push(inputs[0][0]);
    return true;
  }
}

registerProcessor("jarvis-wake-audio-capture", JarvisWakeAudioCapture);
