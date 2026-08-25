import { afterEach, describe, expect, it, vi } from "vitest";
import { JarvisSpeechScheduler, type SpeechSchedulerDependencies } from "./jarvis-speech-scheduler";
import { JarvisVoiceStateMachine } from "./jarvis-voice-state";
import { getVoiceDelivery, setVoiceDeliveryState } from "./voice-delivery";

const storage = new Map<string, string>();
const localStorageMock = { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => { storage.set(key, value); } };
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function setup(phase: "armed" | "listening" = "armed") {
  Object.assign(globalThis, { localStorage: localStorageMock, indexedDB: undefined });
  const machine = new JarvisVoiceStateMachine();
  machine.setMicEnabled(true);
  machine.setPhase(phase);
  const calls: string[] = [];
  let visible = true;
  const dependencies: SpeechSchedulerDependencies = {
    synthesize: vi.fn(async (text) => { calls.push(`synthesize:${text}`); return new Blob([text]); }),
    play: vi.fn(() => ({ finished: Promise.resolve(), stop: vi.fn() })),
    pauseVad: vi.fn(async () => { calls.push("pause"); }),
    resumeVad: vi.fn(async () => { calls.push("resume"); }),
    acknowledgeSpoken: vi.fn(async (id) => { calls.push(`ack:${id}`); }),
    isVisible: () => visible,
    onError: vi.fn(),
  };
  const scheduler = new JarvisSpeechScheduler(machine, dependencies);
  return { machine, scheduler, dependencies, calls, setVisible: (value: boolean) => { visible = value; } };
}

describe("Jarvis speech scheduler", () => {
  afterEach(() => storage.clear());

  it("pauses VAD before playback and resumes only after speech completes", async () => {
    const { scheduler, calls } = setup();
    await scheduler.enqueue({ eventId: "event-1", deliveryId: "delivery-1", text: "Hello there." });
    await flush();
    expect(calls).toEqual(["pause", "synthesize:Hello there.", "ack:delivery-1", "resume"]);
  });

  it("holds updates during listening and drains at the next safe boundary", async () => {
    const { machine, scheduler, dependencies } = setup("listening");
    await scheduler.enqueue({ eventId: "event-safe", text: "Task complete." });
    await flush();
    expect(dependencies.synthesize).not.toHaveBeenCalled();
    machine.setPhase("armed");
    scheduler.onStateChanged();
    await flush();
    expect(dependencies.synthesize).toHaveBeenCalledOnce();
  });

  it("deduplicates concurrent SSE and Push delivery", async () => {
    const { scheduler, dependencies } = setup();
    const item = { eventId: "event-race", deliveryId: "delivery-race", text: "One update." };
    const accepted = await Promise.all([scheduler.enqueue(item), scheduler.enqueue(item)]);
    await flush();
    expect(accepted.filter(Boolean)).toHaveLength(1);
    expect(dependencies.synthesize).toHaveBeenCalledOnce();
  });

  it("merges a later Push delivery ID into an SSE-claimed item for acknowledgement", async () => {
    const { machine, scheduler, dependencies } = setup("listening");
    await scheduler.enqueue({ eventId: "event-merge", text: "Merged update." });
    expect(await scheduler.enqueue({ eventId: "event-merge", deliveryId: "delivery-merge", text: "Merged update." })).toBe(false);
    scheduler.onStateChanged();
    // Transition after both sources have been processed, just as a safe VAD boundary does.
    machine.setPhase("armed");
    scheduler.onStateChanged();
    await flush();
    expect(dependencies.acknowledgeSpoken).toHaveBeenCalledWith("delivery-merge");
  });

  it("acknowledges a Push delivery ID that arrives after SSE speech finished", async () => {
    const { scheduler, dependencies } = setup();
    await scheduler.enqueue({ eventId: "event-late-push", text: "Already spoken." });
    await flush();

    expect(await scheduler.enqueue({ eventId: "event-late-push", deliveryId: "delivery-late", text: "Already spoken." })).toBe(false);

    expect(dependencies.synthesize).toHaveBeenCalledOnce();
    expect(dependencies.acknowledgeSpoken).toHaveBeenCalledWith("delivery-late");
  });

  it("does not speak while hidden and drains after visibility returns", async () => {
    const { scheduler, dependencies, setVisible } = setup();
    setVisible(false);
    await scheduler.enqueue({ eventId: "event-hidden", text: "Hidden update." });
    await flush();
    expect(dependencies.synthesize).not.toHaveBeenCalled();
    setVisible(true);
    scheduler.onStateChanged();
    await flush();
    expect(dependencies.synthesize).toHaveBeenCalledOnce();
  });

  it("does not replay audio that finished when the server acknowledgement fails", async () => {
    const { scheduler, dependencies } = setup();
    vi.mocked(dependencies.acknowledgeSpoken).mockRejectedValueOnce(new Error("offline"));
    await scheduler.enqueue({ eventId: "event-ack-failed", deliveryId: "delivery-ack-failed", text: "Already heard." });
    await flush();
    expect((await getVoiceDelivery("event-ack-failed"))?.state).toBe("spoken");
    expect(dependencies.onError).toHaveBeenCalledOnce();
    await scheduler.recover();
    await flush();
    expect(dependencies.synthesize).toHaveBeenCalledOnce();
  });

  it("waits for an explicit user gesture before speaking recovered audio", async () => {
    const first = setup("listening");
    await first.scheduler.enqueue({ eventId: "event-recovered", text: "Recovered update." });
    const second = setup("armed");

    await second.scheduler.recover();
    await flush();
    expect(second.dependencies.synthesize).not.toHaveBeenCalled();

    second.scheduler.retryPending();
    await flush();
    expect(second.dependencies.synthesize).toHaveBeenCalledOnce();
    expect((await getVoiceDelivery("event-recovered"))?.state).toBe("spoken");
  });

  it("collapses duplicate legacy restart speech during recovery", async () => {
    setup("listening");
    const restartText = "AI-Cockpit restarted before this task completed.";
    await setVoiceDeliveryState("restart-old", "pending", undefined, restartText);
    await new Promise((resolve) => setTimeout(resolve, 2));
    await setVoiceDeliveryState("restart-new", "pending", undefined, restartText);
    const second = setup("armed");

    await second.scheduler.recover();
    second.scheduler.retryPending();
    await flush();

    expect(second.dependencies.synthesize).toHaveBeenCalledOnce();
    expect((await getVoiceDelivery("restart-old"))?.state).toBe("dismissed");
    expect((await getVoiceDelivery("restart-new"))?.state).toBe("spoken");
  });

  it("collapses duplicate legacy restart events arriving from the live stream", async () => {
    const { machine, scheduler, dependencies } = setup("listening");
    const restartText = "AI-Cockpit restarted before this task completed.";

    expect(await scheduler.enqueue({ eventId: "restart-live-one", text: restartText })).toBe(true);
    expect(await scheduler.enqueue({ eventId: "restart-live-two", text: restartText })).toBe(false);
    machine.setPhase("armed");
    scheduler.onStateChanged();
    await flush();

    expect(dependencies.synthesize).toHaveBeenCalledOnce();
    expect((await getVoiceDelivery("restart-live-two"))?.state).toBe("dismissed");
  });

  it("does not collapse unrelated identical recovered task results", async () => {
    const first = setup("listening");
    await first.scheduler.enqueue({ eventId: "result-one", text: "Task complete." });
    await first.scheduler.enqueue({ eventId: "result-two", text: "Task complete." });
    const second = setup("armed");

    await second.scheduler.recover();
    second.scheduler.retryPending();
    await flush();
    await flush();

    expect(second.dependencies.synthesize).toHaveBeenCalledTimes(2);
  });

  it("activates answer routing when question playback starts, not while it is queued", async () => {
    const { machine, scheduler } = setup("listening");
    await scheduler.enqueue({ eventId: "event-question", text: "Which environment?", answerRunId: "run-question" });
    expect(machine.current.awaitingAnswerRunId).toBeNull();
    machine.setPhase("armed");
    scheduler.onStateChanged();
    await flush();
    expect(machine.current.awaitingAnswerRunId).toBe("run-question");
  });

  it("preserves question routing through barge-in and restarts VAD once", async () => {
    const { machine, scheduler, dependencies } = setup();
    let rejectPlayback: ((reason?: unknown) => void) | null = null;
    vi.mocked(dependencies.play).mockReturnValue({
      finished: new Promise<void>((_resolve, reject) => { rejectPlayback = reject; }),
      stop: () => rejectPlayback?.(new Error("Speech interrupted")),
    });
    await scheduler.enqueue({ eventId: "event-barge-question", text: "Which environment?", answerRunId: "run-barge" });
    await flush();
    expect(machine.current.awaitingAnswerRunId).toBe("run-barge");
    await scheduler.interrupt();
    await flush();
    expect(machine.current.awaitingAnswerRunId).toBe("run-barge");
    expect(dependencies.resumeVad).toHaveBeenCalledOnce();
  });

  it("keeps later durable updates queued when the user barges in", async () => {
    const { machine, scheduler, dependencies } = setup();
    let rejectPlayback: ((reason?: unknown) => void) | null = null;
    vi.mocked(dependencies.play)
      .mockReturnValueOnce({
        finished: new Promise<void>((_resolve, reject) => { rejectPlayback = reject; }),
        stop: () => rejectPlayback?.(new Error("Speech interrupted")),
      })
      .mockReturnValueOnce({ finished: Promise.resolve(), stop: vi.fn() });
    await scheduler.enqueue({ eventId: "event-interrupted", text: "First update." });
    await flush();
    await scheduler.enqueue({ eventId: "event-after-barge", text: "Second update." });

    await scheduler.interrupt();
    machine.openFollowUp();
    await flush();
    expect(dependencies.synthesize).toHaveBeenCalledTimes(1);
    expect((await getVoiceDelivery("event-after-barge"))?.state).toBe("queued");

    machine.setPhase("thinking");
    machine.setPhase("armed");
    scheduler.onStateChanged();
    await flush();
    expect(dependencies.synthesize).toHaveBeenCalledTimes(2);
    expect((await getVoiceDelivery("event-after-barge"))?.state).toBe("spoken");
  });

  it("retries transient playback failures only after a user-safe retry boundary", async () => {
    const { scheduler, dependencies } = setup();
    vi.mocked(dependencies.play)
      .mockReturnValueOnce({ finished: Promise.reject(new Error("autoplay blocked")), stop: vi.fn() })
      .mockReturnValueOnce({ finished: Promise.resolve(), stop: vi.fn() });
    await scheduler.enqueue({ eventId: "event-retry", text: "Try again." });
    await flush();
    expect(dependencies.synthesize).toHaveBeenCalledOnce();
    scheduler.onStateChanged();
    await flush();
    expect(dependencies.synthesize).toHaveBeenCalledOnce();
    scheduler.retryPending();
    await flush();
    expect(dependencies.synthesize).toHaveBeenCalledTimes(2);
    expect((await getVoiceDelivery("event-retry"))?.state).toBe("spoken");
  });

  it("dismisses queued clarification speech when that run is answered", async () => {
    const { machine, scheduler, dependencies } = setup("listening");
    await scheduler.enqueue({ eventId: "event-stale-question", text: "Which environment?", answerRunId: "run-stale" });

    await scheduler.dismissQuestion("run-stale");
    machine.setPhase("armed");
    scheduler.onStateChanged();
    await flush();

    expect(dependencies.synthesize).not.toHaveBeenCalled();
    expect((await getVoiceDelivery("event-stale-question"))?.state).toBe("dismissed");
    expect(machine.current.awaitingAnswerRunId).toBeNull();
  });

  it("stops an in-progress clarification when its task terminates", async () => {
    const { machine, scheduler, dependencies } = setup();
    let rejectPlayback: ((reason?: unknown) => void) | null = null;
    const stop = vi.fn(() => rejectPlayback?.(new Error("Question obsolete")));
    vi.mocked(dependencies.play).mockReturnValue({
      finished: new Promise<void>((_resolve, reject) => { rejectPlayback = reject; }),
      stop,
    });
    await scheduler.enqueue({ eventId: "event-current-question", text: "Which environment?", answerRunId: "run-ended" });
    await flush();

    await scheduler.dismissQuestion("run-ended");
    await flush();

    expect(stop).toHaveBeenCalledOnce();
    expect((await getVoiceDelivery("event-current-question"))?.state).toBe("dismissed");
    expect(machine.current.awaitingAnswerRunId).toBeNull();
  });

  it("returns unheard durable speech to pending when the page shuts down", async () => {
    const { scheduler, dependencies } = setup();
    let rejectPlayback: ((reason?: unknown) => void) | null = null;
    vi.mocked(dependencies.play).mockReturnValue({
      finished: new Promise<void>((_resolve, reject) => { rejectPlayback = reject; }),
      stop: () => rejectPlayback?.(new Error("Page closed")),
    });
    await scheduler.enqueue({ eventId: "event-page-close", text: "Recover me later." });
    await flush();

    await scheduler.shutdown();
    await flush();

    expect((await getVoiceDelivery("event-page-close"))?.state).toBe("pending");
    expect(dependencies.resumeVad).not.toHaveBeenCalled();
  });

  it("drains a boundary update instead of racing a direct VAD restart", async () => {
    const { machine, scheduler, dependencies, calls } = setup("listening");
    await scheduler.enqueue({ eventId: "event-boundary", text: "Background task done." });
    machine.setPhase("armed");

    await scheduler.resumeListening();

    expect(calls).toEqual(["pause", "synthesize:Background task done.", "resume"]);
    expect(dependencies.resumeVad).toHaveBeenCalledOnce();
  });

  it("does not recover clarification audio for a run no longer paused on the server", async () => {
    const first = setup("listening");
    await first.scheduler.enqueue({ eventId: "event-old-question", text: "Still need this?", answerRunId: "run-no-longer-paused" });
    const second = setup("armed");

    await second.scheduler.recover(new Set());
    await flush();

    expect(second.dependencies.synthesize).not.toHaveBeenCalled();
    expect((await getVoiceDelivery("event-old-question"))?.state).toBe("dismissed");
  });

  it("does not overwrite an unanswered clarification with a second run's question", async () => {
    const { machine, scheduler, dependencies } = setup("listening");
    await scheduler.enqueue({ eventId: "question-one", text: "First question?", answerRunId: "run-one" });
    await scheduler.enqueue({ eventId: "question-two", text: "Second question?", answerRunId: "run-two" });
    machine.setPhase("armed");
    scheduler.onStateChanged();
    await flush();

    expect(machine.current.awaitingAnswerRunId).toBe("run-one");
    expect(dependencies.synthesize).toHaveBeenCalledTimes(1);

    await scheduler.dismissQuestion("run-one");
    await flush();
    expect(machine.current.awaitingAnswerRunId).toBe("run-two");
    expect(dependencies.synthesize).toHaveBeenCalledTimes(2);
  });

  it("lets an urgent update preempt after the current sentence", async () => {
    const { scheduler, dependencies, calls } = setup();
    let finishFirstSentence: (() => void) | null = null;
    vi.mocked(dependencies.play)
      .mockReturnValueOnce({
        finished: new Promise<void>((resolve) => { finishFirstSentence = resolve; }),
        stop: vi.fn(),
      })
      .mockReturnValue({ finished: Promise.resolve(), stop: vi.fn() });
    await scheduler.enqueue({ eventId: "ordinary-multi", text: "First sentence. Second sentence." });
    await flush();
    await scheduler.enqueue({ eventId: "urgent-boundary", text: "Urgent update.", urgent: true });

    finishFirstSentence?.();
    await flush();
    await flush();

    expect(calls.filter((call) => call.startsWith("synthesize:"))).toEqual([
      "synthesize:First sentence.",
      "synthesize:Urgent update.",
      "synthesize:Second sentence.",
    ]);
    expect((await getVoiceDelivery("ordinary-multi"))?.state).toBe("spoken");
    expect((await getVoiceDelivery("ordinary-multi"))?.text).toBe("Second sentence.");
    expect((await getVoiceDelivery("urgent-boundary"))?.state).toBe("spoken");
  });
});
