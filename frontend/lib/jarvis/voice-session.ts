/**
 * Jarvis voice on this page: who owns the microphone and Live connection, and how wake works.
 *
 * On the phone, Android owns audio; the page only observes and sends commands, so
 * disposing it never stops a live conversation. In a browser, the page owns the
 * wake model, the Live transport and their teardown. Both expose the same state
 * and commands, so the route composes voice without knowing which one it has.
 */
import { closeJarvisLiveSession, createJarvisLiveCall, submitJarvisLiveTelemetry, type JarvisLiveEvent, type JarvisSession } from "../api/jarvis";
import { resolveJarvisWakeGateMode, type JarvisWakeGateMode } from "./benchmark-preferences";
import { JarvisLiveController } from "./live";
import { JarvisPhoneVoiceController, phoneVoiceBridge, type PhoneAudioState } from "./phone-voice";
import type { JarvisVoiceSnapshot } from "./voice-client";
import { configuredJarvisWakeModel } from "./wake-protocol";
import { JarvisWakeRuntime, type JarvisWakeRuntimeSnapshot } from "./wake-runtime";
import { JarvisWakeLifecycle, type JarvisWakeSnapshot } from "./wake-state";
import { createUuid } from "../uuid";

export interface JarvisVoiceState {
  voice: JarvisVoiceSnapshot;
  wake: JarvisWakeSnapshot;
  wakeRuntime: JarvisWakeRuntimeSnapshot;
  /** Android's audio state; null in a browser. */
  phoneAudio: PhoneAudioState | null;
  /** Whether a wake model is installed (browser only). */
  wakeModelConfigured: boolean;
}

export interface JarvisVoiceOptions {
  /** The page's audio element for the remote Live stream (browser only). */
  remoteAudio: () => HTMLAudioElement | null;
  /** Raw provider events from the browser's own Live connection. */
  onProviderEvent: (event: Record<string, unknown>) => void;
  /** A background failure the page should show. */
  onError: (message: string) => void;
  /** Developer mode and the selected wake gate, read when a wake starts. */
  wakeGate: () => { developerMode: boolean; selected: JarvisWakeGateMode };
}

export interface JarvisVoiceSession {
  readonly kind: "phone" | "browser";
  readonly controllerId: string;
  current(): JarvisVoiceState;
  subscribe(listener: (state: JarvisVoiceState) => void): () => void;
  ownsSession(session: JarvisSession | null): boolean;
  /** Durable live events and stream health, for the transport's own bookkeeping. */
  recordLiveEvent(event: JarvisLiveEvent): void;
  recordStreamState(state: "opened" | "errored"): void;
  /** The user did something; follow-up listening stays open. */
  noteActivity(): void;
  /** Arm wake listening once the daily log exists. */
  armWake(conversationId: string | null): void;
  /** Start talking now. */
  listen(hasLog: boolean): Promise<void>;
  /** Stop talking (and close the provider session if nothing local is running). */
  sleep(session: JarvisSession | null): Promise<void>;
  /** Ask Android to listen for the wake word again (phone only). */
  wakePhone(): Promise<void>;
  /** Developer lab: claim the controller role under a new identity. */
  supersede(): Promise<void>;
  dispose(): void;
}

const IDLE_VOICE: JarvisVoiceSnapshot = { status: "idle", micEnabled: false, playbackActive: false, error: null };
const IDLE_WAKE: JarvisWakeSnapshot = { status: "idle", error: null, followUpUntil: null };
const IDLE_RUNTIME: JarvisWakeRuntimeSnapshot = { status: "idle", error: null };
const FOLLOW_UP_ACTIVITY_EVENTS = new Set([
  "input_audio_buffer.speech_started",
  "input_audio_buffer.speech_stopped",
  "conversation.item.input_audio_transcription.completed",
  "response.created",
  "response.done",
]);
const message = (cause: unknown, fallback: string) => (cause instanceof Error ? cause.message : fallback);

abstract class BaseVoiceSession {
  protected state: JarvisVoiceState;
  private readonly listeners = new Set<(state: JarvisVoiceState) => void>();
  protected disposed = false;

  constructor(initial: Partial<JarvisVoiceState>) {
    this.state = { voice: IDLE_VOICE, wake: IDLE_WAKE, wakeRuntime: IDLE_RUNTIME, phoneAudio: null, wakeModelConfigured: false, ...initial };
  }

  current(): JarvisVoiceState {
    return this.state;
  }

  subscribe(listener: (state: JarvisVoiceState) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  protected update(patch: Partial<JarvisVoiceState>): void {
    if (this.disposed) return;
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener(this.state));
  }

  protected closeListeners(): void {
    this.disposed = true;
    this.listeners.clear();
  }
}

/** Android owns audio; this page observes it and sends commands. */
class PhoneVoiceSession extends BaseVoiceSession implements JarvisVoiceSession {
  readonly kind = "phone" as const;
  readonly controllerId = "android";
  private readonly controller: JarvisPhoneVoiceController;

  constructor(bridge: NonNullable<ReturnType<typeof phoneVoiceBridge>>) {
    super({});
    this.controller = new JarvisPhoneVoiceController(bridge, (voice) => this.update({ voice }), (phoneAudio) => this.update({
      phoneAudio,
      wake: {
        status: phoneAudio.mode === "waiting" ? "armed" : phoneAudio.mode === "wake_starting" ? "arming" : "idle",
        error: phoneAudio.mode === "error" ? phoneAudio.text : null,
        followUpUntil: null,
      },
    }));
    this.state = { ...this.state, voice: this.controller.current() };
  }

  // A typed-bridge session is the text path; any surface may drive it.
  ownsSession(session: JarvisSession | null) { return this.controller.ownsSession(session) || session?.controller_id === "typed-bridge"; }
  recordLiveEvent() { /* Android keeps its own event connection. */ }
  recordStreamState() { /* Android keeps its own event connection. */ }
  noteActivity() { /* Android times follow-up listening. */ }
  armWake() { /* Android arms its own wake detector. */ }
  async listen() { await this.controller.startWithOptions(); }
  async sleep() { await this.controller.stop(); }
  async wakePhone() { await this.controller.wake(); }
  async supersede() { throw new Error("The developer lab runs in a browser"); }

  /** Observation only: the conversation keeps running in Android after navigation. */
  dispose() {
    this.closeListeners();
    this.controller.dispose();
  }
}

/** The browser owns the wake model, the Live transport and their teardown. */
class BrowserVoiceSession extends BaseVoiceSession implements JarvisVoiceSession {
  readonly kind = "browser" as const;
  controllerId = `jarvis-browser-${createUuid()}`;
  private readonly live: JarvisLiveController;
  private readonly lifecycle: JarvisWakeLifecycle;
  private readonly runtime: JarvisWakeRuntime;
  private readonly wakeModel = configuredJarvisWakeModel();
  private readonly cleanups: Array<() => void> = [];
  private telemetrySequence = 0;

  constructor(private readonly options: JarvisVoiceOptions) {
    super({ wakeModelConfigured: configuredJarvisWakeModel() !== null });
    this.live = new JarvisLiveController(this.controllerId, {
      onSnapshot: (voice) => this.update({ voice }),
      onRemoteStream: (stream) => this.playRemote(stream),
      playbackElement: options.remoteAudio,
      onProviderEvent: (event) => {
        const type = typeof event.type === "string" ? event.type : "";
        if (FOLLOW_UP_ACTIVITY_EVENTS.has(type) || type.startsWith("session.")) this.lifecycle.noteActivity();
        options.onProviderEvent(event);
      },
      onDiagnostic: (diagnostic) => this.telemetry(
        diagnostic.activationId ?? diagnostic.sessionId ?? `live-${this.controllerId.slice(-36)}-${diagnostic.attempt}`,
        `live.${diagnostic.event}`,
        { attempt: diagnostic.attempt, ...diagnostic.detail },
        { activation_id: diagnostic.activationId, session_id: diagnostic.sessionId, generation: diagnostic.generation,
          observed_at: diagnostic.observedAt },
      ),
    });
    this.lifecycle = new JarvisWakeLifecycle({
      onSnapshot: (wake) => this.update({ wake }),
      onRearmRequested: () => void this.rearm(),
    });
    this.runtime = new JarvisWakeRuntime({
      onTrace: (event, detail) => {
        if (["wake_runtime_starting", "wake_microphone_ready", "wake_runtime_failed"].includes(event)) {
          this.telemetry(`wake-${this.controllerId.slice(-36)}`, event, detail);
        }
      },
      onReady: (stream) => {
        void this.live.prepare(stream).catch((cause) => this.fail(cause, "Unable to prepare GPT-Live transport"));
      },
      onSnapshot: (wakeRuntime) => {
        this.update({ wakeRuntime });
        if (wakeRuntime.status === "ready" && this.lifecycle.current().status === "arming") this.lifecycle.armed();
        else if (wakeRuntime.status === "failed") this.lifecycle.fail(new Error(wakeRuntime.error || "Wake model unavailable"));
      },
      onWake: (handoff) => {
        if (this.disposed || this.lifecycle.current().status !== "armed") return;
        this.lifecycle.detected();
        this.lifecycle.connecting();
        const gate = options.wakeGate();
        const activationContext = handoff.wakeProbability === null ? undefined : {
          source: "wake_word",
          wake_score: handoff.wakeProbability,
          activation_id: handoff.activationId ?? createUuid(),
          gate_mode: resolveJarvisWakeGateMode(gate.developerMode, gate.selected),
        } as const;
        void this.live.startWithOptions({
          stream: handoff.localStream,
          activationContext,
          candidateAudio: handoff.candidateAudio,
          wakeDetectedMonotonicMs: handoff.wakeDetectedMonotonicMs,
        })
          .then((snapshot) => { if (!this.disposed && snapshot.status === "connected") this.lifecycle.connected(); })
          .catch((cause) => this.fail(cause, "Unable to connect after wake"));
      },
    });
    this.watchEnvironment();
  }

  ownsSession(session: JarvisSession | null) {
    return this.live.ownsSession(session) || session?.controller_id === this.controllerId || session?.controller_id === "typed-bridge";
  }

  recordLiveEvent(event: JarvisLiveEvent) { this.live.recordBackendEvent(event); }
  recordStreamState(state: "opened" | "errored") { this.live.recordLiveStreamState(state); }
  noteActivity() { this.lifecycle.noteActivity(); }

  armWake(conversationId: string | null) {
    if (!this.wakeModel || !conversationId || this.disposed || this.runtime.current().status !== "idle") return;
    this.lifecycle.beginArming();
    void this.runtime.start(this.wakeModel);
  }

  async listen(hasLog: boolean) {
    if (!hasLog) throw new Error("Send the first turn before arming Jarvis");
    if (this.runtime.current().status === "ready" && this.lifecycle.current().status === "armed") {
      this.lifecycle.detected();
      this.lifecycle.connecting();
      try {
        await this.live.startWithOptions({ stream: this.runtime.manualHandoff().localStream });
        this.lifecycle.connected();
      } catch (cause) {
        this.lifecycle.fail(cause);
        throw cause;
      }
      return;
    }
    await this.runtime.stop();
    this.lifecycle.reset();
    await this.live.startWithOptions();
  }

  async sleep(session: JarvisSession | null) {
    if (this.wakeModel && this.runtime.current().status === "triggered") this.lifecycle.sleep();
    else if (this.state.voice.status !== "idle") await this.live.stop();
    else if (session) await closeJarvisLiveSession(session.id, session.generation);
  }

  async wakePhone() { throw new Error("Only the Android app listens for the wake word in the background"); }

  async supersede() {
    this.controllerId = `jarvis-browser-${createUuid()}`;
    await createJarvisLiveCall(this.controllerId, "controller");
  }

  dispose() {
    this.closeListeners();
    this.cleanups.forEach((cleanup) => cleanup());
    this.lifecycle.destroy();
    void this.live.stop().finally(() => this.runtime.stop());
  }

  private async rearm() {
    try {
      if (!["idle", "closed"].includes(this.live.current().status)) await this.live.stop();
      await this.runtime.rearm();
      if (!this.disposed && this.lifecycle.current().status === "arming") this.lifecycle.armed();
    } catch (cause) {
      if (!this.disposed) this.lifecycle.fail(cause);
    }
  }

  private playRemote(stream: MediaStream | null) {
    const audio = this.options.remoteAudio();
    if (!audio) return;
    audio.srcObject = stream;
    if (stream) {
      audio.muted = false;
      void audio.play().catch(() => undefined);
    }
  }

  private fail(cause: unknown, fallback: string) {
    if (this.disposed) return;
    this.lifecycle.fail(cause);
    this.options.onError(message(cause, fallback));
  }

  /** A new microphone or network invalidates the prepared transport; refresh it while idle. */
  private watchEnvironment() {
    const onPageHide = () => { void this.live.stop(); };
    const onDeviceChange = () => {
      if (!this.wakeModel || this.runtime.current().status !== "ready" || !["idle", "closed"].includes(this.live.current().status)) return;
      void this.runtime.stop().then(() => this.runtime.start(this.wakeModel!))
        .catch((cause) => { if (!this.disposed) this.options.onError(message(cause, "Unable to refresh the microphone")); });
    };
    const onNetworkChange = () => {
      void this.live.refreshPreparation("network_change")
        .catch((cause) => { if (!this.disposed) this.options.onError(message(cause, "Unable to refresh GPT-Live transport")); });
    };
    const connection = (navigator as Navigator & {
      connection?: { addEventListener?: (type: string, listener: () => void) => void; removeEventListener?: (type: string, listener: () => void) => void };
    }).connection;
    window.addEventListener("pagehide", onPageHide);
    window.addEventListener("online", onNetworkChange);
    navigator.mediaDevices?.addEventListener?.("devicechange", onDeviceChange);
    connection?.addEventListener?.("change", onNetworkChange);
    this.cleanups.push(() => {
      window.removeEventListener("pagehide", onPageHide);
      window.removeEventListener("online", onNetworkChange);
      navigator.mediaDevices?.removeEventListener?.("devicechange", onDeviceChange);
      connection?.removeEventListener?.("change", onNetworkChange);
    });
  }

  /** Best effort, metadata only. */
  private telemetry(traceId: string, eventType: string, detail: Record<string, unknown> | undefined,
    extra: { activation_id?: string; session_id?: string; generation?: number; observed_at?: string } = {}) {
    void submitJarvisLiveTelemetry([{
      trace_id: traceId,
      client_sequence: ++this.telemetrySequence,
      source: "browser",
      event_type: eventType,
      observed_at: extra.observed_at ?? new Date().toISOString(),
      monotonic_ms: performance.now(),
      activation_id: extra.activation_id,
      session_id: extra.session_id,
      generation: extra.generation,
      detail,
    }]);
  }
}

/** The phone's native audio when this page runs in the Jarvis Android app, otherwise the browser's. */
export function createJarvisVoiceSession(options: JarvisVoiceOptions): JarvisVoiceSession {
  const bridge = phoneVoiceBridge();
  return bridge ? new PhoneVoiceSession(bridge) : new BrowserVoiceSession(options);
}
