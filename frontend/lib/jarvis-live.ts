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
  waitForBackendReady?: (session: JarvisSession, signal?: AbortSignal) => Promise<void>;
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
  onDiagnostic?: (diagnostic: JarvisLiveDiagnostic) => void;
}

export interface JarvisLiveDiagnostic {
  event: string;
  observedAt: string;
  attempt: number;
  activationId?: string;
  sessionId?: string;
  generation?: number;
  detail?: Record<string, unknown>;
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
  private attemptEpoch = 0;
  private closedBackendSessions = new Set<string>();
  private startupAbort: AbortController | null = null;
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
    const attempt = ++this.attemptEpoch;
    this.startupAbort?.abort();
    const startupAbort = new AbortController();
    this.startupAbort = startupAbort;
    const readiness = new LiveStartupReadiness();
    let acceptedSession: JarvisSession | null = null;
    const diagnose = (event: string, detail?: Record<string, unknown>) => {
      this.options.onDiagnostic?.({
        event,
        observedAt: new Date().toISOString(),
        attempt,
        ...(options.activationContext?.activation_id
          ? { activationId: options.activationContext.activation_id }
          : {}),
        ...(acceptedSession
          ? { sessionId: acceptedSession.id, generation: acceptedSession.generation }
          : {}),
        ...(detail ? { detail } : {}),
      });
    };
    const assertCurrent = () => {
      if (attempt !== this.attemptEpoch) throw new LiveStartupCancelledError();
    };
    this.publish({ ...this.snapshot, status: "connecting", error: null });
    try {
      const stream = options.stream ?? await this.deps.getUserMedia();
      assertCurrent();
      this.localStream = stream;
      this.ownsLocalStream = !options.stream;
      const activationBridge = options.candidateAudio
        ? await createLiveActivationBridge(options.candidateAudio, this.deps.audioContext)
        : null;
      this.activationBridge = activationBridge;
      const providerStream = activationBridge?.stream ?? stream;
      const pc = this.pc = this.deps.peerConnection();
      const observePeerState = () => {
        if (attempt !== this.attemptEpoch) return;
        const state = pc.connectionState;
        diagnose("peer_state", { state });
        if (state === "connected") readiness.markPeerConnected();
        if (state === "failed" || state === "closed") {
          readiness.fail(new Error(`GPT-Live WebRTC connection ${state} during startup`));
        }
      };
      const observeIceState = () => {
        if (attempt !== this.attemptEpoch) return;
        const state = pc.iceConnectionState;
        diagnose("ice_state", { state });
        if (state === "failed" || state === "closed") {
          readiness.fail(new Error(`GPT-Live ICE connection ${state} during startup`));
        }
      };
      pc.addEventListener?.("connectionstatechange", observePeerState);
      pc.addEventListener?.("iceconnectionstatechange", observeIceState);
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
      const observeEventsOpen = () => {
        if (attempt !== this.attemptEpoch) return;
        diagnose("data_channel_open");
        readiness.markDataChannelOpen();
      };
      events.onopen = observeEventsOpen;
      events.onclose = () => {
        if (attempt !== this.attemptEpoch) return;
        diagnose("data_channel_closed");
        readiness.fail(new Error("GPT-Live events channel closed during startup"));
      };
      events.onmessage = (message) => {
        let event: Record<string, unknown>;
        try {
          event = JSON.parse(String(message.data)) as Record<string, unknown>;
        } catch {
          return;
        }
        const type = typeof event.type === "string" ? event.type : "";
        // A stop invalidates the startup attempt before waiting for this ack.
        if (type === "session.closed") this.closeWaiter?.();
        if (attempt !== this.attemptEpoch) return;
        this.options.onProviderEvent?.(event);
        if (type === "session.started") {
          diagnose("browser_session_started");
          readiness.markBrowserSessionStarted();
        }
        if (type === "response.event") this.trackDelegation(event);
        if (isMeaningfulLiveEvent(event)) this.touch();
      };
      events.onerror = () => {
        if (attempt !== this.attemptEpoch) return;
        diagnose("data_channel_error");
        readiness.fail(new Error("GPT-Live events channel failed during startup"));
      };
      if (events.readyState === "open") observeEventsOpen();

      const offer = await pc.createOffer();
      assertCurrent();
      await pc.setLocalDescription(offer);
      assertCurrent();
      await waitForIceGathering(pc);
      assertCurrent();
      const localOffer = pc.localDescription?.sdp ?? offer.sdp ?? "";
      const response = await this.deps.createCall(
        this.controllerId,
        "controller",
        "openai_live",
        localOffer,
        options.activationContext,
      );
      const responseSession = response.session;
      const sdpAnswer = response.sdp_answer;
      acceptedSession = responseSession;
      if (acceptedSession) this.session = acceptedSession;
      if (!sdpAnswer || !responseSession) {
        throw new Error("GPT-Live backend returned incomplete connection material");
      }
      if (attempt !== this.attemptEpoch) {
        await this.closeBackendSession(responseSession);
        throw new LiveStartupCancelledError();
      }
      // Retain the accepted identity before either readiness wait so every
      // failure/cancellation closes this exact backend attempt.
      void this.deps.waitForBackendReady(responseSession, startupAbort.signal).then(
        () => {
          if (attempt !== this.attemptEpoch) return;
          diagnose("backend_ready");
          readiness.markBackendReady();
        },
        (cause) => {
          if (attempt !== this.attemptEpoch) return;
          const error = cause instanceof Error ? cause : new Error("GPT-Live backend readiness failed");
          diagnose("backend_readiness_failed", { message: error.message });
          readiness.fail(error);
        },
      );
      await withTimeout(
        (async () => {
          await pc.setRemoteDescription({ type: "answer", sdp: sdpAnswer });
          assertCurrent();
          diagnose("sdp_applied");
          readiness.markSdpApplied();
          observePeerState();
          observeIceState();
          if (events.readyState === "open") observeEventsOpen();
          await readiness.ready;
          // Backend readiness may precede a slow WebRTC connection. Re-read it
          // at the combined boundary so a failed sideband cannot be mistaken
          // for a healthy session merely because it was healthy earlier.
          await this.deps.waitForBackendReady(responseSession, startupAbort.signal);
          diagnose("backend_health_confirmed");
        })(),
        START_TIMEOUT_MS,
        this.deps,
        () => {
          const missing = readiness.missing();
          return new Error(
            `GPT-Live startup timed out; missing ${(
              missing.length ? missing : ["backend sideband health confirmation"]
            ).join(", ")}`,
          );
        },
      );
      assertCurrent();
      await activationBridge?.start();
      diagnose("activation_audio_released", {
        buffered: Boolean(activationBridge),
        browser_session_started: readiness.browserSessionStarted,
      });
      this.publish({ ...this.snapshot, status: "connected", micEnabled: true });
      this.touch();
      return this.snapshot;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "GPT-Live connection failed";
      diagnose("startup_failed", { message, missing: readiness.missing() });
      if (attempt === this.attemptEpoch) {
        this.attemptEpoch += 1;
        startupAbort.abort();
        await this.dispose(true);
        this.publish({ ...this.snapshot, status: "error", error: message });
      } else if (acceptedSession) {
        await this.closeBackendSession(acceptedSession);
      }
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
    this.attemptEpoch += 1;
    this.startupAbort?.abort();
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
      await this.closeBackendSession(session);
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
    this.startupAbort = null;
  }

  private async closeBackendSession(session: JarvisSession): Promise<void> {
    const key = `${session.id}:${session.generation}`;
    if (this.closedBackendSessions.has(key)) return;
    this.closedBackendSessions.add(key);
    await this.deps.closeCall(session.id, session.generation).catch(() => undefined);
  }

  private publish(snapshot: JarvisVoiceSnapshot): void {
    this.snapshot = snapshot;
    this.options.onSnapshot?.(snapshot);
    this.listeners.forEach((listener) => listener(snapshot));
  }
}

class LiveStartupCancelledError extends Error {
  constructor() {
    super("GPT-Live startup was cancelled");
  }
}

class LiveStartupReadiness {
  private sdpApplied = false;
  private peerConnected = false;
  private dataChannelOpen = false;
  private backendReady = false;
  private settled = false;
  private readonly resolveReady: () => void;
  private readonly rejectReady: (reason: Error) => void;
  readonly ready: Promise<void>;
  browserSessionStarted = false;

  constructor() {
    let resolveReady!: () => void;
    let rejectReady!: (reason: Error) => void;
    this.ready = new Promise<void>((resolve, reject) => {
      resolveReady = resolve;
      rejectReady = reject;
    });
    // Transport failure can precede the HTTP call response; retain the
    // rejection for the eventual bounded wait without an unhandled promise.
    void this.ready.catch(() => undefined);
    this.resolveReady = resolveReady;
    this.rejectReady = rejectReady;
  }

  markSdpApplied(): void { this.sdpApplied = true; this.reconcile(); }
  markPeerConnected(): void { this.peerConnected = true; this.reconcile(); }
  markDataChannelOpen(): void { this.dataChannelOpen = true; this.reconcile(); }
  markBackendReady(): void { this.backendReady = true; this.reconcile(); }
  markBrowserSessionStarted(): void { this.browserSessionStarted = true; }

  fail(error: Error): void {
    if (this.settled) return;
    this.settled = true;
    this.rejectReady(error);
  }

  missing(): string[] {
    return [
      ...(!this.sdpApplied ? ["SDP answer application"] : []),
      ...(!this.peerConnected ? ["WebRTC connected"] : []),
      ...(!this.dataChannelOpen ? ["oai-events open"] : []),
      ...(!this.backendReady ? ["backend sideband ready"] : []),
    ];
  }

  private reconcile(): void {
    if (this.settled || this.missing().length > 0) return;
    this.settled = true;
    this.resolveReady();
  }
}

interface LiveActivationBridge {
  stream: MediaStream;
  start(): Promise<void>;
  destroy(): Promise<void>;
}

const MAX_ACTIVATION_REPLAY_SAMPLES = 16_000 * 30;
const ACTIVATION_TARGET_BACKLOG_MS = 250;
const ACTIVATION_RETAINED_SILENCE_MS = 500;
const CONFIDENT_SILENCE_PEAK = 0.0015;
const CONFIDENT_SILENCE_RMS = 0.00075;

class ActivationSilenceCompactor {
  private retainedSilenceSamples = 0;

  constructor(private readonly sampleRate: number) {}

  compact(samples: Float32Array, pressureSamples: number): Float32Array[] {
    const frameSamples = Math.max(1, Math.round(this.sampleRate * 0.02));
    const targetSamples = Math.round(this.sampleRate * ACTIVATION_TARGET_BACKLOG_MS / 1_000);
    const retainedSamples = Math.round(this.sampleRate * ACTIVATION_RETAINED_SILENCE_MS / 1_000);
    const output: Float32Array[] = [];
    for (let offset = 0; offset < samples.length; offset += frameSamples) {
      const frame = samples.slice(offset, Math.min(samples.length, offset + frameSamples));
      if (!isConfidentSilence(frame)) {
        this.retainedSilenceSamples = 0;
        output.push(frame);
        continue;
      }
      const required = Math.max(0, retainedSamples - this.retainedSilenceSamples);
      if (required > 0) {
        const keep = Math.min(required, frame.length);
        output.push(frame.slice(0, keep));
        this.retainedSilenceSamples += keep;
        if (keep < frame.length && pressureSamples <= targetSamples) {
          output.push(frame.slice(keep));
        }
        continue;
      }
      if (pressureSamples <= targetSamples) output.push(frame);
    }
    samples.fill(0);
    return output;
  }
}

function isConfidentSilence(samples: Float32Array): boolean {
  if (samples.length === 0) return true;
  let peak = 0;
  let squareSum = 0;
  for (const sample of samples) {
    const magnitude = Math.abs(sample);
    peak = Math.max(peak, magnitude);
    squareSum += sample * sample;
  }
  return peak <= CONFIDENT_SILENCE_PEAK
    && Math.sqrt(squareSum / samples.length) <= CONFIDENT_SILENCE_RMS;
}

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
  const compactor = new ActivationSilenceCompactor(candidate.sampleRate);
  const failOverflow = (samples?: Float32Array) => {
    overflow = true;
    samples?.fill(0);
    candidate.close("audio_backlog_overflow");
  };
  const scheduledBacklogSamples = () => Math.max(
    0,
    Math.round((scheduledAt - context.currentTime) * candidate.sampleRate),
  );
  const scheduleFrame = (samples: Float32Array) => {
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
  const schedule = (samples: Float32Array, pressureSamples = scheduledBacklogSamples()) => {
    for (const frame of compactor.compact(samples, pressureSamples)) scheduleFrame(frame);
  };
  const unsubscribe = candidate.subscribe((chunk) => {
    if (destroyed || overflow) {
      chunk.samples.fill(0);
      return;
    }
    if (started) {
      schedule(chunk.samples, scheduledBacklogSamples());
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
        schedule(samples, Math.max(queuedSamples + samples.length, scheduledBacklogSamples()));
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
  timeoutError: () => Error = () => new Error("GPT-Live session startup timed out"),
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = deps.setTimeout(() => reject(timeoutError()), timeoutMs);
    void promise.then(
      (value) => { deps.clearTimeout(timer); resolve(value); },
      (reason) => { deps.clearTimeout(timer); reject(reason); },
    );
  });
}

async function waitForBackendReady(session: JarvisSession, signal?: AbortSignal): Promise<void> {
  while (!signal?.aborted) {
    const state = await getJarvisState();
    if (
      state.session?.id === session.id
      && state.session.generation === session.generation
      && state.session.lifecycle_state === "ready"
    ) return;
    if (state.session?.id === session.id && state.session.lifecycle_state === "disconnected") {
      throw new Error("GPT-Live backend sideband disconnected during startup");
    }
    if (
      state.session
      && (state.session.id !== session.id || state.session.generation !== session.generation)
    ) {
      throw new Error("GPT-Live session was replaced during startup");
    }
    await new Promise((resolve) => globalThis.setTimeout(resolve, 100));
  }
  throw new LiveStartupCancelledError();
}
