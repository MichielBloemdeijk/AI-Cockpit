export interface JarvisVadInstance {
  start: () => Promise<void>;
  pause: () => void;
  resume: () => Promise<void>;
  destroy: () => void;
}

/** MicVAD.resume reconnects the stream but does not restart its frame processor. */
export async function restartJarvisVad(vad: JarvisVadInstance | null, micEnabled: boolean): Promise<void> {
  if (vad && micEnabled) await vad.start();
}
