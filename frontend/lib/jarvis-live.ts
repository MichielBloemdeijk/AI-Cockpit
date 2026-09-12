import {
  closeJarvisRealtimeSession,
  createJarvisRealtimeCall,
  getJarvisState,
  type JarvisRealtimeActivationContext,
  type JarvisRealtimeCallResponse,
  type JarvisSession,
} from "./api";
import type { JarvisVoiceClient, JarvisVoiceSnapshot } from "./jarvis-voice-client";
import type { JarvisCandidateAudio } from "./jarvis-activation-audio";

export interface JarvisLiveStartOptions {
  stream?: MediaStream;
  activationContext?: JarvisRealtimeActivationContext;
  candidateAudio?: JarvisCandidateAudio;
}

export interface JarvisLiveDependencies {
  createCall?: (
    controllerId: string,
    role: "controller",
    adapter: "openai_live",
    sdpOffer: string,
    activationContext?: JarvisRealtimeActivationContext,
  ) => Promise<JarvisRealtimeCallResponse>;
  closeCall?: (sessionId: string, generation: number) => Promise<unknown>;
  getUserMedia?: () => Promise<MediaStream>;
  waitForBackendReady?: (session: JarvisSession) => Promise<void>;
  peerConnection?: () => RTCPeerConnection;
  audio?: () => HTMLAudioElement;
  audioContext?: () => AudioContext;
  setTimeout?: typeof globalThis.setTimeout;
  clearTimeout?: typeof globalThis.clearTimeout;
}

export interface JarvisLiveControllerOptions {
  onSnapshot?: (snapshot: JarvisVoiceSnapshot) => void;
  onRemoteStream?: (stream: MediaStream | null) => void;
  onProviderEvent?: (event: Record<string, unknown>) => void;
}

const IDLE_CLOSE_MS = 60_000;
const START_TIMEOUT_MS = 15_000;

export class JarvisLiveController implements JarvisVoiceClient {
  private readonly deps: Required<JarvisLiveDependencies>;
  private readonly controllerId: string;
  private readonly options: JarvisLiveControllerOptions;
  private pc: RTCPeerConnection | null = null;
  private events: RTCDataChannel | null = null;
  private localStream: MediaStream | null = null;
  private ownsLocalStream = false;
  private remoteAudio: HTMLAudioElement | null = null;
  private activationBridge: LiveActivationBridge | null = null;
  private session: JarvisSession | null = null;
  private idleTimer: ReturnType<typeof globalThis.setTimeout> | null = null;
  private pendingDelegations = new Set<string>();
  private closeWaiter: (() => void) | null = null;
  private snapshot: JarvisVoiceSnapshot = {
    status: "idle",
    engine: "openai_live",
    micEnabled: false,
    playbackActive: false,
    error: null,
  };
  private listeners = new Set<(snapshot: JarvisVoiceSnapshot) => void>();

  constructor(
    controllerId: string,
    options: JarvisLiveControllerOptions = {},
    dependencies: JarvisLiveDependencies = {},
  ) {
    this.controllerId = controllerId;
    this.options = options;
    this.deps = {
      createCall: dependencies.createCall ?? ((id, role, adapter, offer, activation) => (
        createJarvisRealtimeCall(id, role, adapter, offer, undefined, activation)
      )),
      closeCall: dependencies.closeCall ?? closeJarvisRealtimeSession,
      getUserMedia: dependencies.getUserMedia ?? (() => navigator.mediaDevices.getUserMedia({ audio: true })),
      waitForBackendReady: dependencies.waitForBackendReady ?? waitForBackendReady,
      peerConnection: dependencies.peerConnection ?? (() => new RTCPeerConnection()),
      audio: dependencies.audio ?? (() => document.createElement("audio")),
      audioContext: dependencies.audioContext ?? (() => new AudioContext()),
      setTimeout: dependencies.setTimeout ?? globalThis.setTimeout,
      clearTimeout: dependencies.clearTimeout ?? globalThis.clearTimeout,
    };
  }

  current(): JarvisVoiceSnapshot {
    return this.snapshot;
  }

  onSnapshot(listener: (snapshot: JarvisVoiceSnapshot) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  start(stream?: MediaStream): Promise<JarvisVoiceSnapshot> {
    return this.startWithOptions({ stream });
  }

  async startWithOptions(options: JarvisLiveStartOptions = {}): Promise<JarvisVoiceSnapshot> {
    if (this.snapshot.status === "connected" || this.snapshot.status === "connecting") {
      return this.snapshot;
    }
    this.publish({ ...this.snapshot, status: "connecting", error: null });
    let startedResolve: (() => void) | null = null;
    let startedReject: ((reason: Error) => void) | null = null;
    const started = new Promise<void>((resolve, reject) => {
      startedResolve = resolve;
      startedReject = reject;
    });
    try {
      const stream = options.stream ?? await this.deps.getUserMedia();
      this.localStream = stream;
      this.ownsLocalStream = !options.stream;
      const activationBridge = options.candidateAudio
        ? await createLiveActivationBridge(options.candidateAudio, this.deps.audioContext)
        : null;
      this.activationBridge = activationBridge;
      const providerStream = activationBridge?.stream ?? stream;
      const pc = this.pc = this.deps.peerConnection();
      providerStream.getAudioTracks().forEach((track) => pc.addTrack(track, providerStream));
      pc.ontrack = (event) => {
        const remote = event.streams[0] ?? new MediaStream([event.track]);
        this.options.onRemoteStream?.(remote);
        if (!this.options.onRemoteStream) {
          const audio = this.remoteAudio = this.deps.audio();
          audio.autoplay = true;
          audio.srcObject = remote;
          void audio.play().catch(() => undefined);
        }
        this.publish({ ...this.snapshot, playbackActive: true });
      };

      // GPT-Live requires oai-events to be negotiated in the original offer.
      const events = this.events = pc.createDataChannel("oai-events");
      events.onmessage = (message) => {
        let event: Record<string, unknown>;
        try {
          event = JSON.parse(String(message.data)) as Record<string, unknown>;
        } catch {
          return;
        }
        this.options.onProviderEvent?.(event);
        const type = typeof event.type === "string" ? event.type : "";
        if (type === "session.started") startedResolve?.();
        if (type === "session.closed") this.closeWaiter?.();
        if (type === "response.event") this.trackDelegation(event);
        if (isMeaningfulLiveEvent(event)) this.touch();
      };
      events.onerror = () => startedReject?.(new Error("GPT-Live events channel failed"));

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await waitForIceGathering(pc);
      const localOffer = pc.localDescription?.sdp ?? offer.sdp ?? "";
      const response = await this.deps.createCall(
        this.controllerId,
        "controller",
        "openai_live",
        localOffer,
        options.activationContext,
      );
      if (!response.sdp_answer || !response.session) {
        throw new Error("GPT-Live backend returned incomplete connection material");
      }
      this.session = response.session;
      await pc.setRemoteDescription({ type: "answer", sdp: response.sdp_answer });
      await withTimeout(started, START_TIMEOUT_MS, this.deps);
      await this.deps.waitForBackendReady(response.session);
      await activationBridge?.start();
      this.publish({ ...this.snapshot, status: "connected", micEnabled: true });
      this.touch();
      return this.snapshot;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "GPT-Live connection failed";
      await this.dispose(false);
      this.publish({ ...this.snapshot, status: "error", error: message });
      throw cause;
    }
  }

  interrupt(): void {
    // Speaking over Live is the interruption signal; do not send Realtime's
    // response.cancel event, which is not part of the Live protocol.
    this.publish({ ...this.snapshot, status: "interrupted", playbackActive: false });
    this.touch();
  }

  async stop(): Promise<void> {
    const channel = this.events;
    if (channel?.readyState === "open") {
      channel.send(JSON.stringify({ type: "session.close" }));
      await withTimeout(
        new Promise<void>((resolve) => { this.closeWaiter = resolve; }),
        2_000,
        this.deps,
      ).catch(() => undefined);
    }
    await this.dispose(true);
    this.publish({ ...this.snapshot, status: "closed", micEnabled: false, playbackActive: false });
  }

  private trackDelegation(event: Record<string, unknown>): void {
    const delegationId = typeof event.delegation_id === "string" ? event.delegation_id : "";
    const nested = event.event && typeof event.event === "object"
      ? event.event as Record<string, unknown>
      : null;
    const nestedType = typeof nested?.type === "string" ? nested.type : "";
    if (!delegationId) return;
    if (nestedType === "response.created") this.pendingDelegations.add(delegationId);
    if (["response.completed", "response.failed", "response.cancelled"].includes(nestedType)) {
      this.pendingDelegations.delete(delegationId);
    }
  }

  private touch(): void {
    if (this.idleTimer) this.deps.clearTimeout(this.idleTimer);
    this.idleTimer = null;
    if (this.pendingDelegations.size > 0 || this.snapshot.status !== "connected") return;
    this.idleTimer = this.deps.setTimeout(() => {
      if (this.pendingDelegations.size === 0 && this.snapshot.status === "connected") void this.stop();
    }, IDLE_CLOSE_MS);
  }

  private async dispose(closeBackend: boolean): Promise<void> {
    if (this.idleTimer) this.deps.clearTimeout(this.idleTimer);
    this.idleTimer = null;
    this.closeWaiter = null;
    const session = this.session;
    this.session = null;
    if (closeBackend && session) {
      await this.deps.closeCall(session.id, session.generation).catch(() => undefined);
    }
    this.events?.close();
    this.pc?.close();
    if (this.ownsLocalStream) this.localStream?.getTracks().forEach((track) => track.stop());
    this.options.onRemoteStream?.(null);
    this.remoteAudio?.pause();
    if (this.remoteAudio) this.remoteAudio.srcObject = null;
    this.events = null;
    this.pc = null;
    this.localStream = null;
    this.remoteAudio = null;
    await this.activationBridge?.destroy();
    this.activationBridge = null;
    this.ownsLocalStream = false;
    this.pendingDelegations.clear();
  }

  private publish(snapshot: JarvisVoiceSnapshot): void {
    this.snapshot = snapshot;
    this.options.onSnapshot?.(snapshot);
    this.listeners.forEach((listener) => listener(snapshot));
  }
}

interface LiveActivationBridge {
  stream: MediaStream;
  start(): Promise<void>;
  destroy(): Promise<void>;
}

const MAX_ACTIVATION_REPLAY_SAMPLES = 16_000 * 30;

async function createLiveActivationBridge(
  candidate: JarvisCandidateAudio,
  createAudioContext: () => AudioContext,
): Promise<LiveActivationBridge> {
  const context = createAudioContext();
  const destination = context.createMediaStreamDestination();
  const queued: Float32Array[] = [];
  const replaySources = new Set<AudioBufferSourceNode>();
  let queuedSamples = 0;
  let scheduledAt = 0;
  let started = false;
  let overflow = false;
  let destroyed = false;
  const failOverflow = (samples?: Float32Array) => {
    overflow = true;
    samples?.fill(0);
    candidate.close("audio_backlog_overflow");
  };
  const schedule = (samples: Float32Array) => {
    if (destroyed || overflow) {
      samples.fill(0);
      return;
    }
    const startAt = Math.max(scheduledAt, context.currentTime + 0.03);
    const endAt = startAt + samples.length / candidate.sampleRate;
    if ((endAt - context.currentTime) * candidate.sampleRate > MAX_ACTIVATION_REPLAY_SAMPLES) {
      failOverflow(samples);
      return;
    }
    const buffer = context.createBuffer(1, samples.length, candidate.sampleRate);
    buffer.getChannelData(0).set(samples);
    samples.fill(0);
    const source = context.createBufferSource();
    replaySources.add(source);
    source.buffer = buffer;
    source.connect(destination);
    source.onended = () => {
      replaySources.delete(source);
      source.disconnect();
    };
    source.start(startAt);
    scheduledAt = endAt;
  };
  const unsubscribe = candidate.subscribe((chunk) => {
    if (destroyed || overflow) {
      chunk.samples.fill(0);
      return;
    }
    if (started) {
      schedule(chunk.samples);
      return;
    }
    queuedSamples += chunk.samples.length;
    if (queuedSamples > MAX_ACTIVATION_REPLAY_SAMPLES) {
      failOverflow(chunk.samples);
      return;
    }
    queued.push(chunk.samples.slice());
    chunk.samples.fill(0);
  });
  return {
    stream: destination.stream,
    start: async () => {
      if (destroyed) throw new Error("GPT-Live activation bridge is closed");
      if (overflow) {
        throw new Error("GPT-Live activation audio exceeded the 30-second handoff limit; please retry");
      }
      if (context.state === "suspended") await context.resume();
      started = true;
      candidate.promoteToConversation();
      for (const samples of queued) {
        queuedSamples -= samples.length;
        schedule(samples);
      }
      queued.length = 0;
      if (overflow) {
        throw new Error("GPT-Live activation audio exceeded the 30-second handoff limit; please retry");
      }
    },
    destroy: async () => {
      if (destroyed) return;
      destroyed = true;
      unsubscribe();
      for (const samples of queued) samples.fill(0);
      queued.length = 0;
      for (const source of replaySources) {
        try { source.stop(); } catch { /* Already stopped. */ }
        source.disconnect();
      }
      replaySources.clear();
      destination.stream.getTracks().forEach((track) => track.stop());
      await context.close();
    },
  };
}

function isMeaningfulLiveEvent(event: Record<string, unknown>): boolean {
  const type = typeof event.type === "string" ? event.type : "";
  if (type === "session.input_transcript.delta" || type === "session.output_transcript.delta") {
    return typeof event.delta === "string" && event.delta.length > 0;
  }
  if (type === "session.commentary.appended") return true;
  if (type !== "response.event") return false;
  const nested = event.event && typeof event.event === "object"
    ? event.event as Record<string, unknown>
    : null;
  return typeof nested?.type === "string" && nested.type.length > 0;
}

async function waitForIceGathering(pc: RTCPeerConnection): Promise<void> {
  if (!pc.addEventListener || pc.iceGatheringState === "complete") return;
  await new Promise<void>((resolve) => {
    const timeout = globalThis.setTimeout(resolve, 750);
    const listener = () => {
      if (pc.iceGatheringState !== "complete") return;
      globalThis.clearTimeout(timeout);
      pc.removeEventListener("icegatheringstatechange", listener);
      resolve();
    };
    pc.addEventListener("icegatheringstatechange", listener);
  });
}

async function withTimeout<T>(
  promise: Promise<T>,
  timeoutMs: number,
  deps: Pick<Required<JarvisLiveDependencies>, "setTimeout" | "clearTimeout">,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = deps.setTimeout(() => reject(new Error("GPT-Live session startup timed out")), timeoutMs);
    void promise.then(
      (value) => { deps.clearTimeout(timer); resolve(value); },
      (reason) => { deps.clearTimeout(timer); reject(reason); },
    );
  });
}

async function waitForBackendReady(session: JarvisSession): Promise<void> {
  const deadline = Date.now() + 5_000;
  while (Date.now() < deadline) {
    const state = await getJarvisState();
    if (
      state.session?.id === session.id
      && state.session.generation === session.generation
      && state.session.lifecycle_state === "ready"
    ) return;
    if (state.session?.id === session.id && state.session.lifecycle_state === "disconnected") {
      throw new Error("GPT-Live backend sideband disconnected during startup");
    }
    await new Promise((resolve) => globalThis.setTimeout(resolve, 100));
  }
  throw new Error("GPT-Live backend sideband did not become ready");
}
