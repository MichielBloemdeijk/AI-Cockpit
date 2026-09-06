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
  backend_control_ready_ms?: number;
  data_channel_ready_ms?: number;
  wake_detect_to_backend_call_ms?: number;
  wake_detect_to_data_channel_ms?: number;
}

export interface JarvisRealtimeTurnTiming {
  turn: number;
  kind: "wake" | "follow_up";
  input_item_id?: string;
  boundary_monotonic_ms: number;
  response_id?: string;
  local_speech_stop_to_provider_stop_ms?: number;
  wake_handoff_tail_ms?: number;
  provider_stop_to_response_created_ms?: number;
  response_created_to_first_audio_ms?: number;
  provider_stop_to_first_playable_media_ms?: number;
  local_speech_stop_to_first_playable_media_ms?: number;
  backend_arbiter_ms?: number;
  provider_boundary_to_transcript_ms?: number;
  transcript_to_arbiter_ms?: number;
  arbiter_to_response_created_ms?: number;
  response_create_sent_to_created_ms?: number;
  response_created_to_first_audio_provider_ms?: number;
  first_audio_to_first_playable_media_ms?: number;
  response_created_to_first_playable_media_ms?: number;
  wake_audio_upload_ms?: number;
  wake_commit_ack_ms?: number;
  local_speech_stop_to_commit_ms?: number;
  provider_connection_ms?: number;
  transcription_ms?: number;
  response_arbitration_ms?: number;
  generation_ms?: number;
  outcome?: "complete" | "no_transcript" | "no_arbiter" | "no_response" | "no_audio" | "no_playable_media";
  bottleneck?: "endpointing" | "provider_connection" | "audio_handoff" | "transcription" | "response_arbitration" | "generation" | "media_arrival";
  bottleneck_ms?: number;
}

export interface JarvisRealtimeTimingSummary {
  schema: "jarvis.realtime_timing.v2";
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
  wakeCommitWaiter: WakeCommitWaiter | null;
  receiverStatsBaseline: ReceiverAudioSnapshot | null;
  receiverStatsChain: Promise<void>;
  receiverStatsTimer: number | null;
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

interface BackendReadyWaiter {
  timer: number;
  resolve: (observation: BackendReadinessObservation) => void;
  reject: (cause: Error) => void;
}

interface BackendReadinessObservation {
  ready: boolean;
  error?: string;
  eventType: string;
  sessionId: string;
  generation: number;
  sequence: number;
  serverCreatedAt: string;
  receivedMonotonic: number;
  receivedWallTimeMs: number;
  uncalibratedDeliveryMs?: number;
}

interface RealtimeClockAlignment {
  offsetMs: number;
  uncertaintyMs: number;
  networkRttMs: number;
}

interface ReceiverAudioSnapshot {
  responseId: string;
  bytesReceived: number;
  packetsReceived?: number;
  totalSamplesReceived?: number;
  timestamp?: number;
}

interface JarvisRealtimeConnectionInput {
  localStream: MediaStream;
  ownsLocalStream: boolean;
  wakeAudio?: Promise<JarvisRealtimeWakeAudioEnvelope>;
  activationContext?: JarvisRealtimeActivationContext;
}

type ConnectionAttemptStage =
  | "media"
  | "offer"
  | "backend_call"
  | "remote_description"
  | "backend_control"
  | "data_channel"
  | "wake_upload"
  | "commit"
  | "microphone_activation"
  | "cleanup";

interface ConnectionAttemptTracker {
  attempt: number;
  startedMonotonic: number;
  stageStartedMonotonic: number;
  stage: ConnectionAttemptStage;
  timingMs: Partial<Record<ConnectionAttemptStage, number>>;
  session: JarvisSession | null;
  wakeAudioRetained: boolean;
  clockAlignment: RealtimeClockAlignment | null;
}

interface ConnectionTransportSnapshot {
  peer: string | null;
  ice: string | null;
  ice_gathering: string | null;
  signaling: string | null;
  sctp: string | null;
  data_channel: string | null;
  microphone_enabled: boolean | null;
  microphone_muted: boolean | null;
  microphone_ready_state: string | null;
  wake_audio_retained: boolean;
}

interface ConnectionNetworkSnapshot {
  local_candidate_type?: string;
  remote_candidate_type?: string;
  rtt_ms?: number;
  bytes_sent?: number;
  bytes_received?: number;
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
  setTelemetryTimer: (callback: () => void, milliseconds: number) => number;
  clearTelemetryTimer: (timer: number) => void;
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
const TELEMETRY_AFTER_AUDIO_DELAY_MS = 1_000;
const TELEMETRY_MISSING_RESPONSE_DELAY_MS = 8_000;
const RECEIVER_STATS_FOLLOW_UP_MS = 100;
const TURN_RESULT_SETTLE_MS = 150;
const LIVE_STREAM_ERROR_COALESCE_MS = 30_000;
const WAKE_PCM_SAMPLE_RATE = 24_000 as const;
const WAKE_PCM_CHUNK_BYTES = 24_000;
const BACKEND_TRACE_EVENTS = new Set([
  "provider.ready",
  "provider.user_speaking",
  "provider.user_stopped",
  "provider.wake_audio_committed",
  "provider.user_transcript",
  "wake.verification.accepted",
  "wake.verification.rejected",
  "response.arbiter.started",
  "provider.assistant_generating",
  "provider.assistant_speaking",
  "provider.assistant_response",
  "provider.assistant_interrupted",
  "provider.provider_error",
  "provider.disconnected",
]);
const BROWSER_PROVIDER_TRACE_EVENTS = new Set([
  "session.created",
  "session.updated",
  "input_audio_buffer.speech_started",
  "input_audio_buffer.speech_stopped",
  "input_audio_buffer.committed",
  "conversation.item.input_audio_transcription.completed",
  "response.created",
  "response.output_audio.delta",
  "output_audio_buffer.started",
  "output_audio_buffer.stopped",
  "response.done",
  "error",
]);

const browserDependencies: JarvisRealtimeDependencies = {
  createPeer: () => new RTCPeerConnection(),
  getUserMedia: (constraints) => navigator.mediaDevices.getUserMedia(constraints),
  createCall: createJarvisRealtimeCall,
  closeSession: closeJarvisRealtimeSession,
  sendTelemetry: submitJarvisRealtimeTelemetry,
  wait: (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds)),
  setTimer: (callback, milliseconds) => window.setTimeout(callback, milliseconds),
  clearTimer: (timer) => window.clearTimeout(timer),
  setTelemetryTimer: (callback, milliseconds) => window.setTimeout(callback, milliseconds),
  clearTelemetryTimer: (timer) => window.clearTimeout(timer),
  now: () => performance.now(),
  wallNow: () => new Date().toISOString(),
  createBargeInDetector: createJarvisBargeInDetector,
  createHandoffTransport: createBrowserHandoffTransport,
};

export class JarvisRealtimeController {
  private readonly options: JarvisRealtimeControllerOptions;
  private readonly dependencies: JarvisRealtimeDependencies;
  private active: BrowserConnection | null = null;
  private connectingSession: JarvisSession | null = null;
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
  private telemetrySendPromise: Promise<void> | null = null;
  private telemetryFlushTimer: number | null = null;
  private readonly backendReadiness = new Map<string, BackendReadinessObservation>();
  private readonly clockAlignments = new Map<string, RealtimeClockAlignment>();
  private readonly backendReadyWaiters = new Map<string, BackendReadyWaiter>();
  private lastBackendReadiness: BackendReadinessObservation | null = null;
  private liveStreamState: "idle" | "open" | "error" = "idle";
  private liveStreamErrorCount = 0;
  private lastLiveStreamErrorMonotonic = Number.NEGATIVE_INFINITY;
  private playbackPlaying = false;
  private playbackMuted = false;
  private readonly reportedTurns = new Set<string>();
  private readonly turnResultTimers = new Set<number>();
  private lastTurnBoundaryMonotonic = Number.NEGATIVE_INFINITY;
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
    if (kind === "audio_element_playing" || kind === "audio_play_resolved") {
      this.playbackPlaying = true;
    } else if (kind === "audio_element_paused") {
      this.playbackPlaying = false;
    } else if (kind === "audio_element_muted") {
      this.playbackMuted = true;
    } else if (kind === "audio_element_unmuted") {
      this.playbackMuted = false;
    }
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
    const sessionId = typeof payload.session_id === "string" ? payload.session_id : null;
    const generation = typeof payload.generation === "number" ? payload.generation : null;
    if (sessionId && generation !== null) {
      const key = this.sessionKey(sessionId, generation);
      if (
        event.event_type === "provider.ready"
        || ["provider.provider_error", "provider.disconnected"].includes(event.event_type)
      ) {
        const reason = typeof payload.reason === "string" ? payload.reason : undefined;
        const receivedWall = Date.parse(this.dependencies.wallNow());
        const serverCreated = Date.parse(event.created_at);
        const clockAlignment = this.clockAlignments.get(key);
        const observation: BackendReadinessObservation = {
          ready: event.event_type === "provider.ready",
          ...(event.event_type === "provider.ready"
            ? {}
            : { error: reason || "Realtime backend control connection failed" }),
          eventType: event.event_type,
          sessionId,
          generation,
          sequence: event.sequence,
          serverCreatedAt: event.created_at,
          receivedMonotonic: this.dependencies.now(),
          receivedWallTimeMs: receivedWall,
          ...(Number.isFinite(receivedWall) && Number.isFinite(serverCreated)
            ? { uncalibratedDeliveryMs: Math.max(0, receivedWall - serverCreated) }
            : {}),
        };
        this.lastBackendReadiness = observation;
        this.setBackendReadiness(key, observation);
        const currentForReadiness = this.active?.session ?? this.connectingSession;
        this.recordTrace("backend.readiness_event_received", {
          sequence: event.sequence,
          server_time: event.created_at,
          timing_ms: observation.uncalibratedDeliveryMs === undefined
            ? undefined
            : {
                uncalibrated_delivery: observation.uncalibratedDeliveryMs,
                ...(clockAlignment
                  ? this.backendEventDeliveryTiming(observation, clockAlignment)
                  : {}),
              },
          detail: JSON.stringify({
            event_type: event.event_type,
            observed_session_id: sessionId,
            observed_generation: generation,
            expected_session_id: currentForReadiness?.id ?? null,
            expected_generation: currentForReadiness?.generation ?? null,
            matches_current: Boolean(
              currentForReadiness
              && currentForReadiness.id === sessionId
              && currentForReadiness.generation === generation
            ),
          }),
        });
      }
      const current = this.active?.session ?? this.connectingSession;
      if (
        !current
        || current.id !== sessionId
        || current.generation !== generation
      ) return;
    }
    if (!BACKEND_TRACE_EVENTS.has(event.event_type)) return;
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
    if ([
      "provider.assistant_response",
      "provider.assistant_interrupted",
      "provider.provider_error",
      "provider.disconnected",
    ].includes(event.event_type)) {
      this.scheduleTelemetryFlush(TELEMETRY_AFTER_AUDIO_DELAY_MS);
    }
  }

  recordLiveStreamState(
    state: "opened" | "errored",
    cursor: number,
    readyState?: number,
  ): void {
    const now = this.dependencies.now();
    if (state === "errored") {
      if (
        this.liveStreamState === "error"
        && now - this.lastLiveStreamErrorMonotonic < LIVE_STREAM_ERROR_COALESCE_MS
      ) {
        this.liveStreamErrorCount += 1;
        this.lastLiveStreamErrorMonotonic = now;
        return;
      }
      this.liveStreamState = "error";
      this.liveStreamErrorCount = 1;
      this.lastLiveStreamErrorMonotonic = now;
      this.recordTrace("live_stream.errored", {
        sequence: cursor,
        detail: JSON.stringify({ cursor, ready_state: readyState ?? null }),
      });
      return;
    }
    const eventType = this.liveStreamState === "error"
      ? "live_stream.reconnected"
      : "live_stream.opened";
    const repeatedErrors = this.liveStreamErrorCount;
    this.liveStreamState = "open";
    this.liveStreamErrorCount = 0;
    this.recordTrace(eventType, {
      sequence: cursor,
      detail: JSON.stringify({
        cursor,
        ready_state: readyState ?? null,
        ...(eventType === "live_stream.reconnected" ? { error_count: repeatedErrors } : {}),
      }),
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
    this.cancelBackendReadyWaiters();
    this.cancelTelemetryFlush();
    this.cancelTurnResultTimers();
    const preparation = this.preparation;
    this.preparation = null;
    if (preparation) this.disposePreparation(preparation);
    const current = this.active;
    this.active = null;
    this.connectingSession = null;
    if (current) await this.disposeConnection(current, true);
    this.recordUnreportedTurnResults();
    await this.flushTelemetry(true);
    this.options.onRemoteStream?.(null);
    this.options.onPlaybackMuted?.(false);
    this.playbackPlaying = false;
    this.playbackMuted = false;
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
        this.connectingSession = null;
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
        this.scheduleTelemetryFlush(TELEMETRY_MISSING_RESPONSE_DELAY_MS);
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
    await this.flushTelemetry(true);
    throw new Error(error);
  }

  private async connectAttempt(
    operation: number,
    input?: JarvisRealtimeConnectionInput,
    attempt = 1,
  ): Promise<BrowserConnection> {
    const started = this.dependencies.now();
    const tracker: ConnectionAttemptTracker = {
      attempt,
      startedMonotonic: started,
      stageStartedMonotonic: started,
      stage: "media",
      timingMs: {},
      session: null,
      wakeAudioRetained: Boolean(input?.wakeAudio),
      clockAlignment: null,
    };
    let localStream: MediaStream | null = null;
    let ownsLocalStream = false;
    let prepared: BrowserPreparation | null = null;
    let peer: RTCPeerConnection | null = null;
    let channel: RTCDataChannel | null = null;
    let providerSession: JarvisSession | null = null;
    let connection: BrowserConnection | null = null;
    let handoffTransport: JarvisRealtimeHandoffTransport | null = null;
    try {
      const mediaStarted = this.dependencies.now();
      localStream = input?.localStream ?? await this.dependencies.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      ownsLocalStream = input?.ownsLocalStream ?? true;
      this.recordTrace("realtime_local_stream_ready", {
        attempt,
        detail: `wait_ms:${Math.round(this.dependencies.now() - mediaStarted)}`,
      });
      prepared = this.takePreparation(localStream);
      peer = prepared?.peer ?? this.dependencies.createPeer();
      channel = prepared?.channel ?? peer.createDataChannel("oai-events");
      this.recordTrace("wake_handoff_transport_started", { attempt });
      handoffTransport = await this.dependencies.createHandoffTransport(localStream);
      const outgoingStream = handoffTransport.stream;
      this.recordTrace("wake_handoff_transport_ready", { attempt });
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

      this.enterConnectionStage(tracker, "offer");
      if (prepared) this.recordTrace("realtime_offer_reused", { attempt });
      else this.recordTrace("realtime_offer_started", { attempt });
      const offer = prepared ? await prepared.offer : await this.createLocalOffer(peer);
      if (!prepared) this.recordTrace("realtime_offer_ready", { attempt });
      const sdpOffer = peer.localDescription?.sdp || offer.sdp;
      if (!sdpOffer) throw new Error("Browser did not produce an SDP offer");
      this.enterConnectionStage(tracker, "backend_call");
      this.recordTrace("realtime_backend_call_started", { attempt });
      const clientSentAtMs = Date.parse(this.dependencies.wallNow());
      const response = await this.dependencies.createCall(
        this.options.controllerId,
        "controller",
        "openai",
        sdpOffer,
        undefined,
        input?.activationContext,
      );
      const clientReceivedAtMs = Date.parse(this.dependencies.wallNow());
      this.recordTrace("realtime_backend_call_completed", { attempt });
      providerSession = response.session;
      tracker.session = providerSession;
      tracker.clockAlignment = estimateRealtimeClockAlignment(
        clientSentAtMs,
        Date.parse(response.server_received_at),
        Date.parse(response.server_responded_at),
        clientReceivedAtMs,
      );
      if (providerSession && tracker.clockAlignment) {
        this.setClockAlignment(
          this.sessionKey(providerSession.id, providerSession.generation),
          tracker.clockAlignment,
        );
      }
      this.connectingSession = providerSession;
      if (!providerSession || !response.sdp_answer) {
        throw new Error("Backend did not return a complete Realtime answer");
      }
      this.enterConnectionStage(tracker, "remote_description");
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
        wakeCommitWaiter: null,
        receiverStatsBaseline: null,
        receiverStatsChain: Promise.resolve(),
        receiverStatsTimer: null,
      };
      this.enterConnectionStage(tracker, "backend_control");
      await this.waitForBackendControl(providerSession, operation, attempt);
      if (input?.wakeAudio) {
        this.enterConnectionStage(tracker, "data_channel");
        await this.waitForDataChannelOpen(channel, operation, attempt);
        const wakeAudio = await input.wakeAudio;
        await this.submitWakeAudio(connection, handoffTransport, wakeAudio, attempt, tracker);
        tracker.wakeAudioRetained = false;
      } else {
        this.enterConnectionStage(tracker, "microphone_activation");
        await handoffTransport.connectLive();
        this.recordTrace("realtime_live_mic_activated", { attempt });
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
      this.completeConnectionStage(tracker);
      const successTiming = this.connectionTimingSnapshot(tracker);
      const successTransport = this.connectionTransportSnapshot(peer, channel, localStream, tracker);
      void this.recordConnectionAttemptSucceeded(
        tracker,
        peer,
        successTiming,
        successTransport,
      );
      return connection;
    } catch (cause) {
      const failedStage = tracker.stage;
      this.recordTrace("connection.attempt.failed", {
        attempt,
        timing_ms: this.connectionTimingSnapshot(tracker),
        detail: JSON.stringify({
          result: "failed",
          failed_stage: failedStage,
          error_code: this.connectionErrorCode(failedStage, cause),
          session_id: providerSession?.id ?? null,
          generation: providerSession?.generation ?? null,
          transport: this.connectionTransportSnapshot(peer, channel, localStream, tracker),
          clock: tracker.clockAlignment === null ? null : {
            offset_ms: Math.round(tracker.clockAlignment.offsetMs * 10) / 10,
            uncertainty_ms: Math.round(tracker.clockAlignment.uncertaintyMs * 10) / 10,
            network_rtt_ms: Math.round(tracker.clockAlignment.networkRttMs * 10) / 10,
          },
        }),
      });
      this.enterConnectionStage(tracker, "cleanup");
      const cleanupErrors: string[] = [];
      try {
        await handoffTransport?.destroy();
      } catch (cleanupCause) {
        cleanupErrors.push(`handoff:${this.errorName(cleanupCause)}`);
      }
      try {
        channel?.close();
      } catch (cleanupCause) {
        cleanupErrors.push(`channel:${this.errorName(cleanupCause)}`);
      }
      try {
        peer?.close();
      } catch (cleanupCause) {
        cleanupErrors.push(`peer:${this.errorName(cleanupCause)}`);
      }
      if (ownsLocalStream && localStream) {
        for (const track of localStream.getTracks()) {
          try {
            track.stop();
          } catch (cleanupCause) {
            cleanupErrors.push(`track:${this.errorName(cleanupCause)}`);
          }
        }
      }
      if (providerSession) {
        try {
          await this.dependencies.closeSession(providerSession.id, providerSession.generation);
        } catch (cleanupCause) {
          cleanupErrors.push(`backend:${this.errorName(cleanupCause)}`);
        }
      }
      this.completeConnectionStage(tracker);
      this.recordTrace("connection.attempt.cleanup", {
        attempt,
        timing_ms: { cleanup: tracker.timingMs.cleanup ?? 0 },
        detail: JSON.stringify({
          result: cleanupErrors.length ? "partial" : "complete",
          failed_stage: failedStage,
          error_codes: cleanupErrors,
          session_id: providerSession?.id ?? null,
          generation: providerSession?.generation ?? null,
        }),
      });
      this.scheduleTelemetryFlush(0);
      if (this.connectingSession === providerSession) this.connectingSession = null;
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

  private async waitForBackendControl(
    session: JarvisSession,
    operation: number,
    attempt: number,
  ): Promise<void> {
    const waitStarted = this.dependencies.now();
    const key = this.sessionKey(session.id, session.generation);
    this.recordTrace("realtime_backend_control_wait_started", {
      attempt,
      detail: JSON.stringify({ session_id: session.id, generation: session.generation }),
    });
    try {
      if (session.lifecycle_state === "ready") {
        this.recordBackendControlResult(session, attempt, waitStarted, "response");
        return;
      }
      if (["disconnected", "superseded"].includes(session.lifecycle_state)) {
        throw new Error("Realtime backend control connection failed");
      }
      const observed = this.backendReadiness.get(key);
      if (observed?.ready) {
        this.recordBackendControlResult(session, attempt, waitStarted, "cached_event", observed);
        return;
      }
      if (observed?.error) throw new Error(observed.error);
      const liveObservation = await new Promise<BackendReadinessObservation>((resolve, reject) => {
        const waiter: BackendReadyWaiter = { timer: 0, resolve, reject };
        waiter.timer = this.dependencies.setTimer(() => {
          if (this.backendReadyWaiters.get(key) === waiter) this.backendReadyWaiters.delete(key);
          reject(new Error("Realtime backend control did not become ready in time"));
        }, DATA_CHANNEL_OPEN_TIMEOUT_MS);
        this.backendReadyWaiters.set(key, waiter);
      });
      if (operation !== this.operation) throw new Error("Realtime connection was cancelled");
      this.recordBackendControlResult(session, attempt, waitStarted, "live_event", liveObservation);
    } catch (cause) {
      this.recordTrace("realtime_backend_control_wait_failed", {
        attempt,
        timing_ms: { wait: Math.max(0, this.dependencies.now() - waitStarted) },
        detail: JSON.stringify({
          session_id: session.id,
          generation: session.generation,
          error: this.errorName(cause),
          last_readiness_event: this.lastBackendReadiness === null ? null : {
            event_type: this.lastBackendReadiness.eventType,
            session_id: this.lastBackendReadiness.sessionId,
            generation: this.lastBackendReadiness.generation,
            sequence: this.lastBackendReadiness.sequence,
          },
        }),
      });
      throw cause;
    }
  }

  private recordBackendControlResult(
    session: JarvisSession,
    attempt: number,
    waitStarted: number,
    source: "response" | "cached_event" | "live_event",
    observation?: BackendReadinessObservation,
  ): void {
    const alignment = this.clockAlignments.get(this.sessionKey(session.id, session.generation));
    this.recordTrace("realtime_backend_control_ready", {
      attempt,
      sequence: observation?.sequence,
      server_time: observation?.serverCreatedAt,
      timing_ms: {
        wait: Math.max(0, this.dependencies.now() - waitStarted),
        ...(observation?.uncalibratedDeliveryMs === undefined
          ? {}
          : { uncalibrated_delivery: observation.uncalibratedDeliveryMs }),
        ...(observation && alignment
          ? this.backendEventDeliveryTiming(observation, alignment)
          : {}),
      },
      detail: JSON.stringify({
        source,
        session_id: session.id,
        generation: session.generation,
        observed_event_type: observation?.eventType ?? null,
      }),
    });
  }

  private setBackendReadiness(
    key: string,
    state: BackendReadinessObservation,
  ): void {
    this.backendReadiness.set(key, state);
    while (this.backendReadiness.size > 32) {
      const oldest = this.backendReadiness.keys().next().value;
      if (typeof oldest !== "string") break;
      this.backendReadiness.delete(oldest);
    }
    const waiter = this.backendReadyWaiters.get(key);
    if (!waiter) return;
    this.backendReadyWaiters.delete(key);
    this.dependencies.clearTimer(waiter.timer);
    if (state.ready) waiter.resolve(state);
    else waiter.reject(new Error(state.error || "Realtime backend control connection failed"));
  }

  private cancelBackendReadyWaiters(): void {
    for (const waiter of this.backendReadyWaiters.values()) {
      this.dependencies.clearTimer(waiter.timer);
      waiter.reject(new Error("Realtime connection was cancelled"));
    }
    this.backendReadyWaiters.clear();
  }

  private sessionKey(sessionId: string, generation: number): string {
    return `${sessionId}:${generation}`;
  }

  private setClockAlignment(key: string, alignment: RealtimeClockAlignment): void {
    this.clockAlignments.set(key, alignment);
    while (this.clockAlignments.size > 32) {
      const oldest = this.clockAlignments.keys().next().value;
      if (typeof oldest !== "string") break;
      this.clockAlignments.delete(oldest);
    }
  }

  private backendEventDeliveryTiming(
    observation: BackendReadinessObservation,
    alignment: RealtimeClockAlignment,
  ): Record<string, number> {
    const serverCreatedAtMs = Date.parse(observation.serverCreatedAt);
    if (!Number.isFinite(serverCreatedAtMs) || !Number.isFinite(observation.receivedWallTimeMs)) {
      return { clock_uncertainty: alignment.uncertaintyMs };
    }
    return {
      calibrated_delivery: Math.max(
        0,
        observation.receivedWallTimeMs - (serverCreatedAtMs - alignment.offsetMs),
      ),
      clock_uncertainty: alignment.uncertaintyMs,
    };
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
    tracker: ConnectionAttemptTracker,
  ): Promise<void> {
    if (!envelope.chunks.length) {
      this.enterConnectionStage(tracker, "microphone_activation");
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
      this.enterConnectionStage(tracker, "wake_upload");
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
      this.enterConnectionStage(tracker, "microphone_activation");
      await handoffTransport.connectLive();
      this.recordTrace("wake_live_mic_activated", { attempt });
      this.enterConnectionStage(tracker, "commit");
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
    if (connection.receiverStatsTimer !== null) {
      this.dependencies.clearTelemetryTimer(connection.receiverStatsTimer);
      connection.receiverStatsTimer = null;
    }
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
    let shouldTrace = BROWSER_PROVIDER_TRACE_EVENTS.has(type);
    if (connection && type === "response.output_audio.delta") {
      const key = responseId ?? "unknown";
      shouldTrace = shouldTrace && !connection.tracedAudioDeltaResponses.has(key);
      connection.tracedAudioDeltaResponses.add(key);
    }
    if (shouldTrace) {
      this.recordTrace(type, { response_id: responseId, item_id: itemId });
    }
    if (["output_audio_buffer.started", "response.done"].includes(type)) {
      this.scheduleTelemetryFlush(TELEMETRY_AFTER_AUDIO_DELAY_MS);
    } else if (type === "error") {
      this.scheduleTelemetryFlush(0);
    }
    if (!connection) return;

    if (type === "input_audio_buffer.speech_stopped" || type === "input_audio_buffer.committed") {
      this.scheduleTurnResult(TELEMETRY_MISSING_RESPONSE_DELAY_MS, true);
    }

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
      if (connection.activeResponseId) {
        this.queueReceiverStatsSample(
          connection,
          connection.activeResponseId,
          "response_created",
          true,
        );
      }
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
      if (type === "output_audio_buffer.started" && connection.activeResponseId) {
        const activeResponseId = connection.activeResponseId;
        this.queueReceiverStatsSample(connection, activeResponseId, "output_started");
        if (connection.receiverStatsTimer !== null) {
          this.dependencies.clearTelemetryTimer(connection.receiverStatsTimer);
        }
        connection.receiverStatsTimer = this.dependencies.setTelemetryTimer(() => {
          connection.receiverStatsTimer = null;
          this.queueReceiverStatsSample(connection, activeResponseId, "output_started_plus_100ms");
        }, RECEIVER_STATS_FOLLOW_UP_MS);
      }
      return;
    }
    if (type === "input_audio_buffer.speech_started" && connection.activeResponseId) {
      this.setPlaybackMuted(connection, true);
      this.cancelActiveResponse(connection, "provider_speech_started");
      return;
    }
    if (type === "response.done" && (!responseId || responseId === connection.activeResponseId)) {
      this.scheduleTurnResult(TURN_RESULT_SETTLE_MS);
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
    this.playbackMuted = muted;
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
    const traceSession = this.active?.session ?? this.connectingSession;
    this.telemetryPending.push({
      trace_id: this.traceId,
      ...(this.activationId ? { activation_id: this.activationId } : {}),
      ...(traceSession ? {
        session_id: traceSession.id,
        generation: traceSession.generation,
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
  }

  private scheduleTelemetryFlush(milliseconds: number): void {
    if (!this.dependencies.sendTelemetry) return;
    this.cancelTelemetryFlush();
    this.telemetryFlushTimer = this.dependencies.setTelemetryTimer(() => {
      this.telemetryFlushTimer = null;
      void this.flushTelemetry();
    }, Math.max(0, milliseconds));
  }

  private cancelTelemetryFlush(): void {
    if (this.telemetryFlushTimer === null) return;
    this.dependencies.clearTelemetryTimer(this.telemetryFlushTimer);
    this.telemetryFlushTimer = null;
  }

  private async flushTelemetry(drain = false): Promise<void> {
    if (!this.dependencies.sendTelemetry) return;
    if (this.telemetrySending) {
      const inFlight = this.telemetrySendPromise;
      if (drain && inFlight) {
        await inFlight;
        await this.flushTelemetry(true);
      }
      return;
    }
    if (!this.telemetryPending.length) return;
    this.telemetrySending = true;
    const batch = this.telemetryPending.splice(0, 100);
    let sent = false;
    const send = (async () => {
      try {
        await this.dependencies.sendTelemetry?.(batch);
        sent = true;
      } catch {
        this.telemetryPending.unshift(...batch);
        if (this.telemetryPending.length > 2_000) {
          this.telemetryPending.splice(0, this.telemetryPending.length - 2_000);
        }
      } finally {
        this.telemetrySending = false;
      }
    })();
    this.telemetrySendPromise = send;
    await send;
    if (this.telemetrySendPromise === send) this.telemetrySendPromise = null;
    if (!this.telemetryPending.length) return;
    if (drain && sent) {
      await this.flushTelemetry(true);
      return;
    }
    this.scheduleTelemetryFlush(TELEMETRY_AFTER_AUDIO_DELAY_MS);
  }

  private scheduleTurnResult(milliseconds: number, boundary = false): void {
    if (boundary) {
      const now = this.dependencies.now();
      if (now - this.lastTurnBoundaryMonotonic <= 250) return;
      this.lastTurnBoundaryMonotonic = now;
    }
    let timer = 0;
    timer = this.dependencies.setTelemetryTimer(() => {
      this.turnResultTimers.delete(timer);
      this.recordUnreportedTurnResults();
      this.scheduleTelemetryFlush(0);
    }, Math.max(0, milliseconds));
    this.turnResultTimers.add(timer);
  }

  private cancelTurnResultTimers(): void {
    for (const timer of this.turnResultTimers) this.dependencies.clearTelemetryTimer(timer);
    this.turnResultTimers.clear();
  }

  private recordUnreportedTurnResults(): void {
    const turns = summarizeJarvisRealtimeTrace(this.trace).turns;
    for (const turn of turns) {
      const key = turn.input_item_id
        ? `item:${turn.input_item_id}`
        : `boundary:${turn.boundary_monotonic_ms}`;
      if (this.reportedTurns.has(key)) continue;
      this.reportedTurns.add(key);
      const timingMs = Object.fromEntries(
        Object.entries({
          endpointing: turn.wake_handoff_tail_ms ?? turn.local_speech_stop_to_provider_stop_ms,
          connection: turn.provider_connection_ms,
          upload: turn.wake_audio_upload_ms,
          transcription: turn.transcription_ms,
          arbitration: turn.response_arbitration_ms,
          generation: turn.generation_ms,
          media_arrival: turn.response_created_to_first_playable_media_ms,
          overall: turn.local_speech_stop_to_first_playable_media_ms,
        }).filter((entry): entry is [string, number] => typeof entry[1] === "number"),
      );
      this.recordTrace("turn.result", {
        response_id: turn.response_id,
        timing_ms: timingMs,
        detail: JSON.stringify({
          turn: turn.turn,
          kind: turn.kind,
          input_item_id: turn.input_item_id ?? null,
          outcome: turn.outcome,
          bottleneck: turn.bottleneck ?? null,
          bottleneck_ms: turn.bottleneck_ms ?? null,
        }),
      });
    }
  }

  private queueReceiverStatsSample(
    connection: BrowserConnection,
    responseId: string,
    reason: string,
    baseline = false,
  ): void {
    connection.receiverStatsChain = connection.receiverStatsChain
      .then(() => this.captureReceiverStats(connection, responseId, reason, baseline))
      .catch(() => undefined);
  }

  private async captureReceiverStats(
    connection: BrowserConnection,
    responseId: string,
    reason: string,
    baseline: boolean,
  ): Promise<void> {
    if (typeof connection.peer.getReceivers !== "function") return;
    const receiver = connection.peer.getReceivers().find((item) => item.track?.kind === "audio");
    if (!receiver || typeof receiver.getStats !== "function") return;
    try {
      const reports = await receiver.getStats();
      const snapshots: ReceiverAudioSnapshot[] = [];
      reports.forEach((report) => {
        if (report.type !== "inbound-rtp" || report.kind !== "audio") return;
        const values = report as RTCInboundRtpStreamStats & {
          totalSamplesReceived?: number;
        };
        snapshots.push({
          responseId,
          bytesReceived: values.bytesReceived ?? 0,
          ...(typeof values.packetsReceived === "number"
            ? { packetsReceived: values.packetsReceived }
            : {}),
          ...(typeof values.totalSamplesReceived === "number"
            ? { totalSamplesReceived: values.totalSamplesReceived }
            : {}),
          ...(typeof values.timestamp === "number" ? { timestamp: values.timestamp } : {}),
        });
      });
      const snapshot = snapshots[0];
      if (!snapshot) return;
      const previous = connection.receiverStatsBaseline;
      this.recordTrace("webrtc.receiver_sample", {
        response_id: responseId,
        detail: JSON.stringify({
          reason,
          bytes_received: snapshot.bytesReceived,
          packets_received: snapshot.packetsReceived ?? null,
          total_samples_received: snapshot.totalSamplesReceived ?? null,
          playing: this.playbackPlaying,
          muted: this.playbackMuted,
        }),
      });
      if (
        !baseline
        && previous?.responseId === responseId
        && snapshot.bytesReceived > previous.bytesReceived
        && this.playbackPlaying
        && !this.playbackMuted
        && !this.trace.some((entry) => (
          entry.kind === "first_playable_media" && entry.response_id === responseId
        ))
      ) {
        this.recordTrace("first_playable_media", {
          response_id: responseId,
          detail: JSON.stringify({
            proxy: "inbound_audio_bytes_increased_while_playing",
            sample: reason,
          }),
        });
      }
      connection.receiverStatsBaseline = snapshot;
    } catch (cause) {
      this.recordTrace("webrtc_stats_unavailable", { detail: this.errorName(cause) });
    }
  }

  private enterConnectionStage(
    tracker: ConnectionAttemptTracker,
    stage: ConnectionAttemptStage,
  ): void {
    this.completeConnectionStage(tracker);
    tracker.stage = stage;
    tracker.stageStartedMonotonic = this.dependencies.now();
  }

  private completeConnectionStage(tracker: ConnectionAttemptTracker): void {
    const now = this.dependencies.now();
    const elapsed = Math.max(0, now - tracker.stageStartedMonotonic);
    tracker.timingMs[tracker.stage] = (tracker.timingMs[tracker.stage] ?? 0) + elapsed;
    tracker.stageStartedMonotonic = now;
  }

  private connectionTimingSnapshot(tracker: ConnectionAttemptTracker): Record<string, number> {
    const result: Record<string, number> = { ...tracker.timingMs };
    result[tracker.stage] = (result[tracker.stage] ?? 0)
      + Math.max(0, this.dependencies.now() - tracker.stageStartedMonotonic);
    result.total = Math.max(0, this.dependencies.now() - tracker.startedMonotonic);
    return Object.fromEntries(
      Object.entries(result).map(([key, value]) => [key, Math.round(value * 10) / 10]),
    );
  }

  private connectionTransportSnapshot(
    peer: RTCPeerConnection | null,
    channel: RTCDataChannel | null,
    localStream: MediaStream | null,
    tracker: ConnectionAttemptTracker,
  ): ConnectionTransportSnapshot {
    const microphone = localStream?.getAudioTracks()[0];
    return {
      peer: peer?.connectionState ?? null,
      ice: peer?.iceConnectionState ?? null,
      ice_gathering: peer?.iceGatheringState ?? null,
      signaling: peer?.signalingState ?? null,
      sctp: peer?.sctp?.state ?? null,
      data_channel: channel?.readyState ?? null,
      microphone_enabled: microphone?.enabled ?? null,
      microphone_muted: microphone?.muted ?? null,
      microphone_ready_state: microphone?.readyState ?? null,
      wake_audio_retained: tracker.wakeAudioRetained,
    };
  }

  private async recordConnectionAttemptSucceeded(
    tracker: ConnectionAttemptTracker,
    peer: RTCPeerConnection,
    timingMs: Record<string, number>,
    transport: ConnectionTransportSnapshot,
  ): Promise<void> {
    const network = await this.collectConnectionNetworkSnapshot(peer);
    this.recordTrace("connection.attempt.succeeded", {
      attempt: tracker.attempt,
      timing_ms: timingMs,
      detail: JSON.stringify({
        result: "succeeded",
        session_id: tracker.session?.id ?? null,
        generation: tracker.session?.generation ?? null,
        transport,
        network,
        clock: tracker.clockAlignment === null ? null : {
          offset_ms: Math.round(tracker.clockAlignment.offsetMs * 10) / 10,
          uncertainty_ms: Math.round(tracker.clockAlignment.uncertaintyMs * 10) / 10,
          network_rtt_ms: Math.round(tracker.clockAlignment.networkRttMs * 10) / 10,
        },
      }),
    });
  }

  private async collectConnectionNetworkSnapshot(
    peer: RTCPeerConnection,
  ): Promise<ConnectionNetworkSnapshot> {
    if (typeof peer.getStats !== "function") return {};
    try {
      const reports = await peer.getStats();
      let selectedPair: RTCStats | undefined;
      reports.forEach((report) => {
        if (
          report.type === "candidate-pair"
          && ((report as RTCIceCandidatePairStats & { selected?: boolean }).selected || report.nominated)
        ) selectedPair = report;
        if (report.type === "transport" && report.selectedCandidatePairId) {
          selectedPair = reports.get(report.selectedCandidatePairId) ?? selectedPair;
        }
      });
      if (!selectedPair) return {};
      const pair = selectedPair as RTCIceCandidatePairStats;
      const local = pair.localCandidateId ? reports.get(pair.localCandidateId) : undefined;
      const remote = pair.remoteCandidateId ? reports.get(pair.remoteCandidateId) : undefined;
      const finite = (value: unknown): number | undefined => (
        typeof value === "number" && Number.isFinite(value) ? value : undefined
      );
      const rttSeconds = finite(pair.currentRoundTripTime);
      const bytesSent = finite(pair.bytesSent);
      const bytesReceived = finite(pair.bytesReceived);
      return {
        ...(typeof local?.candidateType === "string"
          ? { local_candidate_type: local.candidateType }
          : {}),
        ...(typeof remote?.candidateType === "string"
          ? { remote_candidate_type: remote.candidateType }
          : {}),
        ...(rttSeconds === undefined ? {} : { rtt_ms: Math.round(rttSeconds * 1_000 * 10) / 10 }),
        ...(bytesSent === undefined ? {} : { bytes_sent: bytesSent }),
        ...(bytesReceived === undefined ? {} : { bytes_received: bytesReceived }),
      };
    } catch {
      return {};
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

  private connectionErrorCode(stage: ConnectionAttemptStage, cause: unknown): string {
    const message = cause instanceof Error ? cause.message.toLowerCase() : "";
    if (message.includes("cancelled")) return `${stage}_cancelled`;
    if (message.includes("in time") || message.includes("timeout")) return `${stage}_timeout`;
    if (
      message.includes("unavailable")
      || message.includes("failed")
      || message.includes("closed")
      || message.includes("incomplete")
    ) return `${stage}_unavailable`;
    const name = this.errorName(cause)
      .replace(/error$/i, "")
      .replace(/[^a-z0-9]+/gi, "_")
      .replace(/^_|_$/g, "")
      .toLowerCase();
    return `${stage}_${name || "error"}`;
  }
}

export function estimateRealtimeClockAlignment(
  clientSentAtMs: number,
  serverReceivedAtMs: number,
  serverRespondedAtMs: number,
  clientReceivedAtMs: number,
): RealtimeClockAlignment | null {
  if (![clientSentAtMs, serverReceivedAtMs, serverRespondedAtMs, clientReceivedAtMs].every(Number.isFinite)) {
    return null;
  }
  if (clientReceivedAtMs < clientSentAtMs || serverRespondedAtMs < serverReceivedAtMs) return null;
  const networkRttMs = Math.max(
    0,
    (clientReceivedAtMs - clientSentAtMs) - (serverRespondedAtMs - serverReceivedAtMs),
  );
  return {
    offsetMs: (
      (serverReceivedAtMs - clientSentAtMs)
      + (serverRespondedAtMs - clientReceivedAtMs)
    ) / 2,
    uncertaintyMs: networkRttMs / 2,
    networkRttMs,
  };
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
    const controlReady = firstTrace(entries, ["realtime_backend_control_ready"], startIndex, endIndex);
    const dataChannelReady = firstTrace(entries, ["realtime_data_channel_open"], startIndex, endIndex);
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
      backend_control_ready_ms: elapsedMilliseconds(backendStart, controlReady),
      data_channel_ready_ms: elapsedMilliseconds(remoteDescription, dataChannelReady),
      wake_detect_to_backend_call_ms: elapsedMilliseconds(wakeDetected, backendStart),
      wake_detect_to_data_channel_ms: elapsedMilliseconds(wakeDetected, dataChannelReady),
    };
  });

  const stopIndexes: number[] = [];
  entries.forEach((entry, index) => {
    if (entry.kind === "input_audio_buffer.speech_stopped") {
      stopIndexes.push(index);
      return;
    }
    if (entry.kind !== "input_audio_buffer.committed") return;
    const previous = stopIndexes.length ? entries[stopIndexes[stopIndexes.length - 1]] : undefined;
    if (
      previous?.kind === "input_audio_buffer.speech_stopped"
      && entry.monotonic_ms - previous.monotonic_ms <= 250
    ) return;
    stopIndexes.push(index);
  });
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
    const uploadStarted = firstTrace(entries, ["wake_audio_upload_started"], segmentStart, stopIndex + 1);
    const uploadCompleted = firstTrace(entries, ["wake_audio_upload_completed"], segmentStart, stopIndex + 1);
    const commitSent = firstTrace(entries, ["wake_audio_commit_sent"], segmentStart, stopIndex + 1);
    const commitAcknowledged = firstTrace(
      entries,
      ["wake_audio_commit_acknowledged", "input_audio_buffer.committed"],
      commitSent ? entries.indexOf(commitSent) + 1 : segmentStart,
      segmentEnd,
    );
    const controlStart = lastTrace(entries, ["realtime_backend_call_started"], segmentStart, stopIndex + 1);
    const controlReady = firstTrace(entries, ["realtime_backend_control_ready"], segmentStart, stopIndex + 1);
    const dataChannelReady = firstTrace(entries, ["realtime_data_channel_open"], segmentStart, stopIndex + 1);
    const readinessBarrier = [controlReady, dataChannelReady]
      .filter((entry): entry is JarvisRealtimeTraceEntry => entry !== undefined)
      .sort((left, right) => right.monotonic_ms - left.monotonic_ms)[0];
    const backendTranscript = firstTrace(
      entries,
      ["backend.provider.user_transcript"],
      stopIndex + 1,
      segmentEnd,
    );
    const transcript = backendTranscript ?? firstTrace(
      entries,
      ["conversation.item.input_audio_transcription.completed"],
      stopIndex + 1,
      segmentEnd,
    );
    const responseCreated = firstTrace(entries, ["response.created"], stopIndex + 1, segmentEnd);
    const responseId = responseCreated?.response_id;
    const firstAudio = firstTrace(
      entries,
      ["response.output_audio.delta"],
      responseCreated ? entries.indexOf(responseCreated) + 1 : stopIndex + 1,
      segmentEnd,
    );
    const firstPlayableMedia = firstTrace(
      entries,
      ["first_playable_media"],
      responseCreated ? entries.indexOf(responseCreated) + 1 : stopIndex + 1,
      segmentEnd,
    );
    const arbiter = firstTrace(
      entries,
      ["backend.response.arbiter.started"],
      stopIndex + 1,
      segmentEnd,
    );
    const providerGenerating = firstTrace(
      entries,
      ["backend.provider.assistant_generating"],
      stopIndex + 1,
      segmentEnd,
    );
    const providerSpeaking = firstTrace(
      entries,
      ["backend.provider.assistant_speaking"],
      stopIndex + 1,
      segmentEnd,
    );
    const endpointMs = elapsedMilliseconds(localStop, utteranceReady);
    const providerConnectionMs = elapsedMilliseconds(controlStart, readinessBarrier);
    const audioHandoffMs = elapsedMilliseconds(uploadStarted, commitAcknowledged ?? providerStop);
    const transcriptionMs = elapsedMilliseconds(providerStop, transcript);
    const responseArbitrationMs = elapsedMilliseconds(transcript, arbiter);
    const generationMs = elapsedMilliseconds(arbiter ?? responseCreated, firstAudio);
    const mediaArrivalMs = elapsedMilliseconds(firstAudio ?? responseCreated, firstPlayableMedia);
    const bottleneck = [
      { label: "endpointing" as const, milliseconds: endpointMs },
      { label: "provider_connection" as const, milliseconds: providerConnectionMs },
      { label: "audio_handoff" as const, milliseconds: audioHandoffMs },
      { label: "transcription" as const, milliseconds: transcriptionMs },
      { label: "response_arbitration" as const, milliseconds: responseArbitrationMs },
      { label: "generation" as const, milliseconds: generationMs },
      { label: "media_arrival" as const, milliseconds: mediaArrivalMs },
    ].filter((stage): stage is { label: JarvisRealtimeTurnTiming["bottleneck"] & string; milliseconds: number } => (
      stage.milliseconds !== undefined
    )).sort((left, right) => right.milliseconds - left.milliseconds)[0];
    return {
      turn: turnIndex + 1,
      kind: wakeDetected ? "wake" : "follow_up",
      input_item_id: providerStop.item_id,
      boundary_monotonic_ms: providerStop.monotonic_ms,
      response_id: responseId,
      local_speech_stop_to_provider_stop_ms: elapsedMilliseconds(localStop, providerStop),
      wake_handoff_tail_ms: elapsedMilliseconds(localStop, utteranceReady),
      provider_stop_to_response_created_ms: elapsedMilliseconds(providerStop, responseCreated),
      response_created_to_first_audio_ms: elapsedMilliseconds(responseCreated, firstAudio),
      provider_stop_to_first_playable_media_ms: elapsedMilliseconds(providerStop, firstPlayableMedia),
      local_speech_stop_to_first_playable_media_ms: elapsedMilliseconds(localStop, firstPlayableMedia),
      backend_arbiter_ms: arbiter?.timing_ms?.total_to_provider_submit,
      provider_boundary_to_transcript_ms: backendTranscript?.timing_ms?.provider_boundary_to_transcript,
      transcript_to_arbiter_ms: elapsedMilliseconds(transcript, arbiter),
      arbiter_to_response_created_ms: elapsedMilliseconds(arbiter, responseCreated),
      response_create_sent_to_created_ms: providerGenerating?.timing_ms?.response_create_sent_to_created,
      response_created_to_first_audio_provider_ms: providerSpeaking?.timing_ms?.response_created_to_first_audio,
      first_audio_to_first_playable_media_ms: elapsedMilliseconds(firstAudio, firstPlayableMedia),
      response_created_to_first_playable_media_ms: elapsedMilliseconds(responseCreated, firstPlayableMedia),
      wake_audio_upload_ms: elapsedMilliseconds(uploadStarted, uploadCompleted),
      wake_commit_ack_ms: elapsedMilliseconds(commitSent, commitAcknowledged),
      local_speech_stop_to_commit_ms: elapsedMilliseconds(localStop, commitSent),
      provider_connection_ms: providerConnectionMs,
      transcription_ms: transcriptionMs,
      response_arbitration_ms: responseArbitrationMs,
      generation_ms: generationMs,
      outcome: !transcript
        ? "no_transcript"
        : !arbiter
          ? "no_arbiter"
          : !responseCreated
            ? "no_response"
            : !firstAudio && !firstPlayableMedia
              ? "no_audio"
              : !firstPlayableMedia
                ? "no_playable_media"
                : "complete",
      bottleneck: bottleneck?.label,
      bottleneck_ms: bottleneck?.milliseconds,
    } satisfies JarvisRealtimeTurnTiming;
  });

  return {
    schema: "jarvis.realtime_timing.v2",
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
