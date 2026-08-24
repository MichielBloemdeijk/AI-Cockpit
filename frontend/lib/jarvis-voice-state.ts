export type VoicePhase = "stopped" | "armed" | "listening" | "transcribing" | "thinking" | "speaking" | "follow-up";

export interface VoiceMachineSnapshot {
  phase: VoicePhase;
  micEnabled: boolean;
  muted: boolean;
  cloudNextTurn: boolean;
  followUpUntil: number;
  awaitingAnswerRunId: string | null;
}

export type TranscriptRoute =
  | { kind: "ignored" }
  | { kind: "acknowledge"; text: "Yes?" }
  | { kind: "sleep" }
  | { kind: "answer"; runId: string; answer: string }
  | { kind: "command"; command: string; cloudNextTurn: boolean };

type TimerHandle = ReturnType<typeof setTimeout>;
type Listener = (snapshot: VoiceMachineSnapshot) => void;

export class JarvisVoiceStateMachine {
  private snapshot: VoiceMachineSnapshot = {
    phase: "stopped",
    micEnabled: false,
    muted: false,
    cloudNextTurn: false,
    followUpUntil: 0,
    awaitingAnswerRunId: null,
  };
  private readonly listeners = new Set<Listener>();
  private followUpTimer: TimerHandle | null = null;

  constructor(
    private readonly now: () => number = Date.now,
    private readonly schedule: (callback: () => void, delay: number) => TimerHandle = setTimeout,
    private readonly cancelSchedule: (handle: TimerHandle) => void = clearTimeout,
    private readonly followUpMs = 45_000,
  ) {}

  get current(): VoiceMachineSnapshot { return { ...this.snapshot }; }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    listener(this.current);
    return () => this.listeners.delete(listener);
  }

  setPhase(phase: VoicePhase): void { this.update({ phase }); }
  setMuted(muted: boolean): void { this.update({ muted }); }
  setCloudNextTurn(enabled: boolean): void { this.update({ cloudNextTurn: enabled }); }
  presentQuestion(runId: string): void { this.update({ awaitingAnswerRunId: runId || null }); }
  clearQuestion(runId?: string): void {
    if (!runId || this.snapshot.awaitingAnswerRunId === runId) this.update({ awaitingAnswerRunId: null });
  }

  beginTurn(): void {
    this.clearFollowUp();
    this.update({ phase: "thinking" });
  }

  setMicEnabled(enabled: boolean): void {
    if (!enabled) {
      this.clearFollowUp();
      this.update({ micEnabled: false, phase: "stopped" });
      return;
    }
    this.update({ micEnabled: true, phase: this.isFollowUpActive() ? "follow-up" : "armed" });
  }

  openFollowUp(): void {
    this.clearFollowUpTimer();
    const followUpUntil = this.now() + this.followUpMs;
    this.update({ followUpUntil, phase: this.snapshot.micEnabled ? "follow-up" : "stopped" });
    this.followUpTimer = this.schedule(() => {
      this.followUpTimer = null;
      if (this.snapshot.followUpUntil !== followUpUntil) return;
      this.update({ followUpUntil: 0, phase: this.snapshot.micEnabled ? "armed" : "stopped" });
    }, this.followUpMs);
  }

  routeTranscript(
    command: string,
    wakeWordMatch: boolean,
    followUpAuthorized = this.isFollowUpActive(),
  ): TranscriptRoute {
    const normalized = command.trim();
    // Authorization belongs to the speech segment that just ended.  The page
    // captures it when speech starts so an old follow-up timer cannot make a
    // second, overlapping segment wake Jarvis while the first is processing.
    const accepted = followUpAuthorized || wakeWordMatch;
    if (!accepted) {
      this.update({ phase: this.snapshot.micEnabled ? "armed" : "stopped" });
      return { kind: "ignored" };
    }
    if (!normalized) {
      this.openFollowUp();
      return { kind: "acknowledge", text: "Yes?" };
    }
    if (/^go to sleep[.!]?$/i.test(normalized)) {
      this.clearFollowUp();
      this.update({ phase: this.snapshot.micEnabled ? "armed" : "stopped" });
      return { kind: "sleep" };
    }
    // An accepted command consumes the current follow-up window.  A new one is
    // opened only after Jarvis has answered (or explicitly says “Yes?”).
    this.beginTurn();
    const pendingQuestionRunId = this.snapshot.awaitingAnswerRunId;
    const explicitAnswer = normalized.match(/^(?:answer|reply)(?:\s+(?:is|with|to))?\s*[:,-]?\s+(.+)$/i);
    if (pendingQuestionRunId && (!wakeWordMatch || explicitAnswer)) {
      this.update({ awaitingAnswerRunId: null });
      return { kind: "answer", runId: pendingQuestionRunId, answer: explicitAnswer?.[1]?.trim() || normalized };
    }
    if (pendingQuestionRunId && wakeWordMatch) this.update({ awaitingAnswerRunId: null });
    const cloudNextTurn = this.snapshot.cloudNextTurn;
    if (cloudNextTurn) this.update({ cloudNextTurn: false });
    return { kind: "command", command: normalized, cloudNextTurn };
  }

  destroy(): void {
    this.clearFollowUpTimer();
    this.listeners.clear();
  }

  private isFollowUpActive(): boolean { return this.snapshot.followUpUntil > this.now(); }

  private clearFollowUp(): void {
    this.clearFollowUpTimer();
    if (this.snapshot.followUpUntil) this.update({ followUpUntil: 0 });
  }

  private clearFollowUpTimer(): void {
    if (this.followUpTimer !== null) this.cancelSchedule(this.followUpTimer);
    this.followUpTimer = null;
  }

  private update(patch: Partial<VoiceMachineSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    const next = this.current;
    this.listeners.forEach((listener) => listener(next));
  }
}
