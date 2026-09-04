export interface JarvisVadInstance {
  start: () => Promise<void>;
  pause: () => void;
  resume: () => Promise<void>;
  destroy: () => void;
}

export interface JarvisBargeInDetector {
  destroy: () => void;
}

/**
 * Run Silero VAD over the same microphone stream already owned by WebRTC.
 * Stream lifecycle remains with the realtime controller, so destroying this
 * detector never stops the peer's microphone track.
 */
export async function createJarvisBargeInDetector(
  stream: MediaStream,
  onSpeechStart: () => void,
  onSpeechEnd: () => void = () => undefined,
): Promise<JarvisBargeInDetector> {
  const { MicVAD } = await import("@ricky0123/vad-web");
  const vad = await MicVAD.new({
    model: "v5",
    baseAssetPath: "/vad/",
    onnxWASMBasePath: "/vad/",
    getStream: async () => stream,
    pauseStream: async () => undefined,
    resumeStream: async () => stream,
    positiveSpeechThreshold: 0.7,
    negativeSpeechThreshold: 0.4,
    minSpeechMs: 96,
    preSpeechPadMs: 0,
    redemptionMs: 300,
    onSpeechStart,
    onSpeechEnd,
  });
  await vad.start();
  return vad;
}

/** MicVAD.resume reconnects the stream but does not restart its frame processor. */
export async function restartJarvisVad(vad: JarvisVadInstance | null, micEnabled: boolean): Promise<void> {
  if (vad && micEnabled) await vad.start();
}
