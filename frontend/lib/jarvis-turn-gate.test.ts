import { describe, expect, it } from "vitest";
import { JarvisTurnGate } from "./jarvis-turn-gate";
import type { VoiceMachineSnapshot } from "./jarvis-voice-state";

const snapshot = (phase: VoiceMachineSnapshot["phase"], followUpUntil = 0): VoiceMachineSnapshot => ({
  phase,
  followUpUntil,
  micEnabled: true,
  muted: false,
  cloudNextTurn: false,
  awaitingAnswerRunId: null,
});

describe("Jarvis turn gate", () => {
  it("allows only one VAD segment while a turn is processing", () => {
    const gate = new JarvisTurnGate();
    expect(gate.beginSegment(snapshot("armed"))).toBe(true);
    expect(gate.beginProcessing()).toEqual({ followUpAuthorized: false });
    expect(gate.beginSegment(snapshot("armed"))).toBe(false);
    expect(gate.beginProcessing()).toBeNull();

    gate.finishProcessing();
    expect(gate.beginSegment(snapshot("armed"))).toBe(true);
  });

  it("captures follow-up authorization when speech starts", () => {
    const gate = new JarvisTurnGate();
    expect(gate.beginSegment(snapshot("follow-up", 2_000), 1_000)).toBe(true);
    expect(gate.beginProcessing()).toEqual({ followUpAuthorized: true });
  });

  it("rejects speech outside armed and follow-up boundaries", () => {
    const gate = new JarvisTurnGate();
    for (const phase of ["listening", "transcribing", "thinking", "speaking", "stopped"] as const) {
      expect(gate.beginSegment(snapshot(phase))).toBe(false);
      expect(gate.beginProcessing()).toBeNull();
    }
  });
});
