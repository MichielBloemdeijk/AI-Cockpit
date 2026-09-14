import {
  closeJarvisLiveSession,
  createJarvisLiveCall,
  getJarvisState,
  type JarvisLiveEvent,
  type JarvisStateResponse,
  type JarvisLiveActivationContext,
  type JarvisLiveCallResponse,
  type JarvisSession,
} from "./api";
import type { JarvisVoiceClient, JarvisVoiceSnapshot } from "./jarvis-voice-client";
import type { JarvisCandidateAudio } from "./jarvis-activation-audio";

export interface JarvisLiveStartOptions {
  stream?: MediaStream;
  activationContext?: JarvisLiveActivationContext;
  candidateAudio?: JarvisCandidateAudio;
  wakeDetectedMonotonicMs?: number;
}

export interface JarvisLiveDependencies {
  createCall?: (
    controllerId: string,
    role: "controller",
    adapter: "openai_live",
    sdpOffer: string,
    activationContext?: JarvisLiveActivationContext,
  ) => Promise<JarvisLiveCallResponse>;
  closeCall?: (sessionId: string, generation: number) => Promise<unknown>;
  getUserMedia?: () => Promise<MediaStream>;
  getBackendState?: () => Promise<JarvisStateResponse>;
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

type BackendReadinessState = "ready" | "failed";

interface BackendReadinessRecord {
  state: BackendReadinessState;
  message?: string;
}

interface PreparedLiveTransport {
  stream: MediaStream;
  ownsStream: boolean;
  pc: RTCPeerConnection;
  events: RTCDataChannel;
  activationBridge: LiveActivationBridge;
  localOffer: string;
  peerConnected: boolean;
  dataChannelOpen: boolean;
  claimed: boolean;
  prepareEpoch: number;
  cleanups: Array<() => void>;
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
  private prepared: PreparedLiveTransport | null = null;
  private preparation: Promise<PreparedLiveTransport> | null = null;
  private preparationStream: MediaStream | null = null;
  private preparationEpoch = 0;
  private activeReadiness: LiveStartupReadiness | null = null;
  private activeDiagnose: ((event: string, detail?: Record<string, unknown>) => void) | null = null;
  private backendStreamOpen = false;
  private backendRecords = new Map<string, BackendReadinessRecord>();
  private backendWaiters = new Map<string, Set<(record: BackendReadinessRecord) => void>>();
  private backendObserverCleanup: (() => void) | null = null;
  private snapshot: JarvisVoiceSnapshot = {
    status: "idle",
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
        createJarvisLiveCall(id, role, adapter, offer, undefined, activation)
      )),
      closeCall: dependencies.closeCall ?? closeJarvisLiveSession,
      getUserMedia: dependencies.getUserMedia ?? (() => navigator.mediaDevices.getUserMedia({ audio: true })),
      getBackendState: dependencies.getBackendState ?? getJarvisState,
      peerConnection: dependencies.peerConnection ?? (() => new RTCPeerConnection()),
      audio: dependencies.audio ?? (() => document.createElement("audio")),
      audioContext: dependencies.audioContext ?? (() => new AudioContext()),
      // Window timers are Web-IDL methods in browsers and must retain their
      // native receiver. Node's timers are permissive, which can hide this
      // failure in unit tests.
      setTimeout: dependencies.setTimeout ?? globalThis.setTimeout.bind(globalThis),
      clearTimeout: dependencies.clearTimeout ?? globalThis.clearTimeout.bind(globalThis),
    };
  }

  current(): JarvisVoiceSnapshot {
    return this.snapshot;
  }

  onSnapshot(listener: (snapshot: JarvisVoiceSnapshot) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Build every local WebRTC/audio primitive while wake listening is armed. */
  async prepare(stream: MediaStream, reason = "wake_armed"): Promise<void> {
    if (["connecting", "connected", "interrupted"].includes(this.snapshot.status)) return;
    if (this.prepared?.stream === stream && !this.prepared.claimed) return;
    if (this.preparation && this.preparationStream === stream) {
      await this.preparation;
      return;
    }
    const prepareEpoch = ++this.preparationEpoch;
    const startedAt = monotonicNow();
    this.emitPreparationDiagnostic("transport_preparation_started", { reason });
    await this.discardPreparedTransport();
    this.preparationStream = stream;
    const pending = this.buildPreparedTransport(stream, false, prepareEpoch);
    this.preparation = pending;
    try {
      const prepared = await pending;
      if (prepareEpoch !== this.preparationEpoch) {
        await destroyPreparedTransport(prepared);
        return;
      }
      this.prepared = prepared;
      this.emitPreparationDiagnostic("transport_preparation_ready", {
        reason,
        preparation_ms: roundMs(monotonicNow() - startedAt),
        offer_bytes: prepared.localOffer.length,
      });
    } finally {
      if (this.preparation === pending) {
        this.preparation = null;
        this.preparationStream = null;
      }
    }
  }

  async refreshPreparation(reason: "microphone_change" | "network_change"): Promise<void> {
    const stream = this.prepared?.stream ?? this.preparationStream;
    if (!stream || ["connecting", "connected", "interrupted"].includes(this.snapshot.status)) return;
    ++this.preparationEpoch;
    this.preparation = null;
    this.preparationStream = null;
    await this.discardPreparedTransport();
    await this.prepare(stream, reason);
  }

  recordBackendEvent(event: JarvisLiveEvent): boolean {
    if (event.source_domain !== "session") return false;
    const payload = event.payload ?? {};
    const sessionId = String(payload.session_id ?? event.source_id ?? "");
    const payloadGeneration = Number(payload.generation ?? 0);
    const generation = payloadGeneration > 0
      ? payloadGeneration
      : this.session?.id === sessionId
        ? this.session.generation
        : 0;
    if (!sessionId || !Number.isFinite(generation) || generation <= 0) return false;
    let record: BackendReadinessRecord | null = null;
    if (event.event_type === "provider.ready") {
      record = { state: "ready" };
    } else if ([
      "provider.disconnected",
      "provider.provider_error",
      "session.disconnected",
      "session.superseded",
      "session.connection_failed",
    ].includes(event.event_type)) {
      record = {
        state: "failed",
        message: event.event_type === "session.superseded"
          ? "GPT-Live session was replaced during startup"
          : "GPT-Live backend sideband disconnected during startup",
      };
    }
    if (!record) return false;
    this.publishBackendRecord(sessionId, generation, record);
    return true;
  }

  recordLiveStreamState(state: "opened" | "errored"): void {
    this.backendStreamOpen = state === "opened";
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
      let transport = this.prepared?.stream === stream ? this.prepared : null;
      if (!transport && this.preparation && this.preparationStream === stream) {
        transport = await this.preparation;
      }
      if (transport && transport.prepareEpoch !== this.preparationEpoch) transport = null;
      const reusedPreparation = Boolean(transport && !transport.claimed);
      transport ??= await this.buildPreparedTransport(stream, !options.stream, ++this.preparationEpoch);
      assertCurrent();
      transport.claimed = true;
      if (this.prepared === transport) this.prepared = null;
      this.pc = transport.pc;
      this.events = transport.events;
      this.localStream = stream;
      this.ownsLocalStream = transport.ownsStream;
      this.activationBridge = transport.activationBridge;
      this.activeReadiness = readiness;
      this.activeDiagnose = diagnose;
      const bufferStart = transport.activationBridge.attach(options.candidateAudio);
      diagnose("activation_audio_buffering", bufferStart);
      if (transport.peerConnected) readiness.markPeerConnected();
      if (transport.dataChannelOpen || transport.events.readyState === "open") readiness.markDataChannelOpen();
      diagnose(reusedPreparation ? "transport_prepared_reused" : "transport_prepared_on_demand", {
        wake_to_transport_claim_ms: options.wakeDetectedMonotonicMs === undefined
          ? undefined
          : roundMs(monotonicNow() - options.wakeDetectedMonotonicMs),
      });
      const pc = transport.pc;
      const requestStartedAt = monotonicNow();
      diagnose("connection_request_started", {
        wake_to_request_ms: options.wakeDetectedMonotonicMs === undefined
          ? undefined
          : roundMs(requestStartedAt - options.wakeDetectedMonotonicMs),
      });
      const response = await this.deps.createCall(
        this.controllerId,
        "controller",
        "openai_live",
        transport.localOffer,
        options.activationContext,
      );
      diagnose("connection_material_received", {
        request_ms: roundMs(monotonicNow() - requestStartedAt),
      });
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
      this.backendObserverCleanup = this.observeBackendReadiness(
        responseSession,
        readiness,
        diagnose,
        startupAbort.signal,
        attempt,
      );
      await withTimeout(
        (async () => {
          await pc.setRemoteDescription({ type: "answer", sdp: sdpAnswer });
          assertCurrent();
          diagnose("sdp_applied");
          readiness.markSdpApplied();
          this.observePreparedTransportState(transport);
          await readiness.ready;
        })(),
        START_TIMEOUT_MS,
        this.deps,
        () => {
          const missing = readiness.missing();
          return new Error(
            `GPT-Live startup timed out; missing ${(missing.length ? missing : ["readiness signal"]).join(", ")}`,
          );
        },
      );
      assertCurrent();
      const backendRecord = this.backendRecords.get(backendSessionKey(responseSession.id, responseSession.generation));
      if (backendRecord?.state === "failed") {
        throw new Error(backendRecord.message ?? "GPT-Live backend sideband disconnected during startup");
      }
      const release = await transport.activationBridge.release();
      diagnose("activation_audio_released", {
        buffered: Boolean(options.candidateAudio),
        browser_session_started: readiness.browserSessionStarted,
        ...release,
        wake_to_release_ms: options.wakeDetectedMonotonicMs === undefined
          ? undefined
          : roundMs(monotonicNow() - options.wakeDetectedMonotonicMs),
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
    // Speaking over Live is the interruption signal; do not send a competing
    // response.cancel event, which is not part of the Live protocol.
    this.publish({ ...this.snapshot, status: "interrupted", playbackActive: false });
    this.touch();
  }

  async stop(): Promise<void> {
    this.attemptEpoch += 1;
    this.startupAbort?.abort();
    ++this.preparationEpoch;
    this.preparation = null;
    this.preparationStream = null;
    await this.discardPreparedTransport();
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

  private async buildPreparedTransport(
    stream: MediaStream,
    ownsStream: boolean,
    prepareEpoch: number,
  ): Promise<PreparedLiveTransport> {
    const activationBridge = await createLiveActivationBridge(stream, this.deps.audioContext);
    const pc = this.deps.peerConnection();
    const events = pc.createDataChannel("oai-events");
    const transport: PreparedLiveTransport = {
      stream,
      ownsStream,
      pc,
      events,
      activationBridge,
      localOffer: "",
      peerConnected: pc.connectionState === "connected",
      dataChannelOpen: events.readyState === "open",
      claimed: false,
      prepareEpoch,
      cleanups: [],
    };
    const observePeerState = () => this.observePreparedTransportState(transport);
    const observeIceState = () => this.observePreparedTransportState(transport);
    pc.addEventListener?.("connectionstatechange", observePeerState);
    pc.addEventListener?.("iceconnectionstatechange", observeIceState);
    transport.cleanups.push(
      () => pc.removeEventListener?.("connectionstatechange", observePeerState),
      () => pc.removeEventListener?.("iceconnectionstatechange", observeIceState),
    );
    activationBridge.stream.getAudioTracks().forEach((track) => pc.addTrack(track, activationBridge.stream));
    pc.ontrack = (event) => {
      if (this.pc !== pc) return;
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
    events.onopen = () => {
      transport.dataChannelOpen = true;
      if (this.events !== events) return;
      this.activeDiagnose?.("data_channel_open");
      this.activeReadiness?.markDataChannelOpen();
    };
    events.onclose = () => {
      if (this.events !== events) return;
      this.activeDiagnose?.("data_channel_closed");
      this.failActiveTransport(new Error("GPT-Live events channel closed"));
    };
    events.onerror = () => {
      if (this.events !== events) return;
      this.activeDiagnose?.("data_channel_error");
      this.failActiveTransport(new Error("GPT-Live events channel failed"));
    };
    events.onmessage = (message) => {
      let event: Record<string, unknown>;
      try {
        event = JSON.parse(String(message.data)) as Record<string, unknown>;
      } catch {
        return;
      }
      const type = typeof event.type === "string" ? event.type : "";
      if (type === "session.closed") this.closeWaiter?.();
      if (this.events !== events) return;
      this.options.onProviderEvent?.(event);
      if (type === "session.started") {
        this.activeDiagnose?.("browser_session_started");
        this.activeReadiness?.markBrowserSessionStarted();
      }
      if (type === "response.event") this.trackDelegation(event);
      if (isMeaningfulLiveEvent(event)) this.touch();
    };
    try {
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await waitForIceGathering(pc);
      transport.localOffer = pc.localDescription?.sdp ?? offer.sdp ?? "";
      return transport;
    } catch (cause) {
      await destroyPreparedTransport(transport);
      throw cause;
    }
  }

  private observePreparedTransportState(transport: PreparedLiveTransport): void {
    const { pc } = transport;
    const peerState = pc.connectionState;
    const iceState = pc.iceConnectionState;
    transport.peerConnected = peerState === "connected";
    if (this.pc !== pc) return;
    this.activeDiagnose?.("peer_state", { state: peerState });
    this.activeDiagnose?.("ice_state", { state: iceState });
    if (transport.peerConnected) this.activeReadiness?.markPeerConnected();
    if (peerState === "failed" || peerState === "closed") {
      this.failActiveTransport(new Error(`GPT-Live WebRTC connection ${peerState}`));
    }
    if (iceState === "failed" || iceState === "closed") {
      this.failActiveTransport(new Error(`GPT-Live ICE connection ${iceState}`));
    }
  }

  private failActiveTransport(error: Error): void {
    this.activeReadiness?.fail(error);
    if (!["connected", "interrupted"].includes(this.snapshot.status)) return;
    this.publish({ ...this.snapshot, status: "error", micEnabled: false, error: error.message });
    void this.dispose(true);
  }

  private observeBackendReadiness(
    session: JarvisSession,
    readiness: LiveStartupReadiness,
    diagnose: (event: string, detail?: Record<string, unknown>) => void,
    signal: AbortSignal,
    attempt: number,
  ): () => void {
    const key = backendSessionKey(session.id, session.generation);
    let cancelled = false;
    const receive = (record: BackendReadinessRecord) => {
      if (cancelled || signal.aborted || attempt !== this.attemptEpoch) return;
      if (record.state === "ready") {
        diagnose("backend_ready", { source: this.backendStreamOpen ? "event_stream" : "snapshot" });
        readiness.markBackendReady();
        return;
      }
      const error = new Error(record.message ?? "GPT-Live backend readiness failed");
      diagnose("backend_readiness_failed", { message: error.message });
      readiness.fail(error);
      if (this.snapshot.status === "connected") {
        void this.dispose(true).finally(() => {
          this.publish({ ...this.snapshot, status: "error", micEnabled: false, error: error.message });
        });
      }
    };
    const listeners = this.backendWaiters.get(key) ?? new Set();
    listeners.add(receive);
    this.backendWaiters.set(key, listeners);
    const cached = this.backendRecords.get(key);
    if (cached) queueMicrotask(() => receive(cached));
    const pollSnapshot = async () => {
      while (!cancelled && !signal.aborted && attempt === this.attemptEpoch) {
        if (!this.backendStreamOpen) {
          try {
            const state = await this.deps.getBackendState();
            if (cancelled || signal.aborted) return;
            const current = state.session;
            if (current?.id === session.id && current.generation === session.generation) {
              if (current.lifecycle_state === "ready") {
                this.publishBackendRecord(session.id, session.generation, { state: "ready" });
              } else if (current.lifecycle_state === "disconnected") {
                this.publishBackendRecord(session.id, session.generation, {
                  state: "failed",
                  message: "GPT-Live backend sideband disconnected during startup",
                });
              }
            } else if (current) {
              this.publishBackendRecord(session.id, session.generation, {
                state: "failed",
                message: "GPT-Live session was replaced during startup",
              });
            }
          } catch {
            // The existing durable event replay may recover readiness. Retry
            // snapshots only while that stream is unavailable.
          }
        }
        await delay(100, this.deps, signal).catch(() => undefined);
      }
    };
    void pollSnapshot();
    return () => {
      cancelled = true;
      const current = this.backendWaiters.get(key);
      current?.delete(receive);
      if (current?.size === 0) this.backendWaiters.delete(key);
    };
  }

  private publishBackendRecord(
    sessionId: string,
    generation: number,
    record: BackendReadinessRecord,
  ): void {
    const key = backendSessionKey(sessionId, generation);
    this.backendRecords.set(key, record);
    this.backendWaiters.get(key)?.forEach((listener) => listener(record));
  }

  private emitPreparationDiagnostic(event: string, detail?: Record<string, unknown>): void {
    this.options.onDiagnostic?.({
      event,
      observedAt: new Date().toISOString(),
      attempt: 0,
      ...(detail ? { detail } : {}),
    });
  }

  private async discardPreparedTransport(): Promise<void> {
    const prepared = this.prepared;
    this.prepared = null;
    if (prepared) await destroyPreparedTransport(prepared);
  }

  private async dispose(closeBackend: boolean): Promise<void> {
    if (this.idleTimer) this.deps.clearTimeout(this.idleTimer);
    this.idleTimer = null;
    this.closeWaiter = null;
    this.backendObserverCleanup?.();
    this.backendObserverCleanup = null;
    this.activeReadiness = null;
    this.activeDiagnose = null;
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
  attach(candidate?: JarvisCandidateAudio): Record<string, unknown>;
  release(): Promise<Record<string, unknown>>;
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
  liveStream: MediaStream,
  createAudioContext: () => AudioContext,
): Promise<LiveActivationBridge> {
  const context = createAudioContext();
  const destination = context.createMediaStreamDestination();
  const liveSource = context.createMediaStreamSource(liveStream);
  const queued: Float32Array[] = [];
  const replaySources = new Set<AudioBufferSourceNode>();
  let queuedSamples = 0;
  let scheduledAt = 0;
  let started = false;
  let overflow = false;
  let destroyed = false;
  let candidate: JarvisCandidateAudio | null = null;
  let unsubscribe: (() => void) | null = null;
  let compactor: ActivationSilenceCompactor | null = null;
  let totalInputSamples = 0;
  let totalScheduledSamples = 0;
  let queuedAtRelease = 0;
  let firstCaptureMonotonicMs: number | undefined;
  const failOverflow = (samples?: Float32Array) => {
    overflow = true;
    samples?.fill(0);
    candidate?.close("audio_backlog_overflow");
  };
  const scheduledBacklogSamples = () => Math.max(
    0,
    Math.round((scheduledAt - context.currentTime) * (candidate?.sampleRate ?? 16_000)),
  );
  const scheduleFrame = (samples: Float32Array) => {
    if (destroyed || overflow) {
      samples.fill(0);
      return;
    }
    const startAt = Math.max(scheduledAt, context.currentTime + 0.03);
    const sampleRate = candidate?.sampleRate ?? 16_000;
    const endAt = startAt + samples.length / sampleRate;
    if ((endAt - context.currentTime) * sampleRate > MAX_ACTIVATION_REPLAY_SAMPLES) {
      failOverflow(samples);
      return;
    }
    const buffer = context.createBuffer(1, samples.length, sampleRate);
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
    totalScheduledSamples += samples.length;
  };
  const schedule = (samples: Float32Array, pressureSamples = scheduledBacklogSamples()) => {
    if (!compactor) {
      samples.fill(0);
      return;
    }
    for (const frame of compactor.compact(samples, pressureSamples)) scheduleFrame(frame);
  };
  return {
    stream: destination.stream,
    attach: (nextCandidate) => {
      if (destroyed) throw new Error("GPT-Live activation bridge is closed");
      if (!nextCandidate || candidate === nextCandidate) {
        return { buffered: Boolean(candidate), queued_audio_ms: 0 };
      }
      if (candidate) throw new Error("GPT-Live activation audio is already attached");
      candidate = nextCandidate;
      compactor = new ActivationSilenceCompactor(candidate.sampleRate);
      unsubscribe = candidate.subscribe((chunk) => {
        firstCaptureMonotonicMs ??= chunk.capturedMonotonicMs;
        totalInputSamples += chunk.samples.length;
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
        buffered: true,
        queued_audio_ms: roundMs(queuedSamples / candidate.sampleRate * 1_000),
        first_capture_monotonic_ms: firstCaptureMonotonicMs,
      };
    },
    release: async () => {
      if (destroyed) throw new Error("GPT-Live activation bridge is closed");
      if (overflow) {
        throw new Error("GPT-Live activation audio exceeded the 30-second handoff limit; please retry");
      }
      if (context.state === "suspended") await context.resume();
      started = true;
      if (!candidate) {
        liveSource.connect(destination);
        return {
          queued_audio_ms: 0,
          scheduled_audio_ms: 0,
          silence_removed_ms: 0,
          estimated_tail_delay_ms: 0,
        };
      }
      candidate.promoteToConversation();
      queuedAtRelease = queuedSamples;
      for (const samples of queued) {
        queuedSamples -= samples.length;
        schedule(samples, Math.max(queuedSamples + samples.length, scheduledBacklogSamples()));
      }
      queued.length = 0;
      if (overflow) {
        throw new Error("GPT-Live activation audio exceeded the 30-second handoff limit; please retry");
      }
      const sampleRate = candidate.sampleRate;
      return {
        queued_audio_ms: roundMs(queuedAtRelease / sampleRate * 1_000),
        scheduled_audio_ms: roundMs(totalScheduledSamples / sampleRate * 1_000),
        silence_removed_ms: roundMs((totalInputSamples - totalScheduledSamples) / sampleRate * 1_000),
        estimated_tail_delay_ms: roundMs(Math.max(0, scheduledAt - context.currentTime) * 1_000),
      };
    },
    destroy: async () => {
      if (destroyed) return;
      destroyed = true;
      unsubscribe?.();
      for (const samples of queued) samples.fill(0);
      queued.length = 0;
      for (const source of replaySources) {
        try { source.stop(); } catch { /* Already stopped. */ }
        source.disconnect();
      }
      replaySources.clear();
      try { liveSource.disconnect(); } catch { /* Never connected. */ }
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

async function destroyPreparedTransport(transport: PreparedLiveTransport): Promise<void> {
  for (const cleanup of transport.cleanups) cleanup();
  transport.events.onopen = null;
  transport.events.onclose = null;
  transport.events.onerror = null;
  transport.events.onmessage = null;
  transport.events.close();
  transport.pc.ontrack = null;
  transport.pc.close();
  await transport.activationBridge.destroy();
  if (transport.ownsStream) transport.stream.getTracks().forEach((track) => track.stop());
}

function backendSessionKey(sessionId: string, generation: number): string {
  return `${sessionId}:${generation}`;
}

function monotonicNow(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function roundMs(value: number): number {
  return Math.round(value * 1_000) / 1_000;
}

async function delay(
  milliseconds: number,
  deps: Pick<Required<JarvisLiveDependencies>, "setTimeout" | "clearTimeout">,
  signal?: AbortSignal,
): Promise<void> {
  if (signal?.aborted) throw new LiveStartupCancelledError();
  await new Promise<void>((resolve, reject) => {
    const timer = deps.setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    const abort = () => {
      deps.clearTimeout(timer);
      reject(new LiveStartupCancelledError());
    };
    signal?.addEventListener("abort", abort, { once: true });
  });
}
