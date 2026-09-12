export type JarvisVoiceEngine = "realtime" | "openai_live";

export interface JarvisVoiceSnapshot {
  status: "idle" | "connecting" | "connected" | "interrupted" | "closed" | "error";
  engine: JarvisVoiceEngine;
  micEnabled: boolean;
  playbackActive: boolean;
  error: string | null;
}

export interface JarvisVoiceClient {
  current(): JarvisVoiceSnapshot;
  start(stream?: MediaStream): Promise<JarvisVoiceSnapshot>;
  interrupt(): void;
  stop(): Promise<void>;
}
