import {
  closeJarvisRealtimeSession,
  createJarvisRealtimeCall,
  type JarvisSession,
} from "./api";

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
}

interface BrowserConnection {
  peer: RTCPeerConnection;
  channel: RTCDataChannel;
  localStream: MediaStream;
  session: JarvisSession;
  rotateAfterSeconds: number;
}

export interface JarvisRealtimeDependencies {
  createPeer: () => RTCPeerConnection;
  getUserMedia: (constraints: MediaStreamConstraints) => Promise<MediaStream>;
  createCall: typeof createJarvisRealtimeCall;
  closeSession: typeof closeJarvisRealtimeSession;
  wait: (milliseconds: number) => Promise<void>;
  setTimer: (callback: () => void, milliseconds: number) => number;
  clearTimer: (timer: number) => void;
}

const RETRY_DELAYS_MS = [0, 500, 1_000, 2_000] as const;
const DISCONNECT_GRACE_MS = 2_000;

const browserDependencies: JarvisRealtimeDependencies = {
  createPeer: () => new RTCPeerConnection(),
  getUserMedia: (constraints) => navigator.mediaDevices.getUserMedia(constraints),
  createCall: createJarvisRealtimeCall,
  closeSession: closeJarvisRealtimeSession,
  wait: (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds)),
  setTimer: (callback, milliseconds) => window.setTimeout(callback, milliseconds),
  clearTimer: (timer) => window.clearTimeout(timer),
};

export class JarvisRealtimeController {
  private readonly options: JarvisRealtimeControllerOptions;
  private readonly dependencies: JarvisRealtimeDependencies;
  private active: BrowserConnection | null = null;
  private rotationTimer: number | null = null;
  private disconnectTimer: number | null = null;
  private operation = 0;
  private reconnecting = false;
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

  async start(): Promise<JarvisRealtimeSnapshot> {
    const operation = ++this.operation;
    this.cancelRotation();
    this.cancelDisconnect();
    return this.connectWithRetry(operation, false);
  }

  async stop(): Promise<void> {
    ++this.operation;
    this.cancelRotation();
    this.cancelDisconnect();
    const current = this.active;
    this.active = null;
    if (current) await this.disposeConnection(current, true);
    this.options.onRemoteStream?.(null);
    this.publish({ status: "idle", attempt: 0, session: null, error: null });
  }

  private async reconnect(): Promise<void> {
    if (this.reconnecting || this.snapshot.status === "idle") return;
    this.reconnecting = true;
    const operation = ++this.operation;
    this.cancelRotation();
    this.cancelDisconnect();
    try {
      await this.connectWithRetry(operation, true);
    } catch {
      // connectWithRetry publishes the terminal fallback state.
    } finally {
      this.reconnecting = false;
    }
  }

  private async connectWithRetry(
    operation: number,
    reconnecting: boolean,
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
        const connection = await this.connectAttempt(operation);
        if (operation !== this.operation) {
          await this.disposeConnection(connection, true);
          throw new Error("Realtime connection was cancelled");
        }
        const previous = this.active;
        this.active = connection;
        this.cancelDisconnect();
        if (previous) await this.disposeConnection(previous, false);
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

  private async connectAttempt(operation: number): Promise<BrowserConnection> {
    const localStream = await this.dependencies.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const peer = this.dependencies.createPeer();
    const channel = peer.createDataChannel("oai-events");
    let providerSession: JarvisSession | null = null;
    try {
      for (const track of localStream.getAudioTracks()) peer.addTrack(track, localStream);
      peer.ontrack = (event) => {
        if (operation !== this.operation) return;
        this.options.onRemoteStream?.(event.streams[0] ?? null);
      };
      channel.onmessage = (event) => {
        try {
          const parsed = JSON.parse(String(event.data));
          if (parsed && typeof parsed === "object") {
            this.options.onProviderEvent?.(parsed as Record<string, unknown>);
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

      const offer = await peer.createOffer();
      await peer.setLocalDescription(offer);
      await this.waitForIceGathering(peer);
      const sdpOffer = peer.localDescription?.sdp || offer.sdp;
      if (!sdpOffer) throw new Error("Browser did not produce an SDP offer");
      const response = await this.dependencies.createCall(
        this.options.controllerId,
        "controller",
        "openai",
        sdpOffer,
      );
      providerSession = response.session;
      if (!providerSession || !response.sdp_answer) {
        throw new Error("Backend did not return a complete Realtime answer");
      }
      await peer.setRemoteDescription({ type: "answer", sdp: response.sdp_answer });
      return {
        peer,
        channel,
        localStream,
        session: providerSession,
        rotateAfterSeconds: response.rotate_after_seconds ?? 55 * 60,
      };
    } catch (cause) {
      channel.close();
      peer.close();
      for (const track of localStream.getTracks()) track.stop();
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

  private async waitForIceGathering(peer: RTCPeerConnection): Promise<void> {
    if (
      peer.iceGatheringState === "complete"
      || typeof peer.addEventListener !== "function"
      || typeof peer.removeEventListener !== "function"
    ) return;
    await new Promise<void>((resolve) => {
      let timer = 0;
      const done = () => {
        peer.removeEventListener("icegatheringstatechange", onChange);
        if (timer) this.dependencies.clearTimer(timer);
        resolve();
      };
      const onChange = () => {
        if (peer.iceGatheringState === "complete") done();
      };
      peer.addEventListener("icegatheringstatechange", onChange);
      timer = this.dependencies.setTimer(done, 3_000);
    });
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
    connection.channel.close();
    connection.peer.ontrack = null;
    connection.peer.onconnectionstatechange = null;
    connection.peer.close();
    for (const track of connection.localStream.getTracks()) track.stop();
    if (notifyBackend) {
      await this.dependencies.closeSession(connection.session.id, connection.session.generation).catch(() => undefined);
    }
  }

  private publish(snapshot: JarvisRealtimeSnapshot): void {
    this.snapshot = snapshot;
    this.options.onSnapshot?.(snapshot);
  }
}
