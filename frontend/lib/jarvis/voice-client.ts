import type { JarvisLiveEvent, JarvisSession } from "../api/jarvis";
import type { JarvisLiveStartOptions } from "./live";

export interface JarvisVoiceSnapshot {
  status: "idle" | "connecting" | "connected" | "interrupted" | "closed" | "error";
  micEnabled: boolean;
  playbackActive: boolean;
  error: string | null;
}

/** What the voice owner needs from a Live transport: the browser's own or Android's. */
export interface JarvisVoiceClient {
  current(): JarvisVoiceSnapshot;
  ownsSession(session: JarvisSession | null): boolean;
  startWithOptions(options?: JarvisLiveStartOptions): Promise<JarvisVoiceSnapshot>;
  stop(): Promise<void>;
  recordBackendEvent(event: JarvisLiveEvent): void;
  recordLiveStreamState(state: "opened" | "errored"): void;
}
