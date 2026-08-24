import { describe, expect, it, vi } from "vitest";
import { JarvisVoiceStateMachine } from "./jarvis-voice-state";

describe("Jarvis voice state machine", () => {
  it("invokes browser timers with their required global receiver", () => {
    const originalSetTimeout = globalThis.setTimeout;
    const originalClearTimeout = globalThis.clearTimeout;
    let handle: ReturnType<typeof setTimeout> | undefined;
    const setTimeoutSpy = vi.fn(function (this: unknown, callback: () => void, delay?: number) {
      expect(this).toBe(globalThis);
      handle = originalSetTimeout.call(globalThis, callback, delay);
      return handle;
    });
    const clearTimeoutSpy = vi.fn(function (this: unknown, timer: ReturnType<typeof setTimeout>) {
      expect(this).toBe(globalThis);
      return originalClearTimeout.call(globalThis, timer);
    });
    vi.stubGlobal("setTimeout", setTimeoutSpy);
    vi.stubGlobal("clearTimeout", clearTimeoutSpy);
    try {
      const machine = new JarvisVoiceStateMachine();
      machine.setMicEnabled(true);
      expect(() => machine.openFollowUp()).not.toThrow();
      machine.destroy();
      expect(setTimeoutSpy).toHaveBeenCalledOnce();
      expect(clearTimeoutSpy).toHaveBeenCalledWith(handle);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("expires follow-up mode and requires the wake word again", () => {
    vi.useFakeTimers();
    const machine = new JarvisVoiceStateMachine(() => Date.now());
    machine.setMicEnabled(true);
    machine.openFollowUp();
    expect(machine.current.phase).toBe("follow-up");
    vi.advanceTimersByTime(45_000);
    expect(machine.current.phase).toBe("armed");
    expect(machine.routeTranscript("what time is it", false).kind).toBe("ignored");
    vi.useRealTimers();
  });

  it("routes a follow-up response to the question that was actually presented", () => {
    const machine = new JarvisVoiceStateMachine();
    machine.setMicEnabled(true);
    machine.presentQuestion("run-paused");
    machine.openFollowUp();
    expect(machine.routeTranscript("continue", false)).toEqual({
      kind: "answer",
      runId: "run-paused",
      answer: "continue",
    });
  });

  it("keeps a fresh wake-word request separate from a pending background question", () => {
    const machine = new JarvisVoiceStateMachine();
    machine.setMicEnabled(true);
    machine.presentQuestion("run-paused");
    machine.openFollowUp();
    expect(machine.routeTranscript("list my active tasks", true)).toEqual({
      kind: "command",
      command: "list my active tasks",
      cloudNextTurn: false,
    });
    expect(machine.current.awaitingAnswerRunId).toBeNull();
  });

  it("accepts an explicit wake-word answer to a presented question", () => {
    const machine = new JarvisVoiceStateMachine();
    machine.setMicEnabled(true);
    machine.presentQuestion("run-paused");
    expect(machine.routeTranscript("answer with production", true)).toEqual({
      kind: "answer",
      runId: "run-paused",
      answer: "production",
    });
  });

  it("returns a spoken acknowledgement for a wake-word-only segment", () => {
    const machine = new JarvisVoiceStateMachine();
    machine.setMicEnabled(true);
    expect(machine.routeTranscript("", true)).toEqual({ kind: "acknowledge", text: "Yes?" });
    expect(machine.current.phase).toBe("follow-up");
  });

  it("consumes the current cloud-next-turn value without a callback closure", () => {
    const machine = new JarvisVoiceStateMachine();
    machine.setMicEnabled(true);
    machine.setCloudNextTurn(true);
    expect(machine.routeTranscript("do this", true)).toEqual({ kind: "command", command: "do this", cloudNextTurn: true });
    expect(machine.current.cloudNextTurn).toBe(false);
    expect(machine.routeTranscript("do that", true)).toEqual({ kind: "command", command: "do that", cloudNextTurn: false });
  });

  it("uses authorization captured at speech start and consumes it for one turn", () => {
    const machine = new JarvisVoiceStateMachine();
    machine.setMicEnabled(true);
    machine.openFollowUp();

    expect(machine.routeTranscript("first request", false, true)).toMatchObject({ kind: "command" });
    expect(machine.current.followUpUntil).toBe(0);
    expect(machine.routeTranscript("ambient second segment", false, false)).toEqual({ kind: "ignored" });
  });

  it("does not let a live timer authorize a segment that began outside follow-up mode", () => {
    const machine = new JarvisVoiceStateMachine();
    machine.setMicEnabled(true);
    machine.openFollowUp();

    expect(machine.routeTranscript("ambient speech", false, false)).toEqual({ kind: "ignored" });
  });
});
