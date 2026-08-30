export const JARVIS_WAKE_SAMPLE_RATE = 16_000;
export const JARVIS_WAKE_RING_SECONDS = 2.5;

export interface JarvisWakeModelConfiguration {
  vadModelUrl: string;
  spectrogramModelUrl: string;
  embeddingModelUrl: string;
  wakeModelUrl: string;
  wasmBaseUrl: string;
  wakeThreshold?: number;
  positiveVadThreshold?: number;
  negativeVadThreshold?: number;
  negativeVadCount?: number;
}

export type JarvisWakeWorkerCommand =
  | { type: "init"; configuration: JarvisWakeModelConfiguration }
  | { type: "audio"; samples: Float32Array }
  | { type: "pause" }
  | { type: "resume" }
  | { type: "destroy" };

export type JarvisWakeWorkerEvent =
  | { type: "ready" }
  | { type: "detected"; probability: number }
  | { type: "utterance"; samples: Float32Array; sampleRate: number }
  | { type: "error"; message: string };

export function defaultJarvisWakeModelConfiguration(): JarvisWakeModelConfiguration {
  return {
    vadModelUrl: "/jarvis-wake/models/silero-vad.onnx",
    spectrogramModelUrl: "/jarvis-wake/models/mel-spectrogram.onnx",
    embeddingModelUrl: "/jarvis-wake/models/speech-embedding.onnx",
    wakeModelUrl: "/jarvis-wake/models/jarvis.onnx",
    wasmBaseUrl: "/vad/",
  };
}

/** Build-time opt-in prevents a missing wake model from prompting for the mic. */
export function configuredJarvisWakeModel(): JarvisWakeModelConfiguration | null {
  const wakeModelUrl = process.env.NEXT_PUBLIC_JARVIS_WAKE_MODEL_URL?.trim();
  if (!wakeModelUrl) return null;
  return { ...defaultJarvisWakeModelConfiguration(), wakeModelUrl };
}
