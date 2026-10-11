import type { JarvisLiveEvent, JarvisSession } from "./api";
import type { JarvisVoiceSnapshot } from "./jarvis-voice-client";
import type { JarvisLiveStartOptions } from "./jarvis-live";

export interface PhoneAudioState {
  type: "audio_state";
  mode: "paused" | "wake_starting" | "waiting" | "connecting" | "live" | "stopping" | "error";
  text: string;
  activation_id?: string;
  session_id?: string;
  generation?: number;
}
export interface PhoneVoiceBridge {
  postMessage(message: string): void;
  onmessage: ((event: { data: string }) => void) | null;
}

export function phoneVoiceBridge(): PhoneVoiceBridge | null {
  if (typeof window === "undefined") return null;
  const bridge = (window as Window & { JarvisPhoneVoice?: PhoneVoiceBridge }).JarvisPhoneVoice;
  if (bridge && typeof bridge.postMessage === "function") return bridge;
  if (navigator.userAgent.includes(" JarvisPhone/")) return {
    onmessage: null,
    postMessage() { throw new Error("Update Android System WebView to enable voice controls"); },
  };
  return null;
}

/** The page observes Android audio; disposing it never stops the conversation. */
export class JarvisPhoneVoiceController {
  private state: PhoneAudioState = { type: "audio_state", mode: "paused", text: "Microphone off" };
  private listeners = new Set<(state: PhoneAudioState) => void>();
  private disposed = false;
  private cancellations = new Set<() => void>();
  constructor(private bridge: PhoneVoiceBridge, private onSnapshot: (state: JarvisVoiceSnapshot) => void,
    private onState: (state: PhoneAudioState) => void) {
    bridge.onmessage = (event) => {
      if (this.disposed) return;
      let value: unknown;
      try { value = JSON.parse(event.data); } catch { return; }
      if (!value || typeof value !== "object") return;
      const state = value as PhoneAudioState;
      if (state.type !== "audio_state" || !["paused", "wake_starting", "waiting", "connecting", "live", "stopping", "error"].includes(state.mode)
        || typeof state.text !== "string") return;
      this.state = state;
      this.onSnapshot(this.current()); this.onState(state);
      this.listeners.forEach(listener => listener(state));
    };
    try { this.bridge.postMessage(JSON.stringify({ command: "get_state" })); }
    catch (cause) { this.state = { type: "audio_state", mode: "error", text: String((cause as Error).message) }; this.onSnapshot(this.current()); }
  }
  current(): JarvisVoiceSnapshot {
    return { status: this.state.mode === "live" ? "connected" : this.state.mode === "connecting" ? "connecting"
      : this.state.mode === "error" ? "error" : "idle", micEnabled: this.state.mode === "live",
      playbackActive: this.state.mode === "live", error: this.state.mode === "error" ? this.state.text : null };
  }
  ownsSession(session: JarvisSession | null): boolean {
    return session !== null && session.id === this.state.session_id && session.generation === this.state.generation
      && ["connecting", "live"].includes(this.state.mode);
  }
  async startWithOptions(_options?: JarvisLiveStartOptions): Promise<JarvisVoiceSnapshot> {
    if (this.state.mode === "live") return this.current();
    return this.change("listen_now", state => state.mode === "live");
  }
  async stop(): Promise<void> {
    await this.change("stop_live", state => state.mode === "paused");
  }
  async wake(): Promise<void> {
    await this.change("hey_jarvis", state => state.mode === "waiting");
  }
  private change(command: string, done: (state: PhoneAudioState) => boolean): Promise<JarvisVoiceSnapshot> {
    if (this.disposed) return Promise.reject(new Error("Android voice observer closed"));
    return new Promise((resolve, reject) => {
      const cleanup = () => { clearTimeout(timer); this.listeners.delete(listener); this.cancellations.delete(cancel); };
      const cancel = () => { cleanup(); reject(new Error("Android voice observer closed")); };
      const timer = setTimeout(() => { cleanup(); reject(new Error("Android voice control timed out")); }, 20_000);
      const listener = (state: PhoneAudioState) => {
        if (state.mode !== "error" && !done(state)) return;
        cleanup();
        if (state.mode === "error") reject(new Error(state.text)); else resolve(this.current());
      };
      this.listeners.add(listener);
      this.cancellations.add(cancel);
      try { this.bridge.postMessage(JSON.stringify({ command, activation_id: this.state.activation_id,
        session_id: this.state.session_id, generation: this.state.generation })); }
      catch (cause) { cleanup(); reject(cause); }
    });
  }
  recordBackendEvent(_event: JarvisLiveEvent): void { /* State is owned by Android. */ }
  recordLiveStreamState(_state: "opened" | "errored"): void { /* Native owns its event connection. */ }
  dispose(): void {
    this.disposed = true; this.bridge.onmessage = null;
    this.cancellations.forEach(cancel => cancel()); this.listeners.clear();
  }
}
