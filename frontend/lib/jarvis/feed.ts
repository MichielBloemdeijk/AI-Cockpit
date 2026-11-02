/**
 * The Jarvis feed: what the page shows about today's conversation, tasks and session.
 *
 * It owns the state projection, the per-domain replay cursors, the durable live
 * event stream (SSE plus a catch-up poll, so a stalled proxy cannot hide events)
 * and the provisional transcripts shown while someone is speaking. Provisional
 * text comes from two sources, the browser's own provider events and the
 * server's durable events, and disappears once the persisted message arrives.
 */
import {
  getJarvisLiveEvents,
  getJarvisLogEvents,
  getJarvisSessionEvents,
  getJarvisState,
  jarvisLiveUrl,
  startFreshJarvisLog,
  type JarvisLiveEvent,
  type JarvisStateResponse,
} from "../api/jarvis";
import { getTaskEvents } from "../api/tasks";
import {
  EMPTY_JARVIS_LIVE_STREAMS,
  mergeJarvisDomainReplay,
  resetJarvisConversationStream,
  resetJarvisSessionStream,
  type JarvisLiveStreamState,
} from "./live-state";
import {
  mergeProvisionalTranscript,
  provisionalTranscriptIsPersisted,
  type ProvisionalTranscript,
} from "./transcript-state";
import { createUuid } from "../uuid";

export interface JarvisFeedSnapshot {
  state: JarvisStateResponse | null;
  streams: JarvisLiveStreamState;
  /** True until the first projection (or its failure) arrives. */
  loading: boolean;
  /** Why the latest refresh failed; cleared by the next successful one. */
  error: string | null;
  provisionalUser: ProvisionalTranscript | null;
  provisionalAssistant: ProvisionalTranscript | null;
}

export interface JarvisFeedOptions {
  /** Each durable live event once, in sequence order (the voice owner keeps its own bookkeeping). */
  onLiveEvent?: (event: JarvisLiveEvent) => void;
  onStreamState?: (state: "opened" | "errored") => void;
  refreshMs?: number;
  catchUpMs?: number;
  reconnectMs?: number;
}

const USER_DELTA_EVENTS = new Set(["conversation.item.input_audio_transcription.delta", "session.input_transcript.delta"]);
const ASSISTANT_DELTA_EVENTS = new Set(["response.output_audio_transcript.delta", "response.output_text.delta", "session.output_transcript.delta"]);
const ASSISTANT_DONE_EVENTS = new Set(["response.output_audio_transcript.done", "response.output_text.done"]);

export class JarvisFeed {
  private snapshot: JarvisFeedSnapshot = {
    state: null,
    streams: EMPTY_JARVIS_LIVE_STREAMS,
    loading: true,
    error: null,
    provisionalUser: null,
    provisionalAssistant: null,
  };
  private readonly listeners = new Set<(snapshot: JarvisFeedSnapshot) => void>();
  private readonly timers: number[] = [];
  private disposed = false;
  private started = false;
  private refreshInFlight = false;
  private logId: string | null = null;
  private sessionId: string | null = null;
  private liveCursor: number | null = null;
  private source: EventSource | null = null;
  private reconnect: number | null = null;
  private catchingUp = false;

  constructor(private readonly options: JarvisFeedOptions = {}) {}

  current(): JarvisFeedSnapshot {
    return this.snapshot;
  }

  subscribe(listener: (snapshot: JarvisFeedSnapshot) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Load the projection now and keep it current until `dispose()`. */
  start(): void {
    if (this.started || this.disposed) return;
    this.started = true;
    void this.refresh();
    this.timers.push(window.setInterval(() => void this.refresh(), this.options.refreshMs ?? 1500));
  }

  /** Reload the projection and replay each domain from its cursor. One refresh runs at a time. */
  async refresh(): Promise<void> {
    if (this.refreshInFlight || this.disposed) return;
    this.refreshInFlight = true;
    try {
      const projection = await getJarvisState();
      if (this.disposed) return;
      if (this.liveCursor === null) {
        this.liveCursor = Math.max(0, projection.live_event_cursor);
        this.connectLiveStream();
      }
      let streams = this.snapshot.streams;
      const logId = projection.current_log?.id ?? null;
      if (this.logId !== logId) {
        streams = resetJarvisConversationStream(streams);
        this.logId = logId;
      }
      const sessionId = projection.session?.id ?? null;
      if (this.sessionId !== sessionId) {
        streams = resetJarvisSessionStream(streams);
        this.sessionId = sessionId;
      }
      const [conversationReplay, taskReplay, sessionReplay] = await Promise.all([
        logId ? getJarvisLogEvents(logId, streams.cursors.conversation) : Promise.resolve(null),
        getTaskEvents(streams.cursors.task),
        projection.session ? getJarvisSessionEvents(projection.session.id, streams.cursors.session) : Promise.resolve(null),
      ]);
      if (this.disposed) return;
      if (conversationReplay) streams = mergeJarvisDomainReplay(streams, "conversation", conversationReplay.events);
      streams = mergeJarvisDomainReplay(streams, "task", taskReplay.events);
      if (sessionReplay) streams = mergeJarvisDomainReplay(streams, "session", sessionReplay.events);
      this.update({ state: projection, streams, error: null, loading: false });
    } catch (cause) {
      if (!this.disposed) this.update({ error: cause instanceof Error ? cause.message : "Unable to load Jarvis", loading: false });
    } finally {
      this.refreshInFlight = false;
    }
  }

  /** Start a new daily log; the conversation stream restarts from its beginning. */
  async freshLog(): Promise<void> {
    await startFreshJarvisLog(createUuid());
    if (!this.disposed) this.update({ streams: resetJarvisConversationStream(this.snapshot.streams) });
  }

  /** A raw provider event from the browser's own Live connection (transcripts only). */
  observeProviderEvent(event: Record<string, unknown>): void {
    const type = typeof event.type === "string" ? event.type : "";
    const afterMessageId = this.newestMessageId();
    if (USER_DELTA_EVENTS.has(type)) {
      this.update({ provisionalUser: mergeProvisionalTranscript(this.snapshot.provisionalUser, {
        id: String(event.item_id ?? "live-user"), delta: String(event.delta ?? ""), afterMessageId,
      }) });
    } else if (type === "conversation.item.input_audio_transcription.completed") {
      this.update({ provisionalUser: mergeProvisionalTranscript(this.snapshot.provisionalUser, {
        id: String(event.item_id ?? this.snapshot.provisionalUser?.id ?? "live-user"),
        transcript: String(event.transcript ?? ""), replace: true, afterMessageId,
      }) });
    } else if (ASSISTANT_DELTA_EVENTS.has(type)) {
      this.update({ provisionalAssistant: mergeProvisionalTranscript(this.snapshot.provisionalAssistant, {
        id: String(event.response_id ?? event.item_id ?? "assistant-live"), delta: String(event.delta ?? ""), afterMessageId,
      }) });
    } else if (ASSISTANT_DONE_EVENTS.has(type)) {
      this.update({ provisionalAssistant: mergeProvisionalTranscript(this.snapshot.provisionalAssistant, {
        id: String(event.response_id ?? event.item_id ?? this.snapshot.provisionalAssistant?.id ?? "assistant-live"),
        transcript: String(event.transcript ?? event.text ?? ""), replace: true, afterMessageId,
      }) });
    }
  }

  dispose(): void {
    this.disposed = true;
    this.timers.forEach((timer) => window.clearInterval(timer));
    if (this.reconnect !== null) window.clearTimeout(this.reconnect);
    this.source?.close();
    this.source = null;
    this.listeners.clear();
  }

  private connectLiveStream(): void {
    const connect = () => {
      if (this.disposed || this.liveCursor === null) return;
      const source = new EventSource(jarvisLiveUrl(this.liveCursor), { withCredentials: true });
      this.source = source;
      source.onopen = () => this.options.onStreamState?.("opened");
      source.addEventListener("jarvis", (raw) => {
        try {
          this.deliver(JSON.parse((raw as MessageEvent<string>).data) as JarvisLiveEvent);
        } catch {
          // Durable replay recovers malformed or incomplete stream frames.
        }
      });
      source.onerror = () => {
        this.options.onStreamState?.("errored");
        source.close();
        if (!this.disposed) this.reconnect = window.setTimeout(connect, this.options.reconnectMs ?? 1000);
      };
    };
    connect();
    this.catchUp();
    this.timers.push(window.setInterval(() => this.catchUp(), this.options.catchUpMs ?? 1000));
  }

  private catchUp(): void {
    if (this.disposed || this.catchingUp || this.liveCursor === null) return;
    this.catchingUp = true;
    void getJarvisLiveEvents(this.liveCursor)
      .then((response) => response.events.sort((left, right) => left.sequence - right.sequence).forEach((event) => this.deliver(event)))
      .catch(() => undefined)
      .finally(() => { this.catchingUp = false; });
  }

  private deliver(event: JarvisLiveEvent): void {
    if (this.disposed || this.liveCursor === null || event.sequence <= this.liveCursor) return;
    this.liveCursor = event.sequence;
    this.options.onLiveEvent?.(event);
    const afterMessageId = this.newestMessageId();
    const payload = event.payload ?? {};
    switch (event.event_type) {
      case "provider.user_transcript_delta":
        this.update({ provisionalUser: mergeProvisionalTranscript(this.snapshot.provisionalUser, {
          id: String(payload.item_id ?? "live-user"), delta: String(payload.delta ?? payload.transcript ?? ""), afterMessageId,
        }) });
        return;
      case "provider.user_transcript": {
        const itemId = String(payload.item_id ?? "live-user");
        this.update({ provisionalUser: mergeProvisionalTranscript(this.snapshot.provisionalUser, {
          id: itemId, transcript: String(payload.transcript ?? ""), replace: true, afterMessageId,
        }) });
        void this.refresh().finally(() => {
          if (this.snapshot.provisionalUser?.id === itemId) this.update({ provisionalUser: null });
        });
        return;
      }
      case "provider.assistant_transcript_delta":
        this.update({ provisionalAssistant: mergeProvisionalTranscript(this.snapshot.provisionalAssistant, {
          id: String(payload.response_id ?? "assistant-live"), transcript: String(payload.transcript ?? ""), afterMessageId,
        }) });
        return;
      case "provider.assistant_response":
      case "provider.assistant_interrupted": {
        const responseId = String(payload.response_id ?? "");
        void this.refresh().finally(() => {
          if (!responseId || this.snapshot.provisionalAssistant?.id === responseId) this.update({ provisionalAssistant: null });
        });
        return;
      }
    }
    if (event.source_domain !== "session" || event.event_type.startsWith("task.")) void this.refresh();
  }

  private newestMessageId(): string | null {
    return this.snapshot.state?.current_log?.messages.at(-1)?.id ?? null;
  }

  private update(patch: Partial<JarvisFeedSnapshot>): void {
    if (this.disposed) return;
    const next = { ...this.snapshot, ...patch };
    // A provisional line ends where the durable message it anticipated begins.
    const messages = next.state?.current_log?.messages ?? [];
    if (provisionalTranscriptIsPersisted(messages, next.provisionalUser, "user")) next.provisionalUser = null;
    if (provisionalTranscriptIsPersisted(messages, next.provisionalAssistant, "assistant")) next.provisionalAssistant = null;
    this.snapshot = next;
    this.listeners.forEach((listener) => listener(next));
  }
}
