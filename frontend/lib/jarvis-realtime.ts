import {
  closeJarvisRealtimeSession,
  createJarvisRealtimeCall,
  bargeInJarvisRealtime,
  registerJarvisRealtimeActivation,
  submitJarvisRealtimeTelemetry,
  type JarvisLiveEvent,
  type JarvisRealtimeTelemetryEvent,
  type JarvisSession,
  type JarvisRealtimeActivationResponse,
  type JarvisRealtimeAudioPolicy,
} from "./api";
import {
  createJarvisActivationAudio,
  type JarvisAudioChunk,
  type JarvisCandidateAudio,
} from "./jarvis-activation-audio";
import {
  summarizeJarvisRealtimeTurns,
  type JarvisRealtimeTurnResult,
  type JarvisRealtimeReducerEntry,
} from "./jarvis-realtime-turns";
import {
  createJarvisBargeInDetector,
  type JarvisBargeInDetector,
} from "./jarvis-vad";

export type JarvisRealtimeStatus =
  | "idle"
  | "connecting"
  | "standby"
  | "candidate"
  | "active"
  | "connected"
  | "reconnecting"
  | "rotating"
  | "degraded"
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
  onActivationPolicy?: (observation: {
    activationId: string;
    requestedGateMode: "transcript_rules" | "immediate";
    backendGateMode: "transcript_rules" | "immediate";
    configurationId: string;
  }) => void;
}

export interface JarvisRealtimeAudioHandoff {
  localStream: MediaStream;
  utterance: Promise<Float32Array>;
  sampleRate: number;
  activationId?: string;
  candidateAudio?: JarvisRealtimeCandidateAudio | JarvisCandidateAudio;
}

export interface JarvisRealtimeStartOptions {
  audioHandoff?: JarvisRealtimeAudioHandoff;
  activationContext?: JarvisRealtimeActivationContext;
  standby?: boolean;
}

export interface JarvisRealtimeActivationContext {
  source: "wake_word";
  wake_score: number;
  activation_id: string;
  wake_detected_at?: string;
  local_speech_started_at?: string;
  gate_mode?: "transcript_rules" | "immediate";
  benchmark_run_id?: string | null;
  benchmark_fixture?: string | null;
}

export interface JarvisRealtimeCandidateAudio {
  sampleRate: number;
  preRoll: Float32Array;
  endpoint: Promise<{ sampleRate: number; totalSamples: number; trailingSilenceMs: number }>;
  subscribe: (listener: (samples: Float32Array) => void) => () => void;
}

export interface JarvisRealtimeCandidateOptions {
  candidateAudio: JarvisRealtimeCandidateAudio;
  activationContext: JarvisRealtimeActivationContext;
}

export interface JarvisRealtimeTraceEntry {
  kind: string;
  monotonic_ms: number;
  wall_time: string;
  activation_id?: string;
  session_id?: string;
  generation?: number;
  request_id?: string;
  response_id?: string;
  item_id?: string;
  provider_input_item_id?: string;
  event_id?: string;
  client_sequence?: number;
  clock_uncertainty_ms?: number;
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
  browser_speech_end_to_first_playable_ms?: number;
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
  gate_started_to_decision_ms?: number;
  local_speech_stop_to_gate_decision_ms?: number;
  gate_decision?: "accepted" | "rejected" | "timed_out";
  gate_decision_source?: string;
  standby_ready?: boolean;
  standby_age_ms?: number;
  provider_connection_ms?: number;
  transcription_ms?: number;
  response_arbitration_ms?: number;
  generation_ms?: number;
  outcome?:
    | "complete"
    | "no_transcript"
    | "no_arbiter"
    | "no_response"
    | "no_audio"
    | "no_playable_media"
    | "gate_rejected"
    | "gate_timed_out";
  bottleneck?: "endpointing" | "provider_connection" | "audio_handoff" | "transcription" | "response_arbitration" | "generation" | "media_arrival";
  bottleneck_ms?: number;
}

export interface JarvisRealtimeTimingSummary {
  schema: "jarvis.realtime_timing.v2";
  connections: JarvisRealtimeConnectionTiming[];
  turns: JarvisRealtimeTurnTiming[];
  turn_results?: JarvisRealtimeTurnResult[];
}

interface BrowserConnection {
  peer: RTCPeerConnection;
  channel: RTCDataChannel;
  localStream: MediaStream;
  ownsLocalStream: boolean;
  handoffTransport: JarvisRealtimeHandoffTransport | null;
  silentTransport: JarvisRealtimeSilentTransport | null;
  session: JarvisSession;
  audioPolicy: JarvisRealtimeAudioPolicy | null;
  rotateAfterSeconds: number;
  bargeInDetector: JarvisBargeInDetector | null;
  activeResponseId: string | null;
  responseCorrelation: BrowserResponseCorrelation | null;
  cancellingResponseId: string | null;
  bargeInTimer: number | null;
  mutedResponseId: string | null;
  tracedAudioDeltaResponses: Set<string>;
  wakeCommitWaiter: WakeCommitWaiter | null;
  receiverStatsBaseline: ReceiverAudioSnapshot | null;
  receiverStatsChain: Promise<void>;
  receiverStatsTimer: number | null;
  mode: "standby" | "active";
  candidate: CandidateActivation | null;
  conversationAudio: JarvisCandidateAudio | null;
  conversationAudioUnsubscribe: (() => void) | null;
  conversationChunkQueue: JarvisAudioChunk[];
  conversationAppendSequence: number;
  sender: RTCRtpSender;
}

interface BrowserResponseCorrelation {
  requestId: string;
  responseId: string;
  activationId: string | null;
  inputItemId: string | null;
  state: "generating" | "speaking" | "cancelling";
}

interface JarvisRealtimeHandoffTransport {
  stream: MediaStream;
  connectLive: () => Promise<void>;
  destroy: () => Promise<void>;
}

interface JarvisRealtimeSilentTransport {
  stream: MediaStream;
  destroy: () => Promise<void>;
}

interface CandidateActivation {
  activationId: string;
  unsubscribe: (() => void) | null;
  appendSequence: number;
  commitSent: boolean;
  gate: JarvisRealtimeActivationResponse | null;
  gateDecision: Promise<"accepted" | "rejected" | "timed_out">;
  resolveGateDecision: (decision: "accepted" | "rejected" | "timed_out") => void;
  rejectGateDecision: (cause: unknown) => void;
  standbySettlement: Promise<boolean>;
  resolveStandbySettlement: (standby: boolean) => void;
  gateSettled: boolean;
  audioPolicy: JarvisRealtimeAudioPolicy | null;
  orderedAudio: JarvisCandidateAudio | null;
  chunkQueue: JarvisAudioChunk[];
  committed: boolean;
  primaryInputItemId: string | null;
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

interface LocalSpeechEndpoint {
  monotonicMs: number;
  source: "wake_local_speech_stopped" | "local_speech_stopped";
  activationId: string | null;
}

interface JarvisRealtimeConnectionInput {
  localStream: MediaStream;
  ownsLocalStream: boolean;
  wakeAudio?: Promise<JarvisRealtimeWakeAudioEnvelope>;
  candidateAudio?: JarvisRealtimeCandidateAudio | JarvisCandidateAudio;
  conversationAudio?: JarvisCandidateAudio;
  activationContext?: JarvisRealtimeActivationContext;
  activationCohort?: "warm" | "connecting" | "cold";
  standby?: boolean;
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
  silentTransport: JarvisRealtimeSilentTransport | null;
}

export interface JarvisRealtimeDependencies {
  createPeer: () => RTCPeerConnection;
  getUserMedia: (constraints: MediaStreamConstraints) => Promise<MediaStream>;
  createCall: typeof createJarvisRealtimeCall;
  registerActivation?: typeof registerJarvisRealtimeActivation;
  closeSession: typeof closeJarvisRealtimeSession;
  sendTelemetry?: typeof submitJarvisRealtimeTelemetry;
  bargeIn?: typeof bargeInJarvisRealtime;
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
  createSilentTransport?: () => Promise<JarvisRealtimeSilentTransport>;
}

const RETRY_DELAYS_MS = [0, 500, 1_000, 2_000] as const;
const DISCONNECT_GRACE_MS = 2_000;
const ICE_CANDIDATE_WAIT_MS = 750;
const DATA_CHANNEL_OPEN_TIMEOUT_MS = 10_000;
const WAKE_COMMIT_TIMEOUT_MS = 3_000;
const TELEMETRY_AFTER_AUDIO_DELAY_MS = 1_000;
// The backend response watchdog closes after 15 seconds (plus scheduling
// margin). Do not synthesize a failed turn while a valid response may still
// be inside that server-side budget.
const TELEMETRY_MISSING_RESPONSE_DELAY_MS = 20_000;
const RECEIVER_STATS_FOLLOW_UP_MS = 100;
const TURN_RESULT_SETTLE_MS = 150;
const LIVE_STREAM_ERROR_COALESCE_MS = 30_000;
const MAX_BROWSER_TRACE_EVENTS = 500;
const MAX_PENDING_TELEMETRY_EVENTS = 500;
const TELEMETRY_FAILURE_INTEGRITY_THRESHOLD = 3;
const WAKE_PCM_SAMPLE_RATE = 24_000 as const;
const WAKE_PCM_CHUNK_BYTES = 24_000;
const BACKEND_TRACE_EVENTS = new Set([
  "provider.ready",
  "provider.user_speaking",
  "provider.user_stopped",
  "provider.user_input_committed",
  "provider.user_transcript",
  "wake.gate.started",
  "wake.gate.accepted",
  "wake.gate.rejected",
  "wake.gate.timed_out",
  "wake.verification.accepted",
  "wake.verification.rejected",
  "response.arbiter.started",
  "provider.assistant_generating",
  "provider.assistant_speaking",
  "provider.assistant_response",
  "provider.assistant_interrupted",
  "provider.provider_error",
  "provider.provider_protocol_warning",
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
const PERSISTED_BROWSER_TELEMETRY_EVENTS = new Set([
  "live_stream.opened",
  "live_stream.errored",
  "live_stream.reconnected",
  "backend.readiness_event_received",
  "realtime_backend_control_wait_started",
  "realtime_backend_control_wait_failed",
  "realtime_backend_control_ready",
  "connection.attempt.succeeded",
  "connection.attempt.failed",
  "connection.attempt.cleanup",
  "turn.result",
  "turn.result.amendment",
  "telemetry.integrity",
]);

const browserDependencies: JarvisRealtimeDependencies = {
  createPeer: () => new RTCPeerConnection(),
  getUserMedia: (constraints) => navigator.mediaDevices.getUserMedia(constraints),
  createCall: createJarvisRealtimeCall,
  registerActivation: registerJarvisRealtimeActivation,
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
  bargeIn: bargeInJarvisRealtime,
  createHandoffTransport: createBrowserHandoffTransport,
  createSilentTransport: createBrowserSilentTransport,
};

export class JarvisRealtimeController {
  private readonly options: JarvisRealtimeControllerOptions;
  private readonly dependencies: JarvisRealtimeDependencies;
  private active: BrowserConnection | null = null;
  private connectingSession: JarvisSession | null = null;
  private preparation: BrowserPreparation | null = null;
  private preparationOperation = 0;
  private standbyConnectionPromise: Promise<JarvisRealtimeSnapshot> | null = null;
  private rotationTimer: number | null = null;
  private rotationPending = false;
  private disconnectTimer: number | null = null;
  private operation = 0;
  private reconnecting = false;
  private readonly trace: JarvisRealtimeTraceEntry[] = [];
  private readonly traceId = crypto.randomUUID();
  private activationId: string | null = null;
  private telemetryPending: JarvisRealtimeTelemetryEvent[] = [];
  private telemetrySending = false;
  private telemetryClientSequence = 0;
  private telemetryDropped = 0;
  private telemetryConsecutiveFailures = 0;
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
  private localSpeechActive = false;
  private latestLocalSpeechEndpoint: LocalSpeechEndpoint | null = null;
  private readonly responseSpeechEndpoints = new Map<string, LocalSpeechEndpoint>();
  private readonly reportedTurns = new Set<string>();
  private readonly reportedV3Turns = new Map<string, string>();
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

  /**
   * Waits until a rejected wake candidate has either restored warm standby or
   * the connection has become unavailable. Page lifecycle code uses this to
   * avoid racing the asynchronous sender/transport rollback.
   */
  async waitForWakeGateStandby(): Promise<boolean> {
    const candidate = this.active?.candidate;
    if (!candidate) return this.snapshot.status === "standby";
    await candidate.standbySettlement;
    return this.snapshot.status === "standby";
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
    const entry = this.recordTrace(kind, {
      detail: detail ? JSON.stringify(detail) : undefined,
    });
    if (kind === "wake_local_speech_started" || kind === "local_speech_started") {
      this.localSpeechActive = true;
    } else if (kind === "wake_local_speech_stopped" || kind === "local_speech_stopped") {
      this.localSpeechActive = false;
      this.rememberLocalSpeechEndpoint(entry, kind);
    }
  }

  handlePageHide(): void {
    if (!this.telemetryPending.length && !this.telemetrySending) return;
    this.recordTelemetryIntegrity("page_shutdown_pending", {
      pending_count: this.telemetryPending.length,
      in_flight: this.telemetrySending,
    });
    void this.flushTelemetry();
  }

  recordBackendEvent(event: JarvisLiveEvent): boolean {
    if (event.source_domain !== "session") return false;
    const payload = event.payload ?? {};
    let gateHandled = false;
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
      ) return false;
      if (["wake.gate.accepted", "wake.gate.rejected", "wake.gate.timed_out"].includes(event.event_type)) {
        gateHandled = this.resolveCandidateGateDecision(event.event_type, payload, sessionId, generation);
      }
    }
    if (!BACKEND_TRACE_EVENTS.has(event.event_type)) return gateHandled;
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
    const activationId = typeof payload.activation_id === "string"
      ? payload.activation_id
      : undefined;
    const providerItemId = typeof payload.item_id === "string" ? payload.item_id : undefined;
    const inputItemId = typeof payload.input_item_id === "string"
      ? payload.input_item_id
      : event.event_type.includes("user_input_committed")
        || event.event_type.includes("user_transcript")
        ? providerItemId
        : undefined;
    const itemId = inputItemId ?? providerItemId;
    const requestId = typeof payload.request_id === "string"
      ? payload.request_id
      : undefined;
    const reason = typeof payload.reason === "string" ? payload.reason : undefined;
    const traceDetail = Object.fromEntries(
      Object.entries({
        reason,
        original_code: typeof payload.original_code === "string" ? payload.original_code : undefined,
        configuration_id: typeof payload.configuration_id === "string" ? payload.configuration_id : undefined,
        turn_mode: typeof payload.turn_mode === "string" ? payload.turn_mode : undefined,
        commit_owner: typeof payload.commit_owner === "string" ? payload.commit_owner : undefined,
        benchmark_run_id: typeof payload.benchmark_run_id === "string" ? payload.benchmark_run_id : undefined,
        benchmark_fixture: typeof payload.benchmark_fixture === "string" ? payload.benchmark_fixture : undefined,
        cohort: typeof payload.cohort === "string" ? payload.cohort : undefined,
        status: typeof payload.status === "string" ? payload.status : undefined,
        response_status: typeof payload.response_status === "string" ? payload.response_status : undefined,
      }).filter((entry): entry is [string, string] => typeof entry[1] === "string"),
    );
    this.recordTrace(`backend.${event.event_type}`, {
      event_id: event.id,
      activation_id: activationId,
      session_id: sessionId ?? undefined,
      generation: generation ?? undefined,
      request_id: requestId,
      response_id: responseId,
      item_id: itemId,
      provider_input_item_id: inputItemId,
      sequence: event.sequence,
      server_time: event.created_at,
      timing_ms: timingMs,
      detail: Object.keys(traceDetail).length ? JSON.stringify(traceDetail) : undefined,
    });
    if ([
      "provider.assistant_response",
      "provider.assistant_interrupted",
      "provider.provider_error",
      "provider.disconnected",
    ].includes(event.event_type)) {
      this.scheduleTelemetryFlush(TELEMETRY_AFTER_AUDIO_DELAY_MS);
    }
    return gateHandled;
  }

  private resolveCandidateGateDecision(
    eventType: string,
    payload: Record<string, unknown>,
    sessionId: string,
    generation: number,
  ): boolean {
    const connection = this.active;
    const candidate = connection?.candidate;
    if (!connection || !candidate || candidate.gateSettled) return false;
    const activationId = typeof payload.activation_id === "string"
      ? payload.activation_id
      : null;
    if (
      activationId !== candidate.activationId
      || sessionId !== connection.session.id
      || generation !== connection.session.generation
    ) return false;
    const decision = eventType === "wake.gate.accepted"
      ? "accepted"
      : eventType === "wake.gate.rejected"
        ? "rejected"
        : "timed_out";
    candidate.gateSettled = true;
    if (candidate.gate) candidate.gate = { ...candidate.gate, gate_state: decision };
    candidate.resolveGateDecision(decision);
    this.maybePromoteOrderedCandidate(connection, candidate, decision);
    return true;
  }

  private maybePromoteOrderedCandidate(
    connection: BrowserConnection,
    candidate: CandidateActivation,
    decision?: "accepted" | "rejected" | "timed_out",
  ): void {
    if (!candidate.orderedAudio) return;
    const effectiveDecision = decision
      ?? (candidate.gate?.gate_state === "accepted" ? "accepted" : undefined);
    if (effectiveDecision !== "accepted" || !candidate.committed) return;
    candidate.orderedAudio.promoteToConversation();
    connection.conversationAudio = candidate.orderedAudio;
    this.recordTrace("wake_audio_promoted_to_conversation", {
      item_id: candidate.primaryInputItemId ?? undefined,
    });
    this.publish({
      status: "connected",
      attempt: this.snapshot.attempt,
      session: connection.session,
      error: null,
    });
    this.scheduleRotation(connection.rotateAfterSeconds);
    void this.maybeRotate();
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
    const silentTransport = this.dependencies.createSilentTransport
      ? await this.dependencies.createSilentTransport()
      : null;
    const sourceStream = silentTransport?.stream ?? localStream;
    const track = sourceStream.getAudioTracks()[0];
    if (!track) {
      channel.close();
      peer.close();
      await silentTransport?.destroy().catch(() => undefined);
      throw new Error("Wake stream has no audio track");
    }
    const sender = peer.addTrack(track, sourceStream);
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
      silentTransport,
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

  async arm(localStream: MediaStream): Promise<JarvisRealtimeSnapshot> {
    await this.prepare(localStream);
    return this.start({ standby: true });
  }

  async start(options: JarvisRealtimeStartOptions = {}): Promise<JarvisRealtimeSnapshot> {
    this.localSpeechActive = false;
    this.latestLocalSpeechEndpoint = null;
    this.responseSpeechEndpoints.clear();
    this.activationId = options.activationContext?.activation_id
      ?? options.audioHandoff?.activationId
      ?? (options.audioHandoff ? crypto.randomUUID() : null);
    const audioHandoff = options.audioHandoff;
    const activationContext = options.activationContext
      ? {
          ...options.activationContext,
          ...(this.activationId ? { activation_id: this.activationId } : {}),
          wake_detected_at: options.activationContext.wake_detected_at ?? this.dependencies.wallNow(),
        }
      : undefined;
    const requestedActivationId = options.activationContext?.activation_id;
    const activationCandidateAudio = audioHandoff?.candidateAudio
      ?? (
        requestedActivationId && audioHandoff
          ? adaptLegacyHandoffToActivationAudio(audioHandoff, requestedActivationId)
          : undefined
      );
    this.recordTrace("realtime_start_requested", {
      detail: audioHandoff ? "wake_handoff" : "direct",
    });
    const standbyConnection = !options.standby && activationCandidateAudio
      ? this.standbyConnectionPromise
      : null;
    if (standbyConnection) {
      try {
        await standbyConnection;
      } catch {
        // Reuse the retained candidate for the normal cold retry path below.
      }
      if (
        activationCandidateAudio
        && audioHandoff
        && this.active?.mode === "standby"
        && this.active.localStream === audioHandoff.localStream
      ) {
        this.cancelRotation();
        this.cancelDisconnect();
        await this.activateCandidate(
          this.active,
          { ...audioHandoff, candidateAudio: activationCandidateAudio },
          activationContext,
          "connecting",
        );
        return this.snapshot;
      }
    }
    if (
      activationCandidateAudio
      && audioHandoff
      && this.active?.mode === "standby"
      && this.active.localStream === audioHandoff.localStream
    ) {
      this.cancelRotation();
      this.cancelDisconnect();
      await this.activateCandidate(
        this.active,
        { ...audioHandoff, candidateAudio: activationCandidateAudio },
        activationContext,
        "warm",
      );
      return this.snapshot;
    }
    const operation = ++this.operation;
    this.cancelRotation();
    this.cancelDisconnect();
    const input = audioHandoff
      ? {
          localStream: audioHandoff.localStream,
          ownsLocalStream: false,
          ...(activationCandidateAudio
            ? { candidateAudio: activationCandidateAudio }
            : { wakeAudio: this.prepareWakeAudio(audioHandoff) }),
          activationContext,
          activationCohort: "cold" as const,
          standby: false,
        }
      : options.standby
        ? {
            localStream: this.preparation?.localStream
              ?? (() => { throw new Error("Standby requires a prepared local stream"); })(),
            ownsLocalStream: false,
            standby: true,
          }
        : undefined;
    const connectionPromise = this.connectWithRetry(operation, false, input);
    if (options.standby) {
      this.standbyConnectionPromise = connectionPromise;
      try {
        return await connectionPromise;
      } finally {
        if (this.standbyConnectionPromise === connectionPromise) {
          this.standbyConnectionPromise = null;
        }
      }
    }
    return connectionPromise;
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
    this.localSpeechActive = false;
    this.latestLocalSpeechEndpoint = null;
    this.responseSpeechEndpoints.clear();
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
      const conversationAudio = current?.conversationAudio;
      if (conversationAudio) {
        // Leave the owner queued, but release the old connection's sole
        // subscription before a replacement attempt attaches to it.
        current.candidate?.unsubscribe?.();
        if (current.candidate) current.candidate.unsubscribe = null;
        current.conversationAudioUnsubscribe?.();
        current.conversationAudioUnsubscribe = null;
      }
      const input = current
        ? {
            localStream: current.localStream,
            ownsLocalStream: false,
            ...(conversationAudio ? { conversationAudio } : {}),
            standby: current.mode === "standby",
          }
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
          await this.disposeConnection(previous, false, input?.conversationAudio);
        }
        this.publish({
          status: connection.mode === "standby" ? "standby" : "connected",
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
    void this.flushTelemetry();
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
      wakeAudioRetained: Boolean(input?.wakeAudio || input?.candidateAudio),
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
    let silentTransport: JarvisRealtimeSilentTransport | null = null;
    let audioSender: RTCRtpSender | null = null;
    const hasCandidateAudio = Boolean(input?.candidateAudio);
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
      if (prepared) {
        audioSender = prepared.sender;
        silentTransport = prepared.silentTransport;
      }
      if (input?.standby || hasCandidateAudio) {
        silentTransport = prepared?.silentTransport ?? null;
        if (!prepared) {
          silentTransport = this.dependencies.createSilentTransport
            ? await this.dependencies.createSilentTransport()
            : null;
          const outgoingStream = silentTransport?.stream ?? localStream;
          for (const track of outgoingStream.getAudioTracks()) {
            audioSender = peer.addTrack(track, outgoingStream);
          }
        }
        this.recordTrace(
          input?.standby
            ? "realtime_standby_transport_ready"
            : "realtime_wake_pcm_transport_ready",
          { attempt },
        );
      } else {
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
          await silentTransport?.destroy().catch(() => undefined);
          silentTransport = null;
        } else {
          for (const track of outgoingStream.getAudioTracks()) {
            audioSender = peer.addTrack(track, outgoingStream);
          }
        }
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
        silentTransport,
        session: providerSession,
        audioPolicy: response.audio_policy ?? null,
        rotateAfterSeconds: response.rotate_after_seconds ?? 55 * 60,
        bargeInDetector: null,
        activeResponseId: null,
        responseCorrelation: null,
        cancellingResponseId: null,
        bargeInTimer: null,
        mutedResponseId: null,
        tracedAudioDeltaResponses: new Set(),
        wakeCommitWaiter: null,
        receiverStatsBaseline: null,
        receiverStatsChain: Promise.resolve(),
        receiverStatsTimer: null,
        mode: input?.standby ? "standby" : "active",
        candidate: null,
        conversationAudio: null,
        conversationAudioUnsubscribe: null,
        conversationChunkQueue: [],
        conversationAppendSequence: 0,
        sender: audioSender ?? (() => { throw new Error("Realtime stream has no audio sender"); })(),
      };
      // Ordered candidate PCM must resume only after the browser has drained
      // its SCTP send buffer.  The low-watermark callback is deliberately
      // attached to this connection, rather than polling from the capture
      // path, so a quiet microphone cannot strand queued audio.
      channel.bufferedAmountLowThreshold = 64 * 1024;
      channel.onbufferedamountlow = () => {
        const currentCandidate = connection?.candidate;
        if (connection && currentCandidate) {
          this.drainOrderedChunks(connection, currentCandidate);
        }
        if (connection) this.drainConversationChunks(connection);
      };
      this.enterConnectionStage(tracker, "backend_control");
      await this.waitForBackendControl(providerSession, operation, attempt);
      const candidateAudio = input?.candidateAudio;
      if (candidateAudio) {
        this.enterConnectionStage(tracker, "data_channel");
        await this.waitForDataChannelOpen(channel, operation, attempt);
        if (isOrderedCandidateAudio(candidateAudio)) {
          await this.streamOrderedCandidate(
            connection,
            candidateAudio,
            input.activationContext,
            input.activationCohort ?? "cold",
          );
        } else {
          await this.activateCandidate(
            connection,
            {
              localStream,
              utterance: Promise.resolve(new Float32Array(0)),
              sampleRate: candidateAudio.sampleRate,
              activationId: input.activationContext?.activation_id,
              candidateAudio,
            },
            input.activationContext,
            input.activationCohort ?? "cold",
          );
        }
        tracker.wakeAudioRetained = false;
      } else if (input?.conversationAudio) {
        this.enterConnectionStage(tracker, "data_channel");
        await this.waitForDataChannelOpen(channel, operation, attempt);
        this.attachConversationAudio(connection, input.conversationAudio);
        tracker.wakeAudioRetained = false;
      } else if (input?.wakeAudio) {
        this.enterConnectionStage(tracker, "data_channel");
        await this.waitForDataChannelOpen(channel, operation, attempt);
        const wakeAudio = await input.wakeAudio;
        if (!handoffTransport) throw new Error("Wake handoff transport is unavailable");
        await this.submitWakeAudio(connection, handoffTransport, wakeAudio, attempt, tracker);
        tracker.wakeAudioRetained = false;
      } else if (input?.standby) {
        this.enterConnectionStage(tracker, "data_channel");
        await this.waitForDataChannelOpen(channel, operation, attempt);
      } else {
        this.enterConnectionStage(tracker, "microphone_activation");
        if (!handoffTransport) throw new Error("Realtime handoff transport is unavailable");
        await handoffTransport.connectLive();
        this.recordTrace("realtime_live_mic_activated", { attempt });
      }
      if (input?.standby) {
        this.recordTrace("realtime_standby_ready", {
          attempt,
          detail: `${providerSession.id}:${providerSession.generation}`,
        });
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
        detail: JSON.stringify({
          session_id: providerSession.id,
          generation: providerSession.generation,
          configuration_id: response.audio_policy?.configuration_id ?? null,
          turn_mode: response.audio_policy?.turn_mode ?? null,
          commit_owner: response.audio_policy?.commit_owner ?? null,
        }),
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
      connection?.candidate?.unsubscribe?.();
      if (connection?.candidate) connection.candidate.unsubscribe = null;
      connection?.conversationAudioUnsubscribe?.();
      if (connection) connection.conversationAudioUnsubscribe = null;
      if (connection) {
        for (const chunk of connection.conversationChunkQueue) chunk.samples.fill(0);
        connection.conversationChunkQueue.length = 0;
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
      () => {
        this.rotationTimer = null;
        this.rotationPending = true;
        void this.maybeRotate();
      },
      Math.max(1, seconds) * 1_000,
    );
  }

  private async maybeRotate(): Promise<void> {
    if (!this.rotationPending || !this.active) return;
    const connection = this.active;
    const candidate = connection.candidate;
    const candidatePending = Boolean(
      candidate
      && !(
        candidate.gateSettled
        && candidate.gate?.gate_state === "accepted"
        && (candidate.orderedAudio ? candidate.committed : candidate.commitSent)
        && candidate.chunkQueue.length === 0
      ),
    );
    if (
      candidatePending
      || connection.activeResponseId
      || connection.responseCorrelation
      || this.localSpeechActive
    ) {
      this.recordTrace("realtime_rotation_deferred", {
        detail: JSON.stringify({
          candidate_pending: candidatePending,
          response_active: Boolean(connection.activeResponseId || connection.responseCorrelation),
          local_speech_active: this.localSpeechActive,
        }),
      });
      this.rotationTimer = this.dependencies.setTimer(() => {
        this.rotationTimer = null;
        void this.maybeRotate();
      }, 1_000);
      return;
    }
    this.rotationPending = false;
    this.recordTrace("realtime_rotation_started");
    await this.reconnect();
  }

  private async activateCandidate(
    connection: BrowserConnection,
    handoff: JarvisRealtimeAudioHandoff,
    activationContext: JarvisRealtimeActivationContext | undefined,
    cohort: "warm" | "connecting" | "cold" = "cold",
  ): Promise<void> {
    if (!activationContext?.activation_id) {
      throw new Error("Wake candidate activation requires an activation id");
    }
    if (connection.channel.readyState !== "open") {
      throw new Error("Realtime data channel is not open");
    }
    const candidateAudio = handoff.candidateAudio;
    if (!candidateAudio) throw new Error("Wake candidate audio is unavailable");
    if (isOrderedCandidateAudio(candidateAudio)) {
      await this.activateOrderedCandidate(connection, candidateAudio, activationContext, cohort);
      return;
    }
    if (connection.audioPolicy?.commit_owner === "provider") {
      await this.activateOrderedCandidate(
        connection,
        adaptLegacyCandidateAudio(candidateAudio, activationContext.activation_id),
        activationContext,
        cohort,
      );
      return;
    }
    const activation = await (this.dependencies.registerActivation ?? registerJarvisRealtimeActivation)(
      connection.session.id,
      {
        generation: connection.session.generation,
        wake_score: activationContext.wake_score,
        activation_id: activationContext.activation_id,
        wake_detected_at: activationContext.wake_detected_at,
        local_speech_started_at: activationContext.local_speech_started_at,
        gate_mode: activationContext.gate_mode ?? "transcript_rules",
        benchmark_run_id: activationContext.benchmark_run_id,
        benchmark_fixture: activationContext.benchmark_fixture,
      },
    );
    connection.audioPolicy = activation.audio_policy ?? connection.audioPolicy;
    const requestedGateMode = activationContext.gate_mode ?? "transcript_rules";
    this.recordTrace("wake.gate.policy", {
      activation_id: activation.activation_id,
      detail: JSON.stringify({
        requested_gate_mode: requestedGateMode,
        backend_gate_mode: activation.gate_mode,
        configuration_id: connection.audioPolicy?.configuration_id ?? null,
        mismatch: requestedGateMode !== activation.gate_mode,
      }),
    });
    this.options.onActivationPolicy?.({
      activationId: activation.activation_id,
      requestedGateMode,
      backendGateMode: activation.gate_mode,
      configurationId: connection.audioPolicy?.configuration_id ?? "unknown",
    });
    this.recordTrace("wake.gate.started", {
      detail: JSON.stringify({
        activation_id: activation.activation_id,
        gate_mode: activation.gate_mode,
        standby: true,
        cohort,
      }),
    });
    let resolveGateDecision!: (
      decision: "accepted" | "rejected" | "timed_out",
    ) => void;
    let rejectGateDecision!: (cause: unknown) => void;
    const gateDecision = new Promise<"accepted" | "rejected" | "timed_out">((resolve, reject) => {
      resolveGateDecision = resolve;
      rejectGateDecision = reject;
    });
    let resolveStandbySettlement!: (standby: boolean) => void;
    const standbySettlement = new Promise<boolean>((resolve) => {
      resolveStandbySettlement = resolve;
    });
    const candidate: CandidateActivation = {
      activationId: activation.activation_id,
      unsubscribe: null,
      appendSequence: 0,
      commitSent: false,
      gate: activation,
      gateDecision,
      resolveGateDecision,
      rejectGateDecision,
      standbySettlement,
      resolveStandbySettlement,
      gateSettled: false,
      audioPolicy: null,
      orderedAudio: null,
      chunkQueue: [],
      committed: false,
      primaryInputItemId: null,
    };
    connection.candidate = candidate;
    connection.mode = "active";
    if (activation.gate_state !== "pending") {
      candidate.gateSettled = true;
      candidate.resolveGateDecision(activation.gate_state);
    }
    this.publish({
      status: "candidate",
      attempt: this.snapshot.attempt,
      session: connection.session,
      error: null,
    });

    const transport = await this.dependencies.createHandoffTransport(handoff.localStream);
    connection.handoffTransport = transport;
    const liveTrack = transport.stream.getAudioTracks()[0];
    if (!liveTrack) throw new Error("Realtime stream has no audio track");
    await connection.sender.replaceTrack(liveTrack);

    this.recordTrace("wake_audio_upload_started", { detail: "candidate_stream" });
    const append = (samples: Float32Array) => {
      if (connection.candidate !== candidate || candidate.commitSent) {
        samples.fill(0);
        return;
      }
      const envelope = encodeJarvisWakeAudio(samples, candidateAudio.sampleRate);
      samples.fill(0);
      for (const audio of envelope.chunks) {
        connection.channel.send(JSON.stringify({
          type: "input_audio_buffer.append",
          event_id: `jarvis-wake-stream-${candidate.activationId}-${candidate.appendSequence++}`,
          audio,
        }));
      }
    };
    append(candidateAudio.preRoll);
    candidate.unsubscribe = candidateAudio.subscribe(append);
    try {
      await candidateAudio.endpoint;
      if (connection.candidate !== candidate || candidate.commitSent) return;
      candidate.commitSent = true;
      this.recordTrace("wake_audio_upload_completed", { detail: "candidate_endpoint" });
      const commitEventId = `jarvis-wake-commit-${candidate.activationId}-${this.snapshot.attempt}`;
      const acknowledgement = new Promise<string | undefined>((resolve, reject) => {
        const timer = this.dependencies.setTimer(
          () => reject(new Error("OpenAI did not acknowledge the wake audio in time")),
          WAKE_COMMIT_TIMEOUT_MS,
        );
        connection.wakeCommitWaiter = { eventId: commitEventId, timer, resolve, reject };
      });
      connection.channel.send(JSON.stringify({
        type: "input_audio_buffer.commit",
        event_id: commitEventId,
      }));
      this.recordTrace("wake_audio_commit_sent", { detail: "candidate_endpoint" });
      const gateDecision = await candidate.gateDecision;
      if (connection.candidate !== candidate) return;
      if (gateDecision !== "accepted") {
        await this.restoreStandbyAfterCandidateDecision(connection, candidate, gateDecision);
        return;
      }
      await transport.connectLive();
      this.recordTrace("wake_live_mic_activated", { detail: "candidate_endpoint" });
      await acknowledgement;
      this.recordTrace("wake_audio_commit_acknowledged", { item_id: candidate.activationId });
      this.publish({
        status: "connected",
        attempt: this.snapshot.attempt,
        session: connection.session,
        error: null,
      });
      this.scheduleRotation(connection.rotateAfterSeconds);
    } finally {
      candidate.unsubscribe?.();
      candidate.unsubscribe = null;
      if (connection.wakeCommitWaiter?.eventId.startsWith(`jarvis-wake-commit-${candidate.activationId}`)) {
        this.dependencies.clearTimer(connection.wakeCommitWaiter.timer);
        connection.wakeCommitWaiter = null;
      }
    }
  }

  private async activateOrderedCandidate(
    connection: BrowserConnection,
    candidateAudio: JarvisCandidateAudio,
    activationContext: JarvisRealtimeActivationContext | undefined,
    cohort: "warm" | "connecting" | "cold" = "cold",
  ): Promise<void> {
    if (!activationContext?.activation_id) {
      throw new Error("Wake candidate activation requires an activation id");
    }
    if (connection.channel.readyState !== "open") {
      throw new Error("Realtime data channel is not open");
    }
    const activation = await (this.dependencies.registerActivation ?? registerJarvisRealtimeActivation)(
      connection.session.id,
      {
        generation: connection.session.generation,
        wake_score: activationContext.wake_score,
        activation_id: activationContext.activation_id,
        wake_detected_at: activationContext.wake_detected_at,
        local_speech_started_at: activationContext.local_speech_started_at,
        gate_mode: activationContext.gate_mode ?? "transcript_rules",
        benchmark_run_id: activationContext.benchmark_run_id,
        benchmark_fixture: activationContext.benchmark_fixture,
      },
    );
    const policy = activation.audio_policy ?? connection.audioPolicy;
    connection.audioPolicy = policy;
    const requestedGateMode = activationContext.gate_mode ?? "transcript_rules";
    this.recordTrace("wake.gate.policy", {
      activation_id: activation.activation_id,
      detail: JSON.stringify({
        requested_gate_mode: requestedGateMode,
        backend_gate_mode: activation.gate_mode,
        configuration_id: policy?.configuration_id ?? null,
        mismatch: requestedGateMode !== activation.gate_mode,
      }),
    });
    this.options.onActivationPolicy?.({
      activationId: activation.activation_id,
      requestedGateMode,
      backendGateMode: activation.gate_mode,
      configurationId: policy?.configuration_id ?? "unknown",
    });
    let resolveGateDecision!: (
      decision: "accepted" | "rejected" | "timed_out",
    ) => void;
    let rejectGateDecision!: (cause: unknown) => void;
    const gateDecision = new Promise<"accepted" | "rejected" | "timed_out">((resolve, reject) => {
      resolveGateDecision = resolve;
      rejectGateDecision = reject;
    });
    let resolveStandbySettlement!: (standby: boolean) => void;
    const standbySettlement = new Promise<boolean>((resolve) => {
      resolveStandbySettlement = resolve;
    });
    const candidate: CandidateActivation = {
      activationId: activation.activation_id,
      unsubscribe: null,
      appendSequence: 0,
      commitSent: false,
      gate: activation,
      gateDecision,
      resolveGateDecision,
      rejectGateDecision,
      standbySettlement,
      resolveStandbySettlement,
      gateSettled: false,
      audioPolicy: policy ?? null,
      orderedAudio: candidateAudio,
      chunkQueue: [],
      committed: false,
      primaryInputItemId: null,
    };
    connection.candidate = candidate;
    connection.mode = "active";
    if (activation.gate_state !== "pending") {
      candidate.gateSettled = true;
      candidate.resolveGateDecision(activation.gate_state);
    }
    this.publish({
      status: "candidate",
      attempt: this.snapshot.attempt,
      session: connection.session,
      error: null,
    });
    this.recordTrace("wake_audio_stream_started", {
      detail: JSON.stringify({
        activation_id: activation.activation_id,
        configuration_id: policy?.configuration_id ?? null,
        commit_owner: policy?.commit_owner ?? "provider",
        cohort,
      }),
    });
    this.recordTrace("wake.gate.started", {
      activation_id: activation.activation_id,
      detail: JSON.stringify({
        activation_id: activation.activation_id,
        gate_mode: activation.gate_mode,
        configuration_id: policy?.configuration_id ?? null,
        commit_owner: policy?.commit_owner ?? "provider",
        cohort,
      }),
    });
    candidate.unsubscribe = candidateAudio.subscribe((chunk) => {
      this.enqueueOrderedChunk(connection, candidate, chunk);
    });
    void candidateAudio.observeEndpoint()
      .then((endpoint) => {
        this.recordTrace("wake_local_endpoint", {
          item_id: String(endpoint.speechEndSample),
          timing_ms: { processing_lag: endpoint.processingLagMs },
          detail: JSON.stringify({
            observed_at_sample: endpoint.observedAtSample,
            trailing_silence_samples: endpoint.trailingSilenceSamples,
          }),
        });
        if (candidate.audioPolicy?.commit_owner === "client") {
          void this.commitOrderedCandidate(connection, candidate);
        }
      })
      .catch((cause) => {
        if (!candidateAudio.isClosed) {
          this.recordTrace("wake_local_endpoint_unavailable", { detail: this.errorName(cause) });
        } else if (connection.candidate === candidate) {
          void this.restoreStandbyAfterCandidateDecision(connection, candidate, "timed_out");
        }
      });
    this.drainOrderedChunks(connection, candidate);
    if (candidate.gateSettled && candidate.gate?.gate_state === "rejected") {
      await this.restoreStandbyAfterCandidateDecision(connection, candidate, "rejected");
    }
  }

  private async streamOrderedCandidate(
    connection: BrowserConnection,
    candidateAudio: JarvisCandidateAudio,
    activationContext: JarvisRealtimeActivationContext | undefined,
    cohort: "warm" | "connecting" | "cold" = "cold",
  ): Promise<void> {
    await this.activateOrderedCandidate(connection, candidateAudio, activationContext, cohort);
  }

  private enqueueOrderedChunk(
    connection: BrowserConnection,
    candidate: CandidateActivation,
    chunk: JarvisAudioChunk,
  ): void {
    if (connection.candidate !== candidate || candidate.orderedAudio === null) {
      chunk.samples.fill(0);
      return;
    }
    candidate.chunkQueue.push({ ...chunk, samples: chunk.samples.slice() });
    this.drainOrderedChunks(connection, candidate);
    const queuedSamples = candidate.chunkQueue.reduce((total, item) => total + item.samples.length, 0);
    if (queuedSamples > 16_000 * 30) {
      for (const item of candidate.chunkQueue) item.samples.fill(0);
      candidate.chunkQueue.length = 0;
      candidate.orderedAudio.close("audio_backlog_overflow");
      this.recordTrace("wake_audio_backlog_overflow", {
        detail: JSON.stringify({ activation_id: candidate.activationId, retained_samples: queuedSamples }),
      });
    } else if (queuedSamples > 16_000 / 2) {
      this.recordTrace("wake_audio_backlog_soft", {
        detail: JSON.stringify({ activation_id: candidate.activationId, retained_samples: queuedSamples }),
      });
    }
  }

  private drainOrderedChunks(connection: BrowserConnection, candidate: CandidateActivation): void {
    if (connection.candidate !== candidate || connection.channel.readyState !== "open") return;
    if (typeof connection.channel.bufferedAmount === "number" && connection.channel.bufferedAmount > 64 * 1024) {
      return;
    }
    while (candidate.chunkQueue.length && connection.channel.readyState === "open") {
      if (
        typeof connection.channel.bufferedAmount === "number"
        && connection.channel.bufferedAmount > 64 * 1024
      ) return;
      const first = candidate.chunkQueue.shift();
      if (!first) return;
      const second = candidate.chunkQueue.length ? candidate.chunkQueue.shift() : undefined;
      const combined = second
        ? Float32Array.from([...first.samples, ...second.samples])
        : first.samples;
      try {
        const envelope = encodeJarvisWakeAudio(combined, 16_000);
        for (const audio of envelope.chunks) {
          connection.channel.send(JSON.stringify({
            type: "input_audio_buffer.append",
            event_id: `jarvis-wake-stream-${candidate.activationId}-${candidate.appendSequence++}`,
            audio,
          }));
        }
      } finally {
        first.samples.fill(0);
        second?.samples.fill(0);
        if (combined !== first.samples) combined.fill(0);
      }
    }
  }

  private attachConversationAudio(
    connection: BrowserConnection,
    audio: JarvisCandidateAudio,
  ): void {
    connection.conversationAudio = audio;
    connection.conversationAudioUnsubscribe = audio.subscribe((chunk) => {
      if (connection.conversationAudio !== audio) {
        chunk.samples.fill(0);
        return;
      }
      connection.conversationChunkQueue.push({ ...chunk, samples: chunk.samples.slice() });
      chunk.samples.fill(0);
      this.drainConversationChunks(connection);
      const retainedSamples = connection.conversationChunkQueue.reduce(
        (total, item) => total + item.samples.length,
        0,
      );
      if (retainedSamples > 16_000 * 30) {
        for (const item of connection.conversationChunkQueue) item.samples.fill(0);
        connection.conversationChunkQueue.length = 0;
        audio.close("audio_backlog_overflow");
        this.recordTrace("wake_audio_backlog_overflow", {
          detail: JSON.stringify({ activation_id: audio.activationId, retained_samples: retainedSamples }),
        });
      } else if (retainedSamples > 16_000 / 2) {
        this.recordTrace("wake_audio_backlog_soft", {
          detail: JSON.stringify({ activation_id: audio.activationId, retained_samples: retainedSamples }),
        });
      }
    });
    this.drainConversationChunks(connection);
    this.recordTrace("wake_audio_conversation_source_attached", {
      activation_id: audio.activationId,
    });
  }

  private drainConversationChunks(connection: BrowserConnection): void {
    if (connection.channel.readyState !== "open") return;
    if (
      typeof connection.channel.bufferedAmount === "number"
      && connection.channel.bufferedAmount > 64 * 1024
    ) return;
    while (connection.conversationChunkQueue.length && connection.channel.readyState === "open") {
      if (
        typeof connection.channel.bufferedAmount === "number"
        && connection.channel.bufferedAmount > 64 * 1024
      ) return;
      const first = connection.conversationChunkQueue.shift();
      if (!first) return;
      const second = connection.conversationChunkQueue.length
        ? connection.conversationChunkQueue.shift()
        : undefined;
      const combined = second
        ? Float32Array.from([...first.samples, ...second.samples])
        : first.samples;
      try {
        const envelope = encodeJarvisWakeAudio(combined, 16_000);
        for (const audio of envelope.chunks) {
          connection.channel.send(JSON.stringify({
            type: "input_audio_buffer.append",
            event_id: `jarvis-conversation-stream-${connection.conversationAudio?.activationId ?? "unknown"}-${connection.conversationAppendSequence++}`,
            audio,
          }));
        }
      } finally {
        first.samples.fill(0);
        second?.samples.fill(0);
        if (combined !== first.samples) combined.fill(0);
      }
    }
  }

  private async commitOrderedCandidate(
    connection: BrowserConnection,
    candidate: CandidateActivation,
  ): Promise<void> {
    if (
      candidate.commitSent
      || candidate.audioPolicy?.commit_owner !== "client"
      || candidate.audioPolicy.turn_mode !== "client_vad"
      || connection.channel.readyState !== "open"
    ) return;
    this.drainOrderedChunks(connection, candidate);
    candidate.commitSent = true;
    const eventId = `jarvis-wake-commit-${candidate.activationId}-${this.snapshot.attempt}`;
    connection.channel.send(JSON.stringify({
      type: "input_audio_buffer.commit",
      event_id: eventId,
    }));
    this.recordTrace("wake_audio_commit_sent", { detail: "client_vad" });
  }

  private async restoreStandbyAfterCandidateDecision(
    connection: BrowserConnection,
    candidate: CandidateActivation,
    decision: "rejected" | "timed_out",
  ): Promise<void> {
    if (connection.candidate !== candidate) return;
    const transport = connection.handoffTransport;
    const silentTrack = connection.silentTransport?.stream.getAudioTracks()[0] ?? null;
    try {
      await connection.sender.replaceTrack(silentTrack);
    } catch (cause) {
      this.recordTrace("wake_gate_standby_restore_failed", {
        detail: this.errorName(cause),
      });
    }
    connection.handoffTransport = null;
    if (candidate.orderedAudio) {
      for (const chunk of candidate.chunkQueue) chunk.samples.fill(0);
      candidate.chunkQueue.length = 0;
      candidate.orderedAudio.close(decision === "rejected" ? "rejected" : "timed_out");
    }
    connection.candidate = null;
    connection.mode = "standby";
    this.recordTrace("wake_gate_activation_ended", {
      detail: JSON.stringify({ decision, activation_id: candidate.activationId }),
    });
    this.publish({
      status: "standby",
      attempt: this.snapshot.attempt,
      session: connection.session,
      error: null,
    });
    candidate.resolveStandbySettlement(true);
    await transport?.destroy().catch(() => undefined);
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
    const providerOwned = connection.audioPolicy?.commit_owner === "provider";
    if (providerOwned) {
      this.recordTrace("wake_audio_upload_started", {
        attempt,
        detail: `bytes:${envelope.byteLength}:chunks:${envelope.chunks.length}:provider_owned`,
      });
      this.enterConnectionStage(tracker, "wake_upload");
      envelope.chunks.forEach((audio, index) => {
        connection.channel.send(JSON.stringify({
          type: "input_audio_buffer.append",
          event_id: `jarvis-wake-append-${activationId}-${attempt}-${index}`,
          audio,
        }));
      });
      this.recordTrace("wake_audio_upload_completed", { attempt, detail: `bytes:${envelope.byteLength}` });
      this.enterConnectionStage(tracker, "microphone_activation");
      await handoffTransport.connectLive();
      this.recordTrace("wake_live_mic_activated", { attempt, detail: "provider_owned" });
      return;
    }
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
    void preparation.silentTransport?.destroy().catch(() => undefined);
  }

  private cancelRotation(): void {
    if (this.rotationTimer === null) return;
    this.dependencies.clearTimer(this.rotationTimer);
    this.rotationTimer = null;
    this.rotationPending = false;
  }

  private cancelDisconnect(): void {
    if (this.disconnectTimer === null) return;
    this.dependencies.clearTimer(this.disconnectTimer);
    this.disconnectTimer = null;
  }

  private async disposeConnection(
    connection: BrowserConnection,
    notifyBackend: boolean,
    preserveConversationAudio?: JarvisCandidateAudio,
  ): Promise<void> {
    if (connection.bargeInTimer !== null) this.dependencies.clearTimer(connection.bargeInTimer);
    if (connection.receiverStatsTimer !== null) {
      this.dependencies.clearTelemetryTimer(connection.receiverStatsTimer);
      connection.receiverStatsTimer = null;
    }
    connection.bargeInDetector?.destroy();
    connection.channel.onbufferedamountlow = null;
    connection.channel.close();
    connection.peer.ontrack = null;
    connection.peer.onconnectionstatechange = null;
    connection.peer.close();
    if (connection.wakeCommitWaiter) {
      this.dependencies.clearTimer(connection.wakeCommitWaiter.timer);
      connection.wakeCommitWaiter.reject(new Error("Realtime connection closed during wake handoff"));
      connection.wakeCommitWaiter = null;
    }
    if (connection.candidate && !connection.candidate.gateSettled) {
      connection.candidate.gateSettled = true;
      connection.candidate.rejectGateDecision(new Error("Realtime connection closed during wake gate"));
    }
    connection.candidate?.unsubscribe?.();
    if (connection.candidate?.orderedAudio && connection.candidate.orderedAudio !== preserveConversationAudio) {
      for (const chunk of connection.candidate.chunkQueue) chunk.samples.fill(0);
      connection.candidate.chunkQueue.length = 0;
      connection.candidate.orderedAudio.close("disconnected");
    }
    connection.conversationAudioUnsubscribe?.();
    connection.conversationAudioUnsubscribe = null;
    for (const chunk of connection.conversationChunkQueue) chunk.samples.fill(0);
    connection.conversationChunkQueue.length = 0;
    if (connection.conversationAudio && connection.conversationAudio !== preserveConversationAudio) {
      connection.conversationAudio.close("disconnected");
    }
    connection.conversationAudio = null;
    connection.candidate?.resolveStandbySettlement(false);
    connection.candidate = null;
    await connection.handoffTransport?.destroy().catch(() => undefined);
    await connection.silentTransport?.destroy().catch(() => undefined);
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
    const eventId = typeof event.event_id === "string" ? event.event_id : undefined;
    const type = typeof event.type === "string" ? event.type : "provider.unknown";
    this.recordTrace("provider.event.received", {
      event_id: eventId,
      detail: type,
    });
    try {
      this.processBrowserProviderEvent(connection, event);
    } finally {
      this.recordTrace("provider.event.handled", {
        event_id: eventId,
        detail: type,
      });
    }
  }

  private processBrowserProviderEvent(
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
      const response = event.response && typeof event.response === "object"
        ? event.response as Record<string, unknown>
        : {};
      const metadata = response.metadata && typeof response.metadata === "object"
        ? response.metadata as Record<string, unknown>
        : {};
      const providerInputItemId = typeof metadata.jarvis_input_item_id === "string"
        ? metadata.jarvis_input_item_id || undefined
        : undefined;
      const eventActivationId = typeof metadata.jarvis_activation_id === "string"
        ? metadata.jarvis_activation_id || undefined
        : undefined;
      const eventRequestId = typeof metadata.jarvis_request_id === "string"
        ? metadata.jarvis_request_id || undefined
        : undefined;
      const responseStatus = typeof response.status === "string" ? response.status : undefined;
      const statusDetails = response.status_details && typeof response.status_details === "object"
        ? response.status_details as Record<string, unknown>
        : undefined;
      this.recordTrace(type, {
        event_id: typeof event.event_id === "string" ? event.event_id : undefined,
        response_id: responseId,
        item_id: itemId ?? providerInputItemId,
        provider_input_item_id: providerInputItemId,
        activation_id: eventActivationId,
        request_id: eventRequestId,
        detail: responseStatus || statusDetails
          ? JSON.stringify({
            ...(responseStatus ? { status: responseStatus } : {}),
            ...(typeof statusDetails?.reason === "string" ? { status_reason: statusDetails.reason } : {}),
          })
          : undefined,
      });
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
    if (type === "input_audio_buffer.committed" && connection.candidate?.orderedAudio) {
      const candidate = connection.candidate;
      candidate.committed = true;
      candidate.primaryInputItemId = itemId ?? null;
      this.recordTrace("wake_audio_commit_acknowledged", { item_id: itemId });
      this.maybePromoteOrderedCandidate(connection, candidate);
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
        const response = event.response && typeof event.response === "object"
          ? event.response as Record<string, unknown>
          : {};
        const metadata = response.metadata && typeof response.metadata === "object"
          ? response.metadata as Record<string, unknown>
          : {};
        connection.responseCorrelation = {
          requestId: typeof metadata.jarvis_request_id === "string"
            ? metadata.jarvis_request_id
            : "",
          responseId: connection.activeResponseId,
          activationId: typeof metadata.jarvis_activation_id === "string"
            ? metadata.jarvis_activation_id || null
            : null,
          inputItemId: typeof metadata.jarvis_input_item_id === "string"
            ? metadata.jarvis_input_item_id || null
            : null,
          state: "generating",
        };
        if (!this.localSpeechActive && this.latestLocalSpeechEndpoint) {
          this.responseSpeechEndpoints.set(
            connection.activeResponseId,
            this.latestLocalSpeechEndpoint,
          );
          this.latestLocalSpeechEndpoint = null;
        }
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
      if (connection.responseCorrelation && responseId === connection.responseCorrelation.responseId) {
        connection.responseCorrelation.state = "speaking";
      }
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
      const onsetCorrelation = connection.responseCorrelation;
      if (onsetCorrelation) {
        this.requestBrowserBargeIn(
          connection,
          "browser_provider_vad",
          typeof event.event_id === "string" ? event.event_id : crypto.randomUUID(),
          itemId ?? null,
          onsetCorrelation,
        );
      }
      return;
    }
    if (type === "response.done" && (!responseId || responseId === connection.activeResponseId)) {
      const completedActivationId = connection.responseCorrelation?.activationId ?? null;
      this.scheduleTurnResult(TURN_RESULT_SETTLE_MS);
      connection.activeResponseId = null;
      connection.responseCorrelation = null;
      connection.cancellingResponseId = null;
      if (completedActivationId && completedActivationId === this.activationId) {
        this.activationId = null;
      }
      this.setPlaybackMuted(connection, false);
      void this.maybeRotate();
      return;
    }
    if (
      (type === "output_audio_buffer.cleared" || type === "output_audio_buffer.stopped")
      && (!responseId || responseId === connection.activeResponseId)
    ) {
      connection.activeResponseId = null;
      connection.responseCorrelation = null;
      connection.cancellingResponseId = null;
      this.setPlaybackMuted(connection, false);
    }
  }

  private handleLocalSpeechStart(
    connection: BrowserConnection | null,
    operation: number,
  ): void {
    if (!connection || operation !== this.operation || this.active?.peer !== connection.peer) return;
    this.localSpeechActive = true;
    const onsetCorrelation = connection.responseCorrelation;
    this.recordTrace("local_speech_started", {
      response_id: onsetCorrelation?.responseId ?? connection.activeResponseId ?? undefined,
    });
    if (!onsetCorrelation) return;
    this.setPlaybackMuted(connection, true);
    if (connection.bargeInTimer !== null) return;
    connection.bargeInTimer = this.dependencies.setTimer(() => {
      connection.bargeInTimer = null;
      this.requestBrowserBargeIn(
        connection,
        "browser_local_vad",
        crypto.randomUUID(),
        null,
        onsetCorrelation,
      );
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
    this.localSpeechActive = false;
    const entry = this.recordTrace("local_speech_stopped", {
      response_id: connection.activeResponseId ?? undefined,
    });
    this.rememberLocalSpeechEndpoint(entry, "local_speech_stopped");
    void this.maybeRotate();
  }

  private requestBrowserBargeIn(
    connection: BrowserConnection,
    source: "browser_local_vad" | "browser_provider_vad",
    speechEventId: string,
    inputItemId: string | null,
    capturedCorrelation: BrowserResponseCorrelation | null = connection.responseCorrelation,
  ): void {
    const correlation = capturedCorrelation;
    if (!correlation?.requestId || !this.dependencies.bargeIn) return;
    if (correlation.state === "cancelling") return;
    correlation.state = "cancelling";
    this.recordTrace("response.barge_in.requested", {
      response_id: correlation.responseId,
      item_id: correlation.inputItemId ?? undefined,
      detail: JSON.stringify({ source, speech_event_id: speechEventId }),
    });
    void this.dependencies.bargeIn(connection.session.id, {
      generation: connection.session.generation,
      speech_event_id: speechEventId,
      source,
      request_id: correlation.requestId,
      response_id: correlation.responseId,
      input_item_id: inputItemId ?? correlation.inputItemId,
    }).then((result) => {
      this.recordTrace("response.barge_in.result", {
        response_id: correlation.responseId,
        detail: JSON.stringify({ accepted: result.accepted, source }),
      });
    }).catch((cause) => {
      this.recordTrace("response.barge_in.failed", {
        response_id: correlation.responseId,
        detail: this.errorName(cause),
      });
    });
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
    monotonicMs = this.dependencies.now(),
  ): JarvisRealtimeTraceEntry {
    const entry: JarvisRealtimeTraceEntry = {
      kind,
      monotonic_ms: monotonicMs,
      wall_time: this.dependencies.wallNow(),
      ...(this.activationId ? { activation_id: this.activationId } : {}),
      ...details,
    };
    this.trace.push(entry);
    if (this.trace.length > MAX_BROWSER_TRACE_EVENTS) {
      this.trace.splice(0, this.trace.length - MAX_BROWSER_TRACE_EVENTS);
    }
    if (!PERSISTED_BROWSER_TELEMETRY_EVENTS.has(entry.kind)) return entry;
    this.enqueueTelemetryEvent(this.telemetryEventFromTrace(entry));
    return entry;
  }

  private rememberLocalSpeechEndpoint(
    entry: JarvisRealtimeTraceEntry,
    source: LocalSpeechEndpoint["source"],
  ): void {
    this.latestLocalSpeechEndpoint = {
      monotonicMs: entry.monotonic_ms,
      source,
      activationId: this.activationId,
    };
  }

  private telemetryEventFromTrace(
    entry: JarvisRealtimeTraceEntry,
  ): JarvisRealtimeTelemetryEvent {
    const traceSession = this.active?.session ?? this.connectingSession;
    let detail: Record<string, unknown> = {
      ...(entry.attempt === undefined ? {} : { attempt: entry.attempt }),
      ...(entry.sequence === undefined ? {} : { sequence: entry.sequence }),
      ...(entry.server_time === undefined ? {} : { server_time: entry.server_time }),
      ...(entry.event_id === undefined ? {} : { event_id: entry.event_id }),
      ...(entry.timing_ms === undefined ? {} : { timing_ms: entry.timing_ms }),
      ...(entry.detail === undefined ? {} : { detail: entry.detail.slice(0, 500) }),
    };
    if (
      (entry.kind === "turn.result" || entry.kind === "turn.result.amendment")
      && entry.detail
    ) {
      try {
        const terminalRecord: unknown = JSON.parse(entry.detail);
        if (terminalRecord && typeof terminalRecord === "object") {
          detail = terminalRecord as Record<string, unknown>;
        }
      } catch {
        // Keep the bounded diagnostic representation for malformed data.
      }
    }
    return {
      trace_id: this.traceId,
      client_sequence: ++this.telemetryClientSequence,
      ...(entry.activation_id ? { activation_id: entry.activation_id } : {}),
      ...(traceSession ? {
        session_id: traceSession.id,
        generation: traceSession.generation,
      } : {}),
      ...(entry.request_id ? { request_id: entry.request_id } : {}),
      ...(entry.response_id ? { provider_response_id: entry.response_id } : {}),
      ...((entry.provider_input_item_id ?? entry.item_id)
        ? { provider_input_item_id: entry.provider_input_item_id ?? entry.item_id }
        : {}),
      source: "browser",
      event_type: entry.kind.slice(0, 128),
      observed_at: entry.wall_time,
      monotonic_ms: entry.monotonic_ms,
      detail,
    };
  }

  private enqueueTelemetryEvent(event: JarvisRealtimeTelemetryEvent): void {
    this.telemetryPending.push(event);
    const overflow = this.telemetryPending.length - MAX_PENDING_TELEMETRY_EVENTS;
    if (overflow <= 0) return;
    this.telemetryPending.splice(0, overflow);
    this.telemetryDropped += overflow;
    this.recordTelemetryIntegrity("queue_overflow");
  }

  private recordTelemetryIntegrity(
    reason: "queue_overflow" | "repeated_submission_failure" | "page_shutdown_pending",
    detail: Record<string, unknown> = {},
  ): void {
    const existing = this.telemetryPending.find(
      (event) => event.event_type === "telemetry.integrity",
    );
    const existingIntegrity = existing?.detail?.integrity;
    const existingReasons = (
      existingIntegrity
      && typeof existingIntegrity === "object"
      && Array.isArray((existingIntegrity as Record<string, unknown>).reasons)
    )
      ? (existingIntegrity as { reasons: unknown[] }).reasons.filter(
          (value): value is string => typeof value === "string",
        )
      : [];
    const integrity = {
      reasons: [...new Set([...existingReasons, reason])],
      dropped_count: this.telemetryDropped,
      consecutive_failures: this.telemetryConsecutiveFailures,
      ...detail,
    };
    if (existing) {
      existing.detail = { integrity };
      return;
    }
    if (this.telemetryPending.length >= MAX_PENDING_TELEMETRY_EVENTS) {
      this.telemetryPending.shift();
      this.telemetryDropped += 1;
      integrity.dropped_count = this.telemetryDropped;
    }
    const entry: JarvisRealtimeTraceEntry = {
      kind: "telemetry.integrity",
      monotonic_ms: this.dependencies.now(),
      wall_time: this.dependencies.wallNow(),
      detail: JSON.stringify(integrity),
    };
    this.trace.push(entry);
    if (this.trace.length > MAX_BROWSER_TRACE_EVENTS) {
      this.trace.splice(0, this.trace.length - MAX_BROWSER_TRACE_EVENTS);
    }
    const event = this.telemetryEventFromTrace(entry);
    event.detail = { integrity };
    this.telemetryPending.push(event);
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
        const acceptedResult = await this.dependencies.sendTelemetry?.(batch);
        const accepted = typeof acceptedResult === "number" && Number.isFinite(acceptedResult)
          ? Math.max(0, Math.min(batch.length, Math.floor(acceptedResult)))
          : batch.length;
        sent = accepted > 0;
        if (accepted < batch.length) {
          this.restoreTelemetryTail(batch.slice(accepted));
        }
        if (accepted > 0) this.telemetryConsecutiveFailures = 0;
        else this.telemetryConsecutiveFailures += 1;
      } catch {
        this.restoreTelemetryTail(batch);
        this.telemetryConsecutiveFailures += 1;
      } finally {
        if (this.telemetryConsecutiveFailures >= TELEMETRY_FAILURE_INTEGRITY_THRESHOLD) {
          this.recordTelemetryIntegrity("repeated_submission_failure");
        }
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

  private restoreTelemetryTail(events: JarvisRealtimeTelemetryEvent[]): void {
    if (!events.length) return;
    this.telemetryPending.unshift(...events);
    const overflow = this.telemetryPending.length - MAX_PENDING_TELEMETRY_EVENTS;
    if (overflow <= 0) return;
    this.telemetryPending.splice(0, overflow);
    this.telemetryDropped += overflow;
    this.recordTelemetryIntegrity("queue_overflow");
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
    const summary = summarizeJarvisRealtimeTrace(this.trace);
    if (summary.turn_results?.length) {
      for (const result of summary.turn_results) {
        const previous = this.reportedV3Turns.get(result.turn_key);
        const serialized = JSON.stringify(result);
        if (previous === serialized) continue;
        this.reportedV3Turns.set(result.turn_key, serialized);
        this.recordTrace(previous ? "turn.result.amendment" : "turn.result", {
          activation_id: result.activation_id ?? undefined,
          session_id: result.session_id ?? undefined,
          generation: result.generation ?? undefined,
          item_id: result.input_item_id ?? undefined,
          request_id: result.request_id ?? undefined,
          response_id: result.response_id ?? undefined,
          clock_uncertainty_ms: result.clock_uncertainty_ms ?? undefined,
          detail: JSON.stringify({
            ...result,
            ...(previous ? { amends_turn_key: result.turn_key } : {}),
          }),
        });
      }
      return;
    }
    const turns = summary.turns;
    for (const turn of turns) {
      const key = turn.input_item_id
        ? `item:${turn.input_item_id}`
        : `boundary:${turn.boundary_monotonic_ms}`;
      if (this.reportedTurns.has(key)) continue;
      this.reportedTurns.add(key);
      const timingMs = Object.fromEntries(
        Object.entries({
          endpointing: turn.local_speech_stop_to_provider_stop_ms,
          wake_handoff: turn.wake_handoff_tail_ms,
          connection: turn.provider_connection_ms,
          upload: turn.wake_audio_upload_ms,
          transcription: turn.transcription_ms,
          arbitration: turn.response_arbitration_ms,
          generation: turn.generation_ms,
          gate: turn.gate_started_to_decision_ms,
          speech_end_to_gate_decision: turn.local_speech_stop_to_gate_decision_ms,
          standby_age: turn.standby_age_ms,
          media_arrival: turn.response_created_to_first_playable_media_ms,
          speech_end_to_first_playable: turn.browser_speech_end_to_first_playable_ms,
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
          gate_decision: turn.gate_decision ?? null,
          gate_decision_source: turn.gate_decision_source ?? null,
          standby_ready: turn.standby_ready ?? null,
          standby_age_ms: turn.standby_age_ms ?? null,
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
        const playableMonotonic = this.dependencies.now();
        const endpoint = this.responseSpeechEndpoints.get(responseId);
        const speechToPlayableMs = endpoint && playableMonotonic >= endpoint.monotonicMs
          ? Math.round((playableMonotonic - endpoint.monotonicMs) * 10) / 10
          : undefined;
        this.recordTrace("first_playable_media", {
          response_id: responseId,
          timing_ms: speechToPlayableMs === undefined
            ? undefined
            : { speech_end_to_first_playable: speechToPlayableMs },
          detail: JSON.stringify({
            proxy: "inbound_audio_bytes_increased_while_playing",
            sample: reason,
            speech_endpoint_source: endpoint?.source ?? null,
            speech_endpoint_activation_id: endpoint?.activationId ?? null,
          }),
        }, playableMonotonic);
        this.responseSpeechEndpoints.delete(responseId);
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

interface TurnBoundary {
  boundaryIndex: number;
  providerStopIndex: number;
  inputItemId?: string;
  committedIndex?: number;
}

const GATE_START_KINDS = [
  "wake.gate.started",
  "wake.verification.started",
  "backend.wake.gate.started",
  "backend.wake.verification.started",
] as const;

const GATE_DECISION_KINDS = [
  "wake.gate.accepted",
  "wake.gate.rejected",
  "wake.gate.timed_out",
  "wake.verification.accepted",
  "wake.verification.rejected",
  "wake.verification.timed_out",
  "backend.wake.gate.accepted",
  "backend.wake.gate.rejected",
  "backend.wake.gate.timed_out",
  "backend.wake.verification.accepted",
  "backend.wake.verification.rejected",
  "backend.wake.verification.timed_out",
] as const;

const RESPONSE_CREATE_SENT_KINDS = [
  "response.create.sent",
  "response_create.sent",
  "response.create",
] as const;

const STANDBY_READY_KINDS = [
  "realtime_standby_ready",
  "standby.ready",
  "realtime_connection_ready",
] as const;

function collectTurnBoundaries(entries: readonly JarvisRealtimeTraceEntry[]): TurnBoundary[] {
  const boundaries: TurnBoundary[] = [];
  entries.forEach((entry, index) => {
    const isCommitted = entry.kind === "input_audio_buffer.committed";
    const isSpeechStopped = entry.kind === "input_audio_buffer.speech_stopped";
    if (!isCommitted && !isSpeechStopped) return;
    const previous = boundaries.at(-1);
    if (!previous) {
      boundaries.push({
        boundaryIndex: index,
        providerStopIndex: index,
        ...(isCommitted ? { committedIndex: index } : {}),
        ...(entry.item_id ? { inputItemId: entry.item_id } : {}),
      });
      return;
    }
    const responseBetween = firstTrace(
      entries,
      ["response.created", "response.done"],
      previous.boundaryIndex + 1,
      index,
    );
    if (responseBetween) {
      boundaries.push({
        boundaryIndex: index,
        providerStopIndex: index,
        ...(isCommitted ? { committedIndex: index } : {}),
        ...(entry.item_id ? { inputItemId: entry.item_id } : {}),
      });
      return;
    }
    const wakeDetected = firstTrace(
      entries,
      ["wake_detected"],
      previous.boundaryIndex + 1,
      index + 1,
    ) ?? firstTrace(entries, ["wake_detected"], 0, previous.boundaryIndex + 1);
    const sameActivation = Boolean(
      entries[previous.boundaryIndex].activation_id
      && entries[previous.boundaryIndex].activation_id === entry.activation_id,
    );
    const nearProviderBoundary = entry.monotonic_ms - entries[previous.boundaryIndex].monotonic_ms <= 250;
    const shouldMerge = (
      previous.committedIndex !== undefined
      || isCommitted
    ) && Boolean(wakeDetected || nearProviderBoundary || sameActivation);
    if (!shouldMerge) {
      boundaries.push({
        boundaryIndex: index,
        providerStopIndex: index,
        ...(isCommitted ? { committedIndex: index } : {}),
        ...(entry.item_id ? { inputItemId: entry.item_id } : {}),
      });
      return;
    }
    previous.boundaryIndex = index;
    if (isSpeechStopped) previous.providerStopIndex = index;
    if (isCommitted) previous.committedIndex = index;
    if (isCommitted && entry.item_id) previous.inputItemId = entry.item_id;
    if (!previous.inputItemId && entry.item_id) previous.inputItemId = entry.item_id;
  });
  return boundaries;
}

function gateDecision(
  entry: JarvisRealtimeTraceEntry | undefined,
): JarvisRealtimeTurnTiming["gate_decision"] {
  if (!entry) return undefined;
  if (entry.kind.endsWith(".accepted")) return "accepted";
  if (entry.kind.endsWith(".rejected")) return "rejected";
  if (entry.kind.endsWith(".timed_out")) return "timed_out";
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

  const boundaries = collectTurnBoundaries(entries);
  const turns = boundaries.map((boundary, turnIndex) => {
    const stopIndex = boundary.boundaryIndex;
    const segmentStart = turnIndex === 0 ? 0 : boundaries[turnIndex - 1].boundaryIndex + 1;
    const segmentEnd = boundaries[turnIndex + 1]?.boundaryIndex ?? entries.length;
    const providerStop = entries[boundary.providerStopIndex];
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
    const responseCreated = firstTrace(entries, ["response.created"], stopIndex + 1, segmentEnd);
    const responseIndex = responseCreated ? entries.indexOf(responseCreated) : segmentEnd;
    const backendTranscript = lastTrace(
      entries,
      ["backend.provider.user_transcript"],
      segmentStart,
      responseIndex,
    );
    const transcript = backendTranscript ?? lastTrace(
      entries,
      ["conversation.item.input_audio_transcription.completed"],
      segmentStart,
      responseIndex,
    );
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
    const gateStarted = firstTrace(entries, GATE_START_KINDS, segmentStart, responseIndex);
    const gateDecisionEntry = firstTrace(entries, GATE_DECISION_KINDS, segmentStart, responseIndex);
    const decision = gateDecision(gateDecisionEntry);
    const standbyReadyEntry = lastTrace(
      entries,
      STANDBY_READY_KINDS,
      0,
      wakeDetected ? entries.indexOf(wakeDetected) : segmentStart,
    );
    const standbyReady = standbyReadyEntry && Boolean(wakeDetected);
    const standbyAgeMs = standbyReadyEntry && wakeDetected
      ? elapsedMilliseconds(standbyReadyEntry, wakeDetected)
      : undefined;
    const responseCreateSent = firstTrace(entries, RESPONSE_CREATE_SENT_KINDS, segmentStart, responseIndex);
    const endpointMs = elapsedMilliseconds(localStop, utteranceReady);
    const providerConnectionMs = elapsedMilliseconds(controlStart, readinessBarrier);
    const audioHandoffMs = elapsedMilliseconds(uploadStarted, commitAcknowledged ?? providerStop);
    const transcriptionMs = elapsedMilliseconds(providerStop, transcript);
    const responseArbitrationMs = elapsedMilliseconds(transcript, arbiter);
    const generationMs = elapsedMilliseconds(arbiter ?? responseCreated, firstAudio);
    const mediaArrivalMs = elapsedMilliseconds(firstAudio ?? responseCreated, firstPlayableMedia);
    const browserSpeechEndToPlayableMs = firstPlayableMedia?.timing_ms?.speech_end_to_first_playable;
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
      boundary_monotonic_ms: providerStop.monotonic_ms,
      response_id: responseId,
      ...(boundary.inputItemId || providerStop.item_id
        ? { input_item_id: boundary.inputItemId ?? providerStop.item_id }
        : {}),
      local_speech_stop_to_provider_stop_ms: elapsedMilliseconds(localStop, providerStop),
      wake_handoff_tail_ms: elapsedMilliseconds(localStop, utteranceReady),
      provider_stop_to_response_created_ms: elapsedMilliseconds(providerStop, responseCreated),
      response_created_to_first_audio_ms: elapsedMilliseconds(responseCreated, firstAudio),
      provider_stop_to_first_playable_media_ms: elapsedMilliseconds(providerStop, firstPlayableMedia),
      local_speech_stop_to_first_playable_media_ms: browserSpeechEndToPlayableMs
        ?? elapsedMilliseconds(localStop, firstPlayableMedia),
      browser_speech_end_to_first_playable_ms: browserSpeechEndToPlayableMs,
      backend_arbiter_ms: arbiter?.timing_ms?.total_to_provider_submit,
      provider_boundary_to_transcript_ms: backendTranscript?.timing_ms?.provider_boundary_to_transcript,
      transcript_to_arbiter_ms: elapsedMilliseconds(transcript, arbiter),
      arbiter_to_response_created_ms: elapsedMilliseconds(arbiter, responseCreated),
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
      gate_started_to_decision_ms: elapsedMilliseconds(gateStarted, gateDecisionEntry),
      local_speech_stop_to_gate_decision_ms: elapsedMilliseconds(localStop, gateDecisionEntry),
      ...(decision ? { gate_decision: decision } : {}),
      ...(gateDecisionEntry ? { gate_decision_source: gateDecisionEntry.kind } : {}),
      ...(standbyReady === undefined ? {} : { standby_ready: standbyReady }),
      ...(standbyAgeMs === undefined ? {} : { standby_age_ms: standbyAgeMs }),
      response_create_sent_to_created_ms: elapsedMilliseconds(responseCreateSent, responseCreated)
        ?? providerGenerating?.timing_ms?.response_create_sent_to_created,
      outcome: decision === "rejected"
        ? "gate_rejected"
        : decision === "timed_out"
          ? "gate_timed_out"
          : !transcript
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

  const v3 = summarizeJarvisRealtimeTurns(
    entries as readonly JarvisRealtimeReducerEntry[],
  );
  const hasPhaseOneCorrelation = entries.some((entry) => (
    entry.request_id !== undefined
    || entry.provider_input_item_id !== undefined
    || (
      entry.activation_id !== undefined
      && entry.item_id !== undefined
      && entry.kind.includes("committed")
    )
  ));
  return {
    schema: "jarvis.realtime_timing.v2",
    connections,
    turns,
    ...(hasPhaseOneCorrelation && v3.turns.length ? { turn_results: v3.turns } : {}),
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

function adaptLegacyCandidateAudio(
  legacy: JarvisRealtimeCandidateAudio,
  activationId: string,
): JarvisCandidateAudio {
  const owner = createJarvisActivationAudio(activationId);
  const now = typeof performance !== "undefined" ? performance.now() : Date.now();
  if (legacy.sampleRate === 16_000 && legacy.preRoll.length) {
    owner.append(
      legacy.preRoll,
      now - legacy.preRoll.length / 16_000 * 1_000,
    );
  } else {
    legacy.preRoll.fill(0);
  }
  legacy.subscribe((samples) => {
    if (legacy.sampleRate === 16_000) owner.append(samples, performance.now());
    else samples.fill(0);
  });
  void legacy.endpoint.then((endpoint) => {
    const totalSamples = Math.max(0, endpoint.totalSamples);
    owner.resolveEndpoint({
      sampleRate: 16_000,
      speechEndSample: totalSamples,
      observedAtSample: totalSamples,
      trailingSilenceSamples: Math.max(
        0,
        Math.round(endpoint.trailingSilenceMs / 1_000 * 16_000),
      ),
      processingLagMs: 0,
    });
  }).catch(() => owner.close("disconnected"));
  return owner;
}

function adaptLegacyHandoffToActivationAudio(
  handoff: JarvisRealtimeAudioHandoff,
  activationId: string,
): JarvisCandidateAudio {
  const owner = createJarvisActivationAudio(activationId);
  void handoff.utterance.then((samples) => {
    if (owner.isClosed || handoff.sampleRate !== 16_000) {
      samples.fill(0);
      owner.close("disconnected");
      return;
    }
    const totalSamples = samples.length;
    owner.append(samples);
    owner.resolveEndpoint({
      sampleRate: 16_000,
      speechEndSample: totalSamples,
      observedAtSample: totalSamples,
      trailingSilenceSamples: 0,
      processingLagMs: 0,
    });
  }).catch(() => owner.close("disconnected"));
  return owner;
}

function isOrderedCandidateAudio(
  value: JarvisRealtimeCandidateAudio | JarvisCandidateAudio | undefined,
): value is JarvisCandidateAudio {
  return Boolean(
    value
    && typeof (value as JarvisCandidateAudio).observeEndpoint === "function"
    && typeof (value as JarvisCandidateAudio).promoteToConversation === "function"
    && typeof (value as JarvisCandidateAudio).close === "function",
  );
}

async function createBrowserSilentTransport(): Promise<JarvisRealtimeSilentTransport> {
  const context = new AudioContext();
  const oscillator = context.createOscillator();
  const gain = context.createGain();
  const destination = context.createMediaStreamDestination();
  gain.gain.value = 0;
  oscillator.connect(gain);
  gain.connect(destination);
  oscillator.start();
  if (context.state === "suspended") await context.resume();
  let destroyed = false;
  return {
    stream: destination.stream,
    destroy: async () => {
      if (destroyed) return;
      destroyed = true;
      try { oscillator.stop(); } catch { /* Already stopped. */ }
      oscillator.disconnect();
      gain.disconnect();
      for (const track of destination.stream.getTracks()) track.stop();
      await context.close();
    },
  };
}
