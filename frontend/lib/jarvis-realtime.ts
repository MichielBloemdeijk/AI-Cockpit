import {
  closeJarvisRealtimeSession,
  createJarvisRealtimeCall,
  submitJarvisRealtimeTelemetry,
  type JarvisLiveEvent,
  type JarvisRealtimeTelemetryEvent,
  type JarvisSession,
} from "./api";
import {
  createJarvisBargeInDetector,
  type JarvisBargeInDetector,
} from "./jarvis-vad";

export type JarvisRealtimeStatus =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "text_fallback";

export interface JarvisRealtimeSnapshot {
  status: JarvisRealtimeStatus;
  attempt: number;
  session: JarvisSession | null;
  error: string | null;
}

export interface JarvisRealtimeControllerOptions {
  controllerId: string;
  onSnapshot?: (snapshot: JarvisRealtimeSnapshot) => void;
  onRemoteStream?: (stream: MediaStream | null) => void;
  onProviderEvent?: (event: Record<string, unknown>) => void;
  onPlaybackMuted?: (muted: boolean) => void;
}

export interface JarvisRealtimeAudioHandoff {
  localStream: MediaStream;
  utterance: Promise<Float32Array>;
  sampleRate: number;
}

export interface JarvisRealtimeStartOptions {
  audioHandoff?: JarvisRealtimeAudioHandoff;
  activationContext?: JarvisRealtimeActivationContext;
}

export interface JarvisRealtimeActivationContext {
  source: "wake_word";
  wake_score: number;
  activation_id?: string;
  wake_detected_at?: string;
  local_speech_started_at?: string;
}

export interface JarvisRealtimeTraceEntry {
  kind: string;
  monotonic_ms: number;
  wall_time: string;
  response_id?: string;
  item_id?: string;
  attempt?: number;
  sequence?: number;
  server_time?: string;
  timing_ms?: Record<string, number>;
  detail?: string;
}

export interface JarvisRealtimeConnectionTiming {
  attempt: number;
  total_ms?: number;
  offer_ms?: number;
  backend_call_ms?: number;
  remote_description_ms?: number;
  wake_detect_to_connected_ms?: number;
  provider_call_ms?: number;
  sideband_connect_ms?: number;
}

export interface JarvisRealtimeTurnTiming {
  turn: number;
  kind: "wake" | "follow_up";
  response_id?: string;
  local_speech_stop_to_provider_stop_ms?: number;
  wake_handoff_tail_ms?: number;
  provider_stop_to_response_created_ms?: number;
  response_created_to_first_audio_ms?: number;
  provider_stop_to_playback_started_ms?: number;
  local_speech_stop_to_playback_started_ms?: number;
  backend_arbiter_ms?: number;
}

export interface JarvisRealtimeTimingSummary {
  schema: "jarvis.realtime_timing.v1";
  connections: JarvisRealtimeConnectionTiming[];
  turns: JarvisRealtimeTurnTiming[];
}

interface BrowserConnection {
  peer: RTCPeerConnection;
  channel: RTCDataChannel;
  localStream: MediaStream;
  ownsLocalStream: boolean;
  handoffTransport: JarvisRealtimeHandoffTransport | null;
  session: JarvisSession;
  rotateAfterSeconds: number;
  bargeInDetector: JarvisBargeInDetector | null;
  activeResponseId: string | null;
  cancellingResponseId: string | null;
  bargeInTimer: number | null;
  mutedResponseId: string | null;
  tracedAudioDeltaResponses: Set<string>;
  tracedTranscriptDeltaItems: Set<string>;
  wakeCommitWaiter: WakeCommitWaiter | null;
}

interface JarvisRealtimeHandoffTransport {
  stream: MediaStream;
  connectLive: () => Promise<void>;
  destroy: () => Promise<void>;
}

export interface JarvisRealtimeWakeAudioEnvelope {
  chunks: readonly string[];
  byteLength: number;
  durationMs: number;
  sourceSampleRate: number;
  targetSampleRate: 24_000;
}

interface WakeCommitWaiter {
  eventId: string;
  timer: number;
  resolve: (itemId?: string) => void;
  reject: (cause: Error) => void;
}

interface JarvisRealtimeConnectionInput {
  localStream: MediaStream;
  ownsLocalStream: boolean;
  wakeAudio?: Promise<JarvisRealtimeWakeAudioEnvelope>;
  activationContext?: JarvisRealtimeActivationContext;
}

interface BrowserPreparation {
  peer: RTCPeerConnection;
  channel: RTCDataChannel;
  localStream: MediaStream;
  sender: RTCRtpSender;
  offer: Promise<RTCSessionDescriptionInit>;
}

export interface JarvisRealtimeDependencies {
  createPeer: () => RTCPeerConnection;
  getUserMedia: (constraints: MediaStreamConstraints) => Promise<MediaStream>;
  createCall: typeof createJarvisRealtimeCall;
  closeSession: typeof closeJarvisRealtimeSession;
  sendTelemetry?: typeof submitJarvisRealtimeTelemetry;
  wait: (milliseconds: number) => Promise<void>;
  setTimer: (callback: () => void, milliseconds: number) => number;
  clearTimer: (timer: number) => void;
  now: () => number;
  wallNow: () => string;
  createBargeInDetector: (
    stream: MediaStream,
    onSpeechStart: () => void,
    onSpeechEnd: () => void,
  ) => Promise<JarvisBargeInDetector>;
  createHandoffTransport: (
    localStream: MediaStream,
  ) => Promise<JarvisRealtimeHandoffTransport>;
}

const RETRY_DELAYS_MS = [0, 500, 1_000, 2_000] as const;
const DISCONNECT_GRACE_MS = 2_000;
const ICE_CANDIDATE_WAIT_MS = 750;
const DATA_CHANNEL_OPEN_TIMEOUT_MS = 10_000;
const WAKE_COMMIT_TIMEOUT_MS = 3_000;
const WAKE_PCM_SAMPLE_RATE = 24_000 as const;
const WAKE_PCM_CHUNK_BYTES = 24_000;

const browserDependencies: JarvisRealtimeDependencies = {
  createPeer: () => new RTCPeerConnection(),
  getUserMedia: (constraints) => navigator.mediaDevices.getUserMedia(constraints),
  createCall: createJarvisRealtimeCall,
  closeSession: closeJarvisRealtimeSession,
  sendTelemetry: submitJarvisRealtimeTelemetry,
  wait: (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds)),
  setTimer: (callback, milliseconds) => window.setTimeout(callback, milliseconds),
  clearTimer: (timer) => window.clearTimeout(timer),
  now: () => performance.now(),
  wallNow: () => new Date().toISOString(),
  createBargeInDetector: createJarvisBargeInDetector,
  createHandoffTransport: createBrowserHandoffTransport,
};

export class JarvisRealtimeController {
  private readonly options: JarvisRealtimeControllerOptions;
  private readonly dependencies: JarvisRealtimeDependencies;
  private active: BrowserConnection | null = null;
  private preparation: BrowserPreparation | null = null;
  private preparationOperation = 0;
  private rotationTimer: number | null = null;
  private disconnectTimer: number | null = null;
  private operation = 0;
  private reconnecting = false;
  private readonly trace: JarvisRealtimeTraceEntry[] = [];
  private readonly traceId = crypto.randomUUID();
  private activationId: string | null = null;
  private telemetryPending: JarvisRealtimeTelemetryEvent[] = [];
  private telemetrySending = false;
  private snapshot: JarvisRealtimeSnapshot = {
    status: "idle",
    attempt: 0,
    session: null,
    error: null,
  };

  constructor(
    options: JarvisRealtimeControllerOptions,
    dependencies: JarvisRealtimeDependencies = browserDependencies,
  ) {
    this.options = options;
    this.dependencies = dependencies;
  }

  current(): JarvisRealtimeSnapshot {
    return this.snapshot;
  }

  traceSnapshot(): readonly JarvisRealtimeTraceEntry[] {
    return [...this.trace];
  }

  timingSummary(): JarvisRealtimeTimingSummary {
    return summarizeJarvisRealtimeTrace(this.trace);
  }

  recordPlaybackSignal(kind: string, detail?: string): void {
    this.recordTrace(kind, { detail });
  }

  recordClientSignal(kind: string, detail?: Record<string, unknown>): void {
    this.recordTrace(kind, {
      detail: detail ? JSON.stringify(detail) : undefined,
    });
  }

  recordBackendEvent(event: JarvisLiveEvent): void {
    if (event.source_domain !== "session") return;
    const payload = event.payload ?? {};
    const timing = payload.timing_ms;
    const timingMs = timing && typeof timing === "object"
      ? Object.fromEntries(
          Object.entries(timing).filter((entry): entry is [string, number] => (
            typeof entry[1] === "number" && Number.isFinite(entry[1])
          )),
        )
      : undefined;
    const responseId = typeof payload.response_id === "string"
      ? payload.response_id
      : undefined;
    const reason = typeof payload.reason === "string" ? payload.reason : undefined;
    this.recordTrace(`backend.${event.event_type}`, {
      response_id: responseId,
      sequence: event.sequence,
      server_time: event.created_at,
      timing_ms: timingMs,
      detail: reason,
    });
  }

  async prepare(localStream: MediaStream): Promise<void> {
    if (this.active || this.snapshot.status === "connecting" || this.snapshot.status === "reconnecting") return;
    if (this.preparation?.localStream === localStream) {
      await this.preparation.offer;
      return;
    }
    const operation = ++this.preparationOperation;
    this.recordTrace("realtime_transport_prepare_started");
    const previous = this.preparation;
    this.preparation = null;
    if (previous) this.disposePreparation(previous);

    const peer = this.dependencies.createPeer();
    const channel = peer.createDataChannel("oai-events");
    const track = localStream.getAudioTracks()[0];
    if (!track) {
      channel.close();
      peer.close();
      throw new Error("Wake stream has no audio track");
    }
    const sender = peer.addTrack(track, localStream);
    this.recordTrace("realtime_offer_started");
    const offer = this.createLocalOffer(peer).then((value) => {
      this.recordTrace("realtime_offer_ready");
      return value;
    });
    const preparation: BrowserPreparation = {
      peer,
      channel,
      localStream,
      sender,
      offer,
    };
    this.preparation = preparation;
    try {
      await preparation.offer;
      if (operation === this.preparationOperation && this.preparation === preparation) {
        this.recordTrace("realtime_transport_prepared");
      }
    } catch (cause) {
      if (this.preparation === preparation) this.preparation = null;
      this.disposePreparation(preparation);
      throw cause;
    }
  }

  async start(options: JarvisRealtimeStartOptions = {}): Promise<JarvisRealtimeSnapshot> {
    this.activationId = options.activationContext?.activation_id ?? crypto.randomUUID();
    const activationContext = options.activationContext
      ? {
          ...options.activationContext,
          activation_id: this.activationId,
          wake_detected_at: options.activationContext.wake_detected_at ?? this.dependencies.wallNow(),
        }
      : undefined;
    this.recordTrace("realtime_start_requested", {
      detail: options.audioHandoff ? "wake_handoff" : "direct",
    });
    const operation = ++this.operation;
    this.cancelRotation();
    this.cancelDisconnect();
    const input = options.audioHandoff
      ? {
          localStream: options.audioHandoff.localStream,
          ownsLocalStream: false,
          wakeAudio: this.prepareWakeAudio(options.audioHandoff),
          activationContext,
        }
      : undefined;
    return this.connectWithRetry(operation, false, input);
  }

  async stop(): Promise<void> {
    ++this.operation;
    ++this.preparationOperation;
    this.cancelRotation();
    this.cancelDisconnect();
    const preparation = this.preparation;
    this.preparation = null;
    if (preparation) this.disposePreparation(preparation);
    const current = this.active;
    this.active = null;
    if (current) await this.disposeConnection(current, true);
    await this.flushTelemetry();
    this.options.onRemoteStream?.(null);
    this.options.onPlaybackMuted?.(false);
    this.publish({ status: "idle", attempt: 0, session: null, error: null });
  }

  private async reconnect(): Promise<void> {
    if (this.reconnecting || this.snapshot.status === "idle") return;
    this.reconnecting = true;
    const operation = ++this.operation;
    this.cancelRotation();
    this.cancelDisconnect();
    try {
      const current = this.active;
      const input = current
        ? { localStream: current.localStream, ownsLocalStream: false }
        : undefined;
      await this.connectWithRetry(operation, true, input);
    } catch {
      // connectWithRetry publishes the terminal fallback state.
    } finally {
      this.reconnecting = false;
    }
  }

  private async connectWithRetry(
    operation: number,
    reconnecting: boolean,
    input?: JarvisRealtimeConnectionInput,
  ): Promise<JarvisRealtimeSnapshot> {
    let lastError: unknown = null;
    for (let index = 0; index < RETRY_DELAYS_MS.length; index += 1) {
      const delay = RETRY_DELAYS_MS[index];
      if (delay) await this.dependencies.wait(delay);
      if (operation !== this.operation) throw new Error("Realtime connection was cancelled");
      this.publish({
        status: reconnecting ? "reconnecting" : "connecting",
        attempt: index + 1,
        session: this.active?.session ?? null,
        error: null,
      });
      try {
        const attempt = index + 1;
        this.recordTrace("realtime_connection_attempt_started", { attempt });
        const connection = await this.connectAttempt(operation, input, attempt);
        if (operation !== this.operation) {
          await this.disposeConnection(connection, true);
          throw new Error("Realtime connection was cancelled");
        }
        const previous = this.active;
        this.active = connection;
        this.cancelDisconnect();
        if (previous) {
          if (previous.localStream === connection.localStream) {
            connection.ownsLocalStream = previous.ownsLocalStream;
            previous.ownsLocalStream = false;
          }
          await this.disposeConnection(previous, false);
        }
        this.publish({
          status: "connected",
          attempt: index + 1,
          session: connection.session,
          error: null,
        });
        this.recordTrace("realtime_controller_connected", {
          attempt: index + 1,
          detail: `${connection.session.id}:${connection.session.generation}`,
        });
        void this.flushTelemetry();
        this.scheduleRotation(connection.rotateAfterSeconds);
        return this.snapshot;
      } catch (cause) {
        lastError = cause;
      }
    }

    const previous = this.active;
    this.active = null;
    if (previous) await this.disposeConnection(previous, false);
    this.options.onRemoteStream?.(null);
    const error = lastError instanceof Error ? lastError.message : "Realtime connection failed";
    this.publish({ status: "text_fallback", attempt: RETRY_DELAYS_MS.length, session: null, error });
    await this.flushTelemetry();
    throw new Error(error);
  }

  private async connectAttempt(
    operation: number,
    input?: JarvisRealtimeConnectionInput,
    attempt = 1,
  ): Promise<BrowserConnection> {
    const mediaStarted = this.dependencies.now();
    const localStream = input?.localStream ?? await this.dependencies.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.recordTrace("realtime_local_stream_ready", {
      attempt,
      detail: `wait_ms:${Math.round(this.dependencies.now() - mediaStarted)}`,
    });
    const ownsLocalStream = input?.ownsLocalStream ?? true;
    const prepared = this.takePreparation(localStream);
    const peer = prepared?.peer ?? this.dependencies.createPeer();
    const channel = prepared?.channel ?? peer.createDataChannel("oai-events");
    let providerSession: JarvisSession | null = null;
    let connection: BrowserConnection | null = null;
    let handoffTransport: JarvisRealtimeHandoffTransport | null = null;
    try {
      let outgoingStream = localStream;
      if (input?.wakeAudio) {
        this.recordTrace("wake_handoff_transport_started", { attempt });
        handoffTransport = await this.dependencies.createHandoffTransport(localStream);
        outgoingStream = handoffTransport.stream;
        this.recordTrace("wake_handoff_transport_ready", { attempt });
      }
      if (prepared) {
        const outgoingTrack = outgoingStream.getAudioTracks()[0];
        if (!outgoingTrack) throw new Error("Realtime stream has no audio track");
        if (outgoingTrack !== localStream.getAudioTracks()[0]) {
          await prepared.sender.replaceTrack(outgoingTrack);
        }
      } else {
        for (const track of outgoingStream.getAudioTracks()) peer.addTrack(track, outgoingStream);
      }
      peer.ontrack = (event) => {
        if (operation !== this.operation) return;
        this.recordTrace("remote_track_received");
        this.options.onRemoteStream?.(event.streams[0] ?? null);
      };
      channel.onmessage = (event) => {
        try {
          const parsed = JSON.parse(String(event.data));
          if (parsed && typeof parsed === "object") {
            const providerEvent = parsed as Record<string, unknown>;
            this.handleBrowserProviderEvent(connection, providerEvent);
            this.options.onProviderEvent?.(providerEvent);
          }
        } catch {
          // Provider diagnostics are optional; malformed data cannot own state.
        }
      };
      peer.onconnectionstatechange = () => {
        if (operation !== this.operation || this.active?.peer !== peer) return;
        if (peer.connectionState === "connected") {
          this.cancelDisconnect();
        } else if (peer.connectionState === "failed") {
          this.cancelDisconnect();
          void this.reconnect();
        } else if (peer.connectionState === "disconnected" && this.disconnectTimer === null) {
          this.disconnectTimer = this.dependencies.setTimer(() => {
            this.disconnectTimer = null;
            if (
              operation === this.operation
              && this.active?.peer === peer
              && peer.connectionState === "disconnected"
            ) void this.reconnect();
          }, DISCONNECT_GRACE_MS);
        }
      };

      if (prepared) this.recordTrace("realtime_offer_reused", { attempt });
      else this.recordTrace("realtime_offer_started", { attempt });
      const offer = prepared ? await prepared.offer : await this.createLocalOffer(peer);
      if (!prepared) this.recordTrace("realtime_offer_ready", { attempt });
      const sdpOffer = peer.localDescription?.sdp || offer.sdp;
      if (!sdpOffer) throw new Error("Browser did not produce an SDP offer");
      this.recordTrace("realtime_backend_call_started", { attempt });
      const response = await this.dependencies.createCall(
        this.options.controllerId,
        "controller",
        "openai",
        sdpOffer,
        undefined,
        input?.activationContext,
      );
      this.recordTrace("realtime_backend_call_completed", { attempt });
      providerSession = response.session;
      if (!providerSession || !response.sdp_answer) {
        throw new Error("Backend did not return a complete Realtime answer");
      }
      await peer.setRemoteDescription({ type: "answer", sdp: response.sdp_answer });
      this.recordTrace("realtime_remote_description_set", { attempt });
      connection = {
        peer,
        channel,
        localStream,
        ownsLocalStream,
        handoffTransport,
        session: providerSession,
        rotateAfterSeconds: response.rotate_after_seconds ?? 55 * 60,
        bargeInDetector: null,
        activeResponseId: null,
        cancellingResponseId: null,
        bargeInTimer: null,
        mutedResponseId: null,
        tracedAudioDeltaResponses: new Set(),
        tracedTranscriptDeltaItems: new Set(),
        wakeCommitWaiter: null,
      };
      if (handoffTransport && input?.wakeAudio) {
        await this.waitForDataChannelOpen(channel, operation, attempt);
        const wakeAudio = await input.wakeAudio;
        await this.submitWakeAudio(connection, handoffTransport, wakeAudio, attempt);
      }
      try {
        connection.bargeInDetector = await this.dependencies.createBargeInDetector(
          localStream,
          () => this.handleLocalSpeechStart(connection, operation),
          () => this.handleLocalSpeechEnd(connection, operation),
        );
        this.recordTrace("local_vad_ready");
      } catch (cause) {
        this.recordTrace("local_vad_unavailable", { detail: this.errorName(cause) });
      }
      this.recordTrace("realtime_connection_ready", {
        attempt,
        detail: `${providerSession.id}:${providerSession.generation}`,
      });
      return connection;
    } catch (cause) {
      await handoffTransport?.destroy().catch(() => undefined);
      channel.close();
      peer.close();
      if (ownsLocalStream) {
        for (const track of localStream.getTracks()) track.stop();
      }
      if (providerSession) {
        await this.dependencies.closeSession(providerSession.id, providerSession.generation).catch(() => undefined);
      }
      throw cause;
    }
  }

  private scheduleRotation(seconds: number): void {
    this.cancelRotation();
    this.rotationTimer = this.dependencies.setTimer(
      () => void this.reconnect(),
      Math.max(1, seconds) * 1_000,
    );
  }

  private async createLocalOffer(peer: RTCPeerConnection): Promise<RTCSessionDescriptionInit> {
    const offer = await peer.createOffer();
    await peer.setLocalDescription(offer);
    await this.waitForUsableIce(peer);
    return peer.localDescription ?? offer;
  }

  private prepareWakeAudio(
    handoff: JarvisRealtimeAudioHandoff,
  ): Promise<JarvisRealtimeWakeAudioEnvelope> {
    this.recordTrace("wake_audio_encode_waiting");
    return handoff.utterance.then((samples) => {
      this.recordTrace("wake_audio_encode_started", {
        detail: `samples:${samples.length}:rate:${handoff.sampleRate}`,
      });
      try {
        const envelope = encodeJarvisWakeAudio(samples, handoff.sampleRate);
        this.recordTrace("wake_audio_encode_completed", {
          detail: `bytes:${envelope.byteLength}:chunks:${envelope.chunks.length}:duration_ms:${Math.round(envelope.durationMs)}`,
        });
        return envelope;
      } finally {
        samples.fill(0);
      }
    });
  }

  private async waitForDataChannelOpen(
    channel: RTCDataChannel,
    operation: number,
    attempt: number,
  ): Promise<void> {
    if (channel.readyState === "open") {
      this.recordTrace("realtime_data_channel_open", { attempt });
      return;
    }
    if (channel.readyState !== "connecting") throw new Error("Realtime data channel is unavailable");
    await new Promise<void>((resolve, reject) => {
      let timer = 0;
      const cleanup = () => {
        channel.removeEventListener("open", onOpen);
        channel.removeEventListener("close", onClose);
        channel.removeEventListener("error", onError);
        if (timer) this.dependencies.clearTimer(timer);
      };
      const onOpen = () => {
        cleanup();
        if (operation !== this.operation) reject(new Error("Realtime connection was cancelled"));
        else resolve();
      };
      const onClose = () => {
        cleanup();
        reject(new Error("Realtime data channel closed before wake audio was sent"));
      };
      const onError = () => {
        cleanup();
        reject(new Error("Realtime data channel failed before wake audio was sent"));
      };
      channel.addEventListener("open", onOpen);
      channel.addEventListener("close", onClose);
      channel.addEventListener("error", onError);
      timer = this.dependencies.setTimer(() => {
        cleanup();
        reject(new Error("Realtime data channel did not open in time"));
      }, DATA_CHANNEL_OPEN_TIMEOUT_MS);
    });
    this.recordTrace("realtime_data_channel_open", { attempt });
  }

  private async submitWakeAudio(
    connection: BrowserConnection,
    handoffTransport: JarvisRealtimeHandoffTransport,
    envelope: JarvisRealtimeWakeAudioEnvelope,
    attempt: number,
  ): Promise<void> {
    if (!envelope.chunks.length) {
      await handoffTransport.connectLive();
      this.recordTrace("wake_live_mic_activated", { attempt, detail: "empty_handoff" });
      return;
    }
    if (connection.channel.readyState !== "open") throw new Error("Realtime data channel is not open");
    const activationId = this.activationId ?? crypto.randomUUID();
    const commitEventId = `jarvis-wake-commit-${activationId}-${attempt}`;
    const acknowledgement = new Promise<string | undefined>((resolve, reject) => {
      const timer = this.dependencies.setTimer(
        () => reject(new Error("OpenAI did not acknowledge the wake audio in time")),
        WAKE_COMMIT_TIMEOUT_MS,
      );
      connection.wakeCommitWaiter = { eventId: commitEventId, timer, resolve, reject };
    });
    this.recordTrace("wake_audio_upload_started", {
      attempt,
      detail: `bytes:${envelope.byteLength}:chunks:${envelope.chunks.length}`,
    });
    try {
      envelope.chunks.forEach((audio, index) => {
        connection.channel.send(JSON.stringify({
          type: "input_audio_buffer.append",
          event_id: `jarvis-wake-append-${activationId}-${attempt}-${index}`,
          audio,
        }));
      });
      this.recordTrace("wake_audio_upload_completed", { attempt, detail: `bytes:${envelope.byteLength}` });
      connection.channel.send(JSON.stringify({
        type: "input_audio_buffer.commit",
        event_id: commitEventId,
      }));
      this.recordTrace("wake_audio_commit_sent", { attempt });
      await handoffTransport.connectLive();
      this.recordTrace("wake_live_mic_activated", { attempt });
      const itemId = await acknowledgement;
      this.recordTrace("wake_audio_commit_acknowledged", { attempt, item_id: itemId });
    } finally {
      if (connection.wakeCommitWaiter?.eventId === commitEventId) {
        this.dependencies.clearTimer(connection.wakeCommitWaiter.timer);
        connection.wakeCommitWaiter = null;
      }
    }
  }

  private async waitForUsableIce(peer: RTCPeerConnection): Promise<void> {
    if (
      peer.iceGatheringState === "complete"
      || peer.localDescription?.sdp?.includes("a=candidate:")
      || typeof peer.addEventListener !== "function"
      || typeof peer.removeEventListener !== "function"
    ) return;
    await new Promise<void>((resolve) => {
      let timer = 0;
      const done = () => {
        peer.removeEventListener("icegatheringstatechange", onChange);
        peer.removeEventListener("icecandidate", onCandidate);
        if (timer) this.dependencies.clearTimer(timer);
        resolve();
      };
      const onChange = () => {
        if (peer.iceGatheringState === "complete") done();
      };
      const onCandidate = (event: RTCPeerConnectionIceEvent) => {
        if (event.candidate) done();
      };
      peer.addEventListener("icegatheringstatechange", onChange);
      peer.addEventListener("icecandidate", onCandidate);
      timer = this.dependencies.setTimer(done, ICE_CANDIDATE_WAIT_MS);
    });
  }

  private takePreparation(localStream: MediaStream): BrowserPreparation | null {
    const preparation = this.preparation;
    if (!preparation) return null;
    this.preparation = null;
    ++this.preparationOperation;
    if (preparation.localStream === localStream) return preparation;
    this.disposePreparation(preparation);
    return null;
  }

  private disposePreparation(preparation: BrowserPreparation): void {
    preparation.channel.close();
    preparation.peer.close();
  }

  private cancelRotation(): void {
    if (this.rotationTimer === null) return;
    this.dependencies.clearTimer(this.rotationTimer);
    this.rotationTimer = null;
  }

  private cancelDisconnect(): void {
    if (this.disconnectTimer === null) return;
    this.dependencies.clearTimer(this.disconnectTimer);
    this.disconnectTimer = null;
  }

  private async disposeConnection(connection: BrowserConnection, notifyBackend: boolean): Promise<void> {
    if (connection.bargeInTimer !== null) this.dependencies.clearTimer(connection.bargeInTimer);
    connection.bargeInDetector?.destroy();
    connection.channel.close();
    connection.peer.ontrack = null;
    connection.peer.onconnectionstatechange = null;
    connection.peer.close();
    if (connection.wakeCommitWaiter) {
      this.dependencies.clearTimer(connection.wakeCommitWaiter.timer);
      connection.wakeCommitWaiter.reject(new Error("Realtime connection closed during wake handoff"));
      connection.wakeCommitWaiter = null;
    }
    await connection.handoffTransport?.destroy().catch(() => undefined);
    if (connection.ownsLocalStream) {
      for (const track of connection.localStream.getTracks()) track.stop();
    }
    if (notifyBackend) {
      await this.dependencies.closeSession(connection.session.id, connection.session.generation).catch(() => undefined);
    }
  }

  private publish(snapshot: JarvisRealtimeSnapshot): void {
    this.snapshot = snapshot;
    this.options.onSnapshot?.(snapshot);
  }

  private handleBrowserProviderEvent(
    connection: BrowserConnection | null,
    event: Record<string, unknown>,
  ): void {
    const type = typeof event.type === "string" ? event.type : "provider.unknown";
    const responseId = this.responseId(event);
    const itemId = typeof event.item_id === "string" ? event.item_id : undefined;
    const traceDetail = type.includes("transcript.delta")
      ? `chars:${typeof event.delta === "string" ? event.delta.length : 0}`
      : undefined;
    let shouldTrace = true;
    if (connection && type === "response.output_audio.delta") {
      const key = responseId ?? "unknown";
      shouldTrace = !connection.tracedAudioDeltaResponses.has(key);
      connection.tracedAudioDeltaResponses.add(key);
    } else if (connection && type.includes("transcript.delta")) {
      const key = `${type}:${responseId ?? itemId ?? "unknown"}`;
      shouldTrace = !connection.tracedTranscriptDeltaItems.has(key);
      connection.tracedTranscriptDeltaItems.add(key);
    }
    if (shouldTrace) {
      this.recordTrace(type, { response_id: responseId, item_id: itemId, detail: traceDetail });
    }
    if (!connection) return;

    if (type === "input_audio_buffer.committed" && connection.wakeCommitWaiter) {
      connection.wakeCommitWaiter.resolve(itemId);
      return;
    }
    if (type === "error" && connection.wakeCommitWaiter) {
      const error = event.error && typeof event.error === "object"
        ? event.error as Record<string, unknown>
        : {};
      if (error.event_id === connection.wakeCommitWaiter.eventId) {
        connection.wakeCommitWaiter.reject(new Error(String(error.message || "Wake audio commit failed")));
        return;
      }
    }

    if (type === "response.created") {
      connection.activeResponseId = responseId ?? connection.activeResponseId;
      connection.cancellingResponseId = null;
      return;
    }

    if (
      type === "output_audio_buffer.started"
      || type === "response.output_audio.delta"
    ) {
      if (responseId && connection.mutedResponseId !== responseId) {
        this.setPlaybackMuted(connection, false);
      }
      connection.activeResponseId = responseId ?? connection.activeResponseId;
      void this.captureReceiverStats(connection, "output_started");
      return;
    }
    if (type === "input_audio_buffer.speech_started" && connection.activeResponseId) {
      this.setPlaybackMuted(connection, true);
      this.cancelActiveResponse(connection, "provider_speech_started");
      return;
    }
    if (type === "response.done" && (!responseId || responseId === connection.activeResponseId)) {
      connection.activeResponseId = null;
      connection.cancellingResponseId = null;
      this.setPlaybackMuted(connection, false);
      return;
    }
    if (
      type === "output_audio_buffer.cleared"
      || type === "output_audio_buffer.stopped"
    ) {
      connection.activeResponseId = null;
      connection.cancellingResponseId = null;
      void this.captureReceiverStats(connection, type);
      this.setPlaybackMuted(connection, false);
    }
  }

  private handleLocalSpeechStart(
    connection: BrowserConnection | null,
    operation: number,
  ): void {
    if (!connection || operation !== this.operation || this.active?.peer !== connection.peer) return;
    this.recordTrace("local_speech_started", {
      response_id: connection.activeResponseId ?? undefined,
    });
    if (!connection.activeResponseId) return;
    this.setPlaybackMuted(connection, true);
    void this.captureReceiverStats(connection, "local_speech_started");
    if (connection.bargeInTimer !== null) return;
    connection.bargeInTimer = this.dependencies.setTimer(() => {
      connection.bargeInTimer = null;
      this.cancelActiveResponse(connection, "local_speech_confirmed");
    }, 120);
  }

  private handleLocalSpeechEnd(
    connection: BrowserConnection | null,
    operation: number,
  ): void {
    if (!connection || operation !== this.operation || this.active?.peer !== connection.peer) return;
    if (connection.bargeInTimer !== null) {
      this.dependencies.clearTimer(connection.bargeInTimer);
      connection.bargeInTimer = null;
    }
    this.recordTrace("local_speech_stopped", {
      response_id: connection.activeResponseId ?? undefined,
    });
  }

  private cancelActiveResponse(connection: BrowserConnection, reason: string): void {
    const responseId = connection.activeResponseId;
    if (
      !responseId
      || connection.cancellingResponseId === responseId
      || connection.channel.readyState !== "open"
    ) return;
    connection.cancellingResponseId = responseId;
    connection.channel.send(JSON.stringify({
      type: "response.cancel",
      event_id: `jarvis-response-cancel-${crypto.randomUUID()}`,
      response_id: responseId,
    }));
    connection.channel.send(JSON.stringify({
      type: "output_audio_buffer.clear",
      event_id: `jarvis-output-clear-${crypto.randomUUID()}`,
    }));
    this.recordTrace("response.cancel.sent", { response_id: responseId, detail: reason });
    this.recordTrace("output_audio_buffer.clear.sent", { response_id: responseId, detail: reason });
  }

  private setPlaybackMuted(connection: BrowserConnection, muted: boolean): void {
    if (muted) {
      if (connection.mutedResponseId === connection.activeResponseId) return;
      connection.mutedResponseId = connection.activeResponseId;
    } else {
      if (connection.mutedResponseId === null) return;
      connection.mutedResponseId = null;
    }
    this.recordTrace(muted ? "local_output_muted" : "local_output_unmuted", {
      response_id: connection.activeResponseId ?? undefined,
    });
    this.options.onPlaybackMuted?.(muted);
  }

  private recordTrace(
    kind: string,
    details: Omit<JarvisRealtimeTraceEntry, "kind" | "monotonic_ms" | "wall_time"> = {},
  ): void {
    const entry: JarvisRealtimeTraceEntry = {
      kind,
      monotonic_ms: this.dependencies.now(),
      wall_time: this.dependencies.wallNow(),
      ...details,
    };
    this.trace.push(entry);
    if (this.trace.length > 2_000) this.trace.splice(0, this.trace.length - 2_000);
    this.telemetryPending.push({
      trace_id: this.traceId,
      ...(this.activationId ? { activation_id: this.activationId } : {}),
      ...(this.active?.session ? {
        session_id: this.active.session.id,
        generation: this.active.session.generation,
      } : {}),
      ...(entry.response_id ? { provider_response_id: entry.response_id } : {}),
      source: "browser",
      event_type: entry.kind.slice(0, 128),
      observed_at: entry.wall_time,
      monotonic_ms: entry.monotonic_ms,
      detail: {
        ...(entry.attempt === undefined ? {} : { attempt: entry.attempt }),
        ...(entry.sequence === undefined ? {} : { sequence: entry.sequence }),
        ...(entry.server_time === undefined ? {} : { server_time: entry.server_time }),
        ...(entry.timing_ms === undefined ? {} : { timing_ms: entry.timing_ms }),
        ...(entry.detail === undefined ? {} : { detail: entry.detail.slice(0, 500) }),
      },
    });
    if (this.telemetryPending.length >= 25) void this.flushTelemetry();
  }

  private async flushTelemetry(): Promise<void> {
    if (!this.dependencies.sendTelemetry || this.telemetrySending || !this.telemetryPending.length) return;
    this.telemetrySending = true;
    const batch = this.telemetryPending.splice(0, 100);
    try {
      await this.dependencies.sendTelemetry(batch);
    } catch {
      this.telemetryPending.unshift(...batch);
      if (this.telemetryPending.length > 2_000) {
        this.telemetryPending.splice(0, this.telemetryPending.length - 2_000);
      }
    } finally {
      this.telemetrySending = false;
      if (this.telemetryPending.length >= 25) void this.flushTelemetry();
    }
  }

  private async captureReceiverStats(connection: BrowserConnection, reason: string): Promise<void> {
    if (typeof connection.peer.getReceivers !== "function") return;
    const receiver = connection.peer.getReceivers().find((item) => item.track?.kind === "audio");
    if (!receiver || typeof receiver.getStats !== "function") return;
    try {
      const reports = await receiver.getStats();
      reports.forEach((report) => {
        if (report.type !== "inbound-rtp" || report.kind !== "audio") return;
        const values = report as RTCInboundRtpStreamStats & {
          totalSamplesDuration?: number;
          jitterBufferEmittedCount?: number;
        };
        this.recordTrace("webrtc_inbound_audio", {
          response_id: connection.activeResponseId ?? undefined,
          detail: JSON.stringify({
            reason,
            timestamp: values.timestamp,
            jitter: values.jitter,
            jitter_buffer_delay: values.jitterBufferDelay,
            jitter_buffer_emitted_count: values.jitterBufferEmittedCount,
            total_samples_duration: values.totalSamplesDuration,
            concealed_samples: values.concealedSamples,
          }),
        });
      });
    } catch (cause) {
      this.recordTrace("webrtc_stats_unavailable", { detail: this.errorName(cause) });
    }
  }

  private static responseId(event: Record<string, unknown>): string | undefined {
    if (typeof event.response_id === "string") return event.response_id;
    if (event.response && typeof event.response === "object") {
      const id = (event.response as Record<string, unknown>).id;
      if (typeof id === "string") return id;
    }
    return undefined;
  }

  private responseId(event: Record<string, unknown>): string | undefined {
    return JarvisRealtimeController.responseId(event);
  }

  private errorName(cause: unknown): string {
    return cause instanceof Error ? cause.name : "unknown";
  }
}

function elapsedMilliseconds(
  start: JarvisRealtimeTraceEntry | undefined,
  end: JarvisRealtimeTraceEntry | undefined,
): number | undefined {
  if (!start || !end || end.monotonic_ms < start.monotonic_ms) return undefined;
  return Math.round((end.monotonic_ms - start.monotonic_ms) * 10) / 10;
}

function firstTrace(
  entries: readonly JarvisRealtimeTraceEntry[],
  kinds: readonly string[],
  startIndex: number,
  endIndex = entries.length,
): JarvisRealtimeTraceEntry | undefined {
  for (let index = Math.max(0, startIndex); index < Math.min(endIndex, entries.length); index += 1) {
    if (kinds.includes(entries[index].kind)) return entries[index];
  }
  return undefined;
}

function lastTrace(
  entries: readonly JarvisRealtimeTraceEntry[],
  kinds: readonly string[],
  startIndex: number,
  endIndex: number,
): JarvisRealtimeTraceEntry | undefined {
  for (let index = Math.min(endIndex, entries.length) - 1; index >= Math.max(0, startIndex); index -= 1) {
    if (kinds.includes(entries[index].kind)) return entries[index];
  }
  return undefined;
}

/** Produce a compact, turn-oriented view while retaining the raw trace for deeper diagnosis. */
export function summarizeJarvisRealtimeTrace(
  entries: readonly JarvisRealtimeTraceEntry[],
): JarvisRealtimeTimingSummary {
  const attemptIndexes = entries
    .map((entry, index) => entry.kind === "realtime_connection_attempt_started" ? index : -1)
    .filter((index) => index >= 0);
  const connections = attemptIndexes.map((startIndex, connectionIndex) => {
    const endIndex = attemptIndexes[connectionIndex + 1] ?? entries.length;
    const attemptStart = entries[startIndex];
    const requestStart = lastTrace(entries, ["realtime_start_requested"], 0, startIndex + 1)
      ?? attemptStart;
    const connectionReady = firstTrace(entries, ["realtime_connection_ready"], startIndex, endIndex);
    const backendStart = firstTrace(entries, ["realtime_backend_call_started"], startIndex, endIndex);
    const backendEnd = firstTrace(entries, ["realtime_backend_call_completed"], startIndex, endIndex);
    const remoteDescription = firstTrace(entries, ["realtime_remote_description_set"], startIndex, endIndex);
    const offerEnd = firstTrace(entries, ["realtime_offer_ready"], startIndex, endIndex)
      ?? lastTrace(entries, ["realtime_offer_ready"], 0, startIndex + 1);
    const offerStart = offerEnd
      ? lastTrace(
          entries,
          ["realtime_offer_started"],
          0,
          entries.indexOf(offerEnd) + 1,
        )
      : undefined;
    const wakeDetected = lastTrace(entries, ["wake_detected"], 0, startIndex + 1);
    const providerReady = firstTrace(entries, ["backend.provider.ready"], startIndex, endIndex);
    return {
      attempt: attemptStart.attempt ?? connectionIndex + 1,
      total_ms: elapsedMilliseconds(requestStart, connectionReady),
      offer_ms: elapsedMilliseconds(offerStart, offerEnd),
      backend_call_ms: elapsedMilliseconds(backendStart, backendEnd),
      remote_description_ms: elapsedMilliseconds(backendEnd, remoteDescription),
      wake_detect_to_connected_ms: elapsedMilliseconds(wakeDetected, connectionReady),
      provider_call_ms: providerReady?.timing_ms?.provider_call,
      sideband_connect_ms: providerReady?.timing_ms?.sideband_connect,
    };
  });

  const stopIndexes = entries
    .map((entry, index) => entry.kind === "input_audio_buffer.speech_stopped" ? index : -1)
    .filter((index) => index >= 0);
  const turns = stopIndexes.map((stopIndex, turnIndex) => {
    const segmentStart = turnIndex === 0 ? 0 : stopIndexes[turnIndex - 1] + 1;
    const segmentEnd = stopIndexes[turnIndex + 1] ?? entries.length;
    const providerStop = entries[stopIndex];
    const localStop = lastTrace(
      entries,
      ["wake_local_speech_stopped", "local_speech_stopped"],
      segmentStart,
      stopIndex + 1,
    );
    const wakeDetected = lastTrace(entries, ["wake_detected"], segmentStart, stopIndex + 1);
    const utteranceReady = firstTrace(
      entries,
      ["wake_utterance_ready"],
      localStop ? entries.indexOf(localStop) : segmentStart,
      stopIndex + 1,
    );
    const responseCreated = firstTrace(entries, ["response.created"], stopIndex + 1, segmentEnd);
    const responseId = responseCreated?.response_id;
    const firstAudio = firstTrace(
      entries,
      ["response.output_audio.delta"],
      responseCreated ? entries.indexOf(responseCreated) + 1 : stopIndex + 1,
      segmentEnd,
    );
    const playbackStarted = firstTrace(
      entries,
      ["output_audio_buffer.started"],
      responseCreated ? entries.indexOf(responseCreated) + 1 : stopIndex + 1,
      segmentEnd,
    );
    const arbiter = firstTrace(
      entries,
      ["backend.response.arbiter.started"],
      stopIndex + 1,
      segmentEnd,
    );
    return {
      turn: turnIndex + 1,
      kind: wakeDetected ? "wake" : "follow_up",
      response_id: responseId,
      local_speech_stop_to_provider_stop_ms: elapsedMilliseconds(localStop, providerStop),
      wake_handoff_tail_ms: elapsedMilliseconds(localStop, utteranceReady),
      provider_stop_to_response_created_ms: elapsedMilliseconds(providerStop, responseCreated),
      response_created_to_first_audio_ms: elapsedMilliseconds(responseCreated, firstAudio),
      provider_stop_to_playback_started_ms: elapsedMilliseconds(providerStop, playbackStarted),
      local_speech_stop_to_playback_started_ms: elapsedMilliseconds(localStop, playbackStarted),
      backend_arbiter_ms: arbiter?.timing_ms?.total_to_provider_submit,
    } satisfies JarvisRealtimeTurnTiming;
  });

  return {
    schema: "jarvis.realtime_timing.v1",
    connections,
    turns,
  };
}

export function encodeJarvisWakeAudio(
  samples: Float32Array,
  sourceSampleRate: number,
): JarvisRealtimeWakeAudioEnvelope {
  if (!Number.isFinite(sourceSampleRate) || sourceSampleRate <= 0) {
    throw new Error("Wake audio sample rate must be positive");
  }
  if (!samples.length) {
    return {
      chunks: [],
      byteLength: 0,
      durationMs: 0,
      sourceSampleRate,
      targetSampleRate: WAKE_PCM_SAMPLE_RATE,
    };
  }
  const outputLength = Math.max(1, Math.round(samples.length * WAKE_PCM_SAMPLE_RATE / sourceSampleRate));
  const pcmBytes = new Uint8Array(outputLength * 2);
  const view = new DataView(pcmBytes.buffer);
  for (let index = 0; index < outputLength; index += 1) {
    const sourcePosition = index * sourceSampleRate / WAKE_PCM_SAMPLE_RATE;
    const lower = Math.min(samples.length - 1, Math.floor(sourcePosition));
    const upper = Math.min(samples.length - 1, lower + 1);
    const fraction = sourcePosition - lower;
    const sample = Math.max(
      -1,
      Math.min(1, samples[lower] + (samples[upper] - samples[lower]) * fraction),
    );
    const integer = sample < 0 ? Math.round(sample * 32_768) : Math.round(sample * 32_767);
    view.setInt16(index * 2, integer, true);
  }
  const chunks: string[] = [];
  for (let offset = 0; offset < pcmBytes.length; offset += WAKE_PCM_CHUNK_BYTES) {
    const chunk = pcmBytes.subarray(offset, Math.min(offset + WAKE_PCM_CHUNK_BYTES, pcmBytes.length));
    let binary = "";
    for (let index = 0; index < chunk.length; index += 1) binary += String.fromCharCode(chunk[index]);
    chunks.push(globalThis.btoa(binary));
  }
  return {
    chunks,
    byteLength: pcmBytes.length,
    durationMs: samples.length / sourceSampleRate * 1_000,
    sourceSampleRate,
    targetSampleRate: WAKE_PCM_SAMPLE_RATE,
  };
}

async function createBrowserHandoffTransport(
  localStream: MediaStream,
): Promise<JarvisRealtimeHandoffTransport> {
  const context = new AudioContext();
  const liveSource = context.createMediaStreamSource(localStream);
  const destination = context.createMediaStreamDestination();
  let destroyed = false;
  let connected = false;
  return {
    stream: destination.stream,
    connectLive: async () => {
      if (destroyed || connected) return;
      connected = true;
      if (context.state === "suspended") await context.resume();
      liveSource.connect(destination);
    },
    destroy: async () => {
      if (destroyed) return;
      destroyed = true;
      liveSource.disconnect();
      for (const track of destination.stream.getTracks()) track.stop();
      await context.close();
    },
  };
}
