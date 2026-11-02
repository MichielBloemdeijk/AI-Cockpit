// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { JarvisLiveEvent, JarvisStateResponse } from "../api/jarvis";

const api = vi.hoisted(() => ({
  getJarvisState: vi.fn(),
  getJarvisLogEvents: vi.fn(),
  getJarvisSessionEvents: vi.fn(),
  getJarvisLiveEvents: vi.fn(),
  getTaskEvents: vi.fn(),
  startFreshJarvisLog: vi.fn(),
  jarvisLiveUrl: (cursor: number) => `/api/jarvis/live?cursor=${cursor}`,
}));
vi.mock("../api/jarvis", () => api);
vi.mock("../api/tasks", () => ({ getTaskEvents: api.getTaskEvents }));

import { JarvisFeed } from "./feed";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly listeners = new Map<string, (event: MessageEvent<string>) => void>();
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(readonly url: string) { FakeEventSource.instances.push(this); }
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) { this.listeners.set(type, listener); }
  close() { this.closed = true; }
  emit(event: Partial<JarvisLiveEvent>) {
    this.listeners.get("jarvis")?.(new MessageEvent("jarvis", { data: JSON.stringify(event) }));
  }
}

function projection(messages: Array<{ id: string; role: "user" | "assistant"; content: string }> = [], cursor = 10): JarvisStateResponse {
  return {
    operational_day: "2026-11-02", state_revision: 1, situation_revision: 1, live_event_cursor: cursor,
    current_log: { id: "log-1", messages } as unknown as JarvisStateResponse["current_log"],
    tasks: [], pending_questions: [], pending_presentations: [], session: null,
    worker_health: { status: "online", worker_id: "w", heartbeat_at: null }, component_health: {},
    cursors: { conversation: 0, task: 0, session: 0 }, focused_task_id: null, recent_task_refs: [],
  };
}

function liveEvent(sequence: number, eventType: string, payload: Record<string, unknown> = {}): JarvisLiveEvent {
  return { id: `e${sequence}`, sequence, event_type: eventType, source_domain: "session", source_id: null,
    source_sequence: null, payload, created_at: "2026-11-02T00:00:00Z" };
}

const flush = () => vi.advanceTimersByTimeAsync(0);

describe("JarvisFeed", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    api.getJarvisState.mockResolvedValue(projection());
    api.getJarvisLogEvents.mockResolvedValue({ conversation_id: "log-1", latest_sequence: 2, events: [
      { id: "c1", sequence: 1, event_type: "conversation.user_message.accepted", created_at: "2026-11-02T00:00:00Z" },
      { id: "c2", sequence: 2, event_type: "conversation.assistant.message.completed", created_at: "2026-11-02T00:00:01Z" },
    ] });
    api.getTaskEvents.mockResolvedValue({ latest_sequence: 0, events: [] });
    api.getJarvisLiveEvents.mockResolvedValue({ latest_sequence: 10, events: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("replays each domain from its cursor and opens the durable stream at the projection cursor", async () => {
    const feed = new JarvisFeed();
    feed.start();
    await flush();
    expect(feed.current()).toMatchObject({ loading: false, error: null });
    expect(feed.current().streams.cursors.conversation).toBe(2);
    expect(FakeEventSource.instances[0].url).toBe("/api/jarvis/live?cursor=10");
    await vi.advanceTimersByTimeAsync(1500);
    expect(api.getJarvisLogEvents).toHaveBeenLastCalledWith("log-1", 2);
    feed.dispose();
  });

  it("delivers a durable event once, whether it arrives by stream or by catch-up", async () => {
    const delivered: number[] = [];
    const feed = new JarvisFeed({ onLiveEvent: (event) => delivered.push(event.sequence) });
    feed.start();
    await flush();
    FakeEventSource.instances[0].emit(liveEvent(11, "provider.user_transcript_delta", { item_id: "u1", delta: "Hello" }));
    api.getJarvisLiveEvents.mockResolvedValue({ latest_sequence: 12, events: [
      liveEvent(11, "provider.user_transcript_delta", { item_id: "u1", delta: "Hello" }),
      liveEvent(12, "provider.user_transcript_delta", { item_id: "u1", delta: " there" }),
    ] });
    await vi.advanceTimersByTimeAsync(1000);
    expect(delivered).toEqual([11, 12]);
    expect(feed.current().provisionalUser?.text).toBe("Hello there");
    feed.dispose();
  });

  it("shows provider speech provisionally and drops it once the durable message arrives", async () => {
    const feed = new JarvisFeed();
    feed.start();
    await flush();
    feed.observeProviderEvent({ type: "conversation.item.input_audio_transcription.delta", item_id: "u2", delta: "What is " });
    feed.observeProviderEvent({ type: "conversation.item.input_audio_transcription.completed", item_id: "u2", transcript: "What is the time?" });
    // The server's final copy of the same turn replaces it rather than duplicating it.
    api.getJarvisState.mockResolvedValue(projection([{ id: "m1", role: "user", content: "What is the time?" }]));
    FakeEventSource.instances[0].emit(liveEvent(11, "provider.user_transcript", { item_id: "u2", transcript: "What is the time?" }));
    expect(feed.current().provisionalUser).toMatchObject({ id: "u2", text: "What is the time?" });
    await flush();
    expect(feed.current().provisionalUser).toBeNull();
    expect(feed.current().state?.current_log?.messages).toHaveLength(1);
    feed.dispose();
  });

  it("stays silent when disposed while the first projection is loading", async () => {
    let resolve!: (value: JarvisStateResponse) => void;
    api.getJarvisState.mockReturnValue(new Promise((done) => { resolve = done; }));
    const feed = new JarvisFeed();
    const listener = vi.fn();
    feed.subscribe(listener);
    feed.start();
    feed.dispose();
    resolve(projection());
    await flush();
    expect(listener).not.toHaveBeenCalled();
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("reconnects the stream from the newest cursor after a network error", async () => {
    const states: string[] = [];
    const feed = new JarvisFeed({ onStreamState: (state) => states.push(state) });
    feed.start();
    await flush();
    const first = FakeEventSource.instances[0];
    first.emit(liveEvent(11, "task.progress"));
    first.onerror?.();
    expect(first.closed).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    expect(FakeEventSource.instances[1].url).toBe("/api/jarvis/live?cursor=11");
    expect(states).toEqual(["errored"]);
    feed.dispose();
    expect(FakeEventSource.instances[1].closed).toBe(true);
  });

  it("recovers from a failed refresh on the next poll", async () => {
    api.getJarvisState.mockRejectedValueOnce(new Error("Request timed out. Please try again."));
    const feed = new JarvisFeed();
    feed.start();
    await flush();
    expect(feed.current()).toMatchObject({ loading: false, error: "Request timed out. Please try again." });
    await vi.advanceTimersByTimeAsync(1500);
    expect(feed.current().error).toBeNull();
    feed.dispose();
  });
});
