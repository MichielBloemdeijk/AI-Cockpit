export const JARVIS_WAKE_SAMPLE_RATE = 16_000;
export const JARVIS_WAKE_RING_SECONDS = 2.5;

export interface JarvisWakeModelConfiguration {
  vadModelUrl: string;
  spectrogramModelUrl: string;
  embeddingModelUrl: string;
  wakeModelUrl: string;
  wasmBaseUrl: string;
  wakeThreshold?: number;
  candidateWakeThreshold?: number;
  candidateWakeHits?: number;
  speechLookbackMs?: number;
  candidateLookbackMs?: number;
  positiveVadThreshold?: number;
  negativeVadThreshold?: number;
  negativeVadCount?: number;
  endpointSilenceMs?: number;
  preRollPaddingMs?: number;
}

export type JarvisWakeWorkerCommand =
  | { type: "init"; configuration: JarvisWakeModelConfiguration }
  | { type: "audio"; samples: Float32Array; capturedMonotonicMs?: number }
  | { type: "pause" }
  | { type: "resume" }
  | { type: "destroy" };

export type JarvisWakeWorkerEvent =
  | { type: "ready" }
  | {
    type: "detected";
    probability: number;
    activationId?: string;
    preRoll?: Float32Array;
    sampleRate?: number;
  }
  | { type: "candidate_audio"; samples: Float32Array; sampleRate: number }
  | {
    type: "candidate_chunk";
    activationId: string;
    sequence: number;
    sampleRate: 16_000;
    startSample: number;
    endSample: number;
    capturedMonotonicMs: number;
    samples: Float32Array;
  }
  | {
    type: "candidate_endpoint";
    sampleRate: number;
    totalSamples: number;
    trailingSilenceMs: number;
    speechEndSample?: number;
    observedAtSample?: number;
    trailingSilenceSamples?: number;
    processingLagMs?: number;
  }
  | {
    type: "candidate_close";
    reason: "audio_backlog_overflow" | "rearmed" | "sleep";
  }
  | { type: "vad_boundary"; state: "speech_started" | "speech_stopped"; probability: number }
  | { type: "utterance"; samples: Float32Array; sampleRate: number }
  | { type: "error"; message: string };

export function defaultJarvisWakeModelConfiguration(): JarvisWakeModelConfiguration {
  return {
    vadModelUrl: "/jarvis-wake/models/silero-vad.onnx",
    spectrogramModelUrl: "/jarvis-wake/models/mel-spectrogram.onnx",
    embeddingModelUrl: "/jarvis-wake/models/speech-embedding.onnx",
    wakeModelUrl: "/jarvis-wake/models/jarvis.onnx",
    wasmBaseUrl: "/vad/",
    wakeThreshold: 0.45,
    candidateWakeThreshold: 0.18,
    candidateWakeHits: 2,
    speechLookbackMs: 1_200,
    candidateLookbackMs: 960,
    endpointSilenceMs: 300,
    preRollPaddingMs: 300,
  };
}

/** Build-time opt-in prevents a missing wake model from prompting for the mic. */
export function configuredJarvisWakeModel(): JarvisWakeModelConfiguration | null {
  const wakeModelUrl = process.env.NEXT_PUBLIC_JARVIS_WAKE_MODEL_URL?.trim();
  if (!wakeModelUrl) return null;
  return { ...defaultJarvisWakeModelConfiguration(), wakeModelUrl };
}
