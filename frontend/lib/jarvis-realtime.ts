import {
  closeJarvisRealtimeSession,
  createJarvisRealtimeCall,
  type JarvisLiveEvent,
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
  handoffOutput: JarvisRealtimeHandoffOutput | null;
  session: JarvisSession;
  rotateAfterSeconds: number;
  bargeInDetector: JarvisBargeInDetector | null;
  activeResponseId: string | null;
  mutedResponseId: string | null;
  tracedAudioDeltaResponses: Set<string>;
  tracedTranscriptDeltaItems: Set<string>;
}

interface JarvisRealtimeHandoffOutput {
  stream: MediaStream;
  start: () => Promise<void>;
  destroy: () => Promise<void>;
}

interface JarvisRealtimeConnectionInput {
  localStream: MediaStream;
  ownsLocalStream: boolean;
  audioHandoff?: JarvisRealtimeAudioHandoff;
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
  createHandoffOutput: (
    handoff: JarvisRealtimeAudioHandoff,
  ) => Promise<JarvisRealtimeHandoffOutput>;
}

const RETRY_DELAYS_MS = [0, 500, 1_000, 2_000] as const;
const DISCONNECT_GRACE_MS = 2_000;
const ICE_CANDIDATE_WAIT_MS = 750;

const browserDependencies: JarvisRealtimeDependencies = {
  createPeer: () => new RTCPeerConnection(),
  getUserMedia: (constraints) => navigator.mediaDevices.getUserMedia(constraints),
  createCall: createJarvisRealtimeCall,
  closeSession: closeJarvisRealtimeSession,
  wait: (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds)),
  setTimer: (callback, milliseconds) => window.setTimeout(callback, milliseconds),
  clearTimer: (timer) => window.clearTimeout(timer),
  now: () => performance.now(),
  wallNow: () => new Date().toISOString(),
  createBargeInDetector: createJarvisBargeInDetector,
  createHandoffOutput: createBrowserHandoffOutput,
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
          audioHandoff: options.audioHandoff,
          activationContext: options.activationContext,
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
    let handoffOutput: JarvisRealtimeHandoffOutput | null = null;
    try {
      let outgoingStream = localStream;
      if (input?.audioHandoff) {
        this.recordTrace("wake_handoff_output_started", { attempt });
        handoffOutput = await this.dependencies.createHandoffOutput(input.audioHandoff);
        outgoingStream = handoffOutput.stream;
        this.recordTrace("wake_handoff_output_ready", { attempt });
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
        handoffOutput,
        session: providerSession,
        rotateAfterSeconds: response.rotate_after_seconds ?? 55 * 60,
        bargeInDetector: null,
        activeResponseId: null,
        mutedResponseId: null,
        tracedAudioDeltaResponses: new Set(),
        tracedTranscriptDeltaItems: new Set(),
      };
      if (handoffOutput) {
        this.recordTrace("wake_utterance_handoff_requested", { attempt });
        void handoffOutput.start()
          .then(() => this.recordTrace("wake_utterance_handoff_started"))
          .catch((cause) => this.recordTrace("wake_utterance_handoff_failed", {
            detail: cause instanceof Error ? cause.message : "unknown",
          }));
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
      await handoffOutput?.destroy().catch(() => undefined);
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
    connection.bargeInDetector?.destroy();
    connection.channel.close();
    connection.peer.ontrack = null;
    connection.peer.onconnectionstatechange = null;
    connection.peer.close();
    await connection.handoffOutput?.destroy().catch(() => undefined);
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
      return;
    }
    if (
      type === "output_audio_buffer.cleared"
      || type === "output_audio_buffer.stopped"
    ) {
      connection.activeResponseId = null;
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
    if (connection.channel.readyState !== "open") return;
    connection.channel.send(JSON.stringify({
      type: "output_audio_buffer.clear",
      event_id: `jarvis-barge-in-${crypto.randomUUID()}`,
    }));
    this.recordTrace("output_audio_buffer.clear.sent", {
      response_id: connection.activeResponseId,
    });
  }

  private handleLocalSpeechEnd(
    connection: BrowserConnection | null,
    operation: number,
  ): void {
    if (!connection || operation !== this.operation || this.active?.peer !== connection.peer) return;
    this.recordTrace("local_speech_stopped", {
      response_id: connection.activeResponseId ?? undefined,
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
    this.options.onPlaybackMuted?.(muted);
  }

  private recordTrace(
    kind: string,
    details: Omit<JarvisRealtimeTraceEntry, "kind" | "monotonic_ms" | "wall_time"> = {},
  ): void {
    this.trace.push({
      kind,
      monotonic_ms: this.dependencies.now(),
      wall_time: this.dependencies.wallNow(),
      ...details,
    });
    if (this.trace.length > 2_000) this.trace.splice(0, this.trace.length - 2_000);
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

async function createBrowserHandoffOutput(
  handoff: JarvisRealtimeAudioHandoff,
): Promise<JarvisRealtimeHandoffOutput> {
  const context = new AudioContext();
  const liveSource = context.createMediaStreamSource(handoff.localStream);
  const destination = context.createMediaStreamDestination();
  let replaySource: AudioBufferSourceNode | null = null;
  let destroyed = false;
  let started = false;
  const connectLive = () => {
    if (!destroyed) liveSource.connect(destination);
  };
  return {
    stream: destination.stream,
    start: async () => {
      if (started) return;
      started = true;
      if (context.state === "suspended") await context.resume();
      try {
        const samples = await handoff.utterance;
        if (destroyed) {
          samples.fill(0);
          return;
        }
        if (!samples.length) {
          connectLive();
          return;
        }
        const buffer = context.createBuffer(1, samples.length, handoff.sampleRate);
        buffer.getChannelData(0).set(samples);
        samples.fill(0);
        replaySource = context.createBufferSource();
        replaySource.buffer = buffer;
        replaySource.connect(destination);
        replaySource.onended = () => {
          replaySource?.disconnect();
          replaySource = null;
          connectLive();
        };
        replaySource.start();
      } catch (cause) {
        connectLive();
        throw cause;
      }
    },
    destroy: async () => {
      if (destroyed) return;
      destroyed = true;
      replaySource?.stop();
      replaySource?.disconnect();
      liveSource.disconnect();
      for (const track of destination.stream.getTracks()) track.stop();
      await context.close();
    },
  };
}
