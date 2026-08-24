import { claimVoiceDelivery, getVoiceDelivery, listRecoverableVoiceDeliveries, setVoiceDeliveryState } from "./voice-delivery";
import { JarvisVoiceStateMachine } from "./jarvis-voice-state";

export interface SpeechPlayback {
  finished: Promise<void>;
  stop: () => void;
}

export interface SpeechQueueItem {
  eventId: string;
  text: string;
  deliveryId?: string;
  urgent?: boolean;
  persist?: boolean;
  answerRunId?: string;
  retryCount?: number;
  retryAfterGesture?: boolean;
}

export interface SpeechSchedulerDependencies {
  synthesize: (text: string) => Promise<Blob>;
  play: (audio: Blob) => SpeechPlayback;
  pauseVad: () => Promise<void> | void;
  resumeVad: () => Promise<void> | void;
  acknowledgeSpoken: (deliveryId: string) => Promise<void>;
  isVisible: () => boolean;
  onError: (error: unknown) => void;
}

function splitSentences(text: string): string[] {
  const compact = text.replace(/[`*_>#]/g, "").replace(/\s+/g, " ").trim();
  return (compact.match(/[^.!?]+[.!?]+|[^.!?]+$/g) || []).map((part) => part.trim()).filter(Boolean);
}

export class JarvisSpeechScheduler {
  private queue: SpeechQueueItem[] = [];
  private draining = false;
  private current: SpeechQueueItem | null = null;
  private playback: SpeechPlayback | null = null;
  private stopDisposition: "dismissed" | "pending" | null = null;
  private shuttingDown = false;
  private suppressNextDrain = false;
  private transientCounter = 0;

  constructor(
    private readonly machine: JarvisVoiceStateMachine,
    private readonly dependencies: SpeechSchedulerDependencies,
  ) {}

  async enqueue(item: SpeechQueueItem): Promise<boolean> {
    const normalized = { ...item, text: item.text.trim(), persist: item.persist !== false };
    if (!normalized.text) return false;
    const existingItem = this.queue.find((candidate) => candidate.eventId === normalized.eventId) || (this.current?.eventId === normalized.eventId ? this.current : undefined);
    if (existingItem) {
      if (normalized.deliveryId && !existingItem.deliveryId) existingItem.deliveryId = normalized.deliveryId;
      if (normalized.answerRunId && !existingItem.answerRunId) existingItem.answerRunId = normalized.answerRunId;
      return false;
    }
    if (normalized.persist && !(await claimVoiceDelivery(normalized.eventId, normalized.deliveryId, normalized.text, normalized.urgent, normalized.answerRunId))) {
      if (normalized.deliveryId) {
        const queued = this.queue.find((candidate) => candidate.eventId === normalized.eventId);
        if (queued && !queued.deliveryId) queued.deliveryId = normalized.deliveryId;
        if (this.current?.eventId === normalized.eventId && !this.current.deliveryId) this.current.deliveryId = normalized.deliveryId;
        const existingDelivery = await getVoiceDelivery(normalized.eventId);
        if (existingDelivery?.state === "spoken") {
          try {
            await this.dependencies.acknowledgeSpoken(normalized.deliveryId);
          } catch (error) {
            this.dependencies.onError(error);
          }
        }
      }
      return false;
    }
    this.queue = normalized.urgent ? [normalized, ...this.queue] : [...this.queue, normalized];
    void this.drain();
    return true;
  }

  async speakTransient(text: string): Promise<void> {
    this.transientCounter += 1;
    await this.enqueue({ eventId: `transient-${this.transientCounter}`, text, persist: false, urgent: true });
  }

  async recover(validQuestionRunIds?: ReadonlySet<string>): Promise<void> {
    const records = await listRecoverableVoiceDeliveries();
    for (const record of records) {
      if (!record.text || this.queue.some((item) => item.eventId === record.eventId) || this.current?.eventId === record.eventId) continue;
      if (record.answerRunId && validQuestionRunIds && !validQuestionRunIds.has(record.answerRunId)) {
        await setVoiceDeliveryState(record.eventId, "dismissed", record.deliveryId);
        continue;
      }
      await setVoiceDeliveryState(record.eventId, "queued", record.deliveryId);
      // Recovered audio is never allowed to autoplay as soon as the page opens.
      // Mobile browsers require a gesture, and old updates speaking on load are
      // surprising even where autoplay happens to be permitted.
      const item: SpeechQueueItem = { eventId: record.eventId, deliveryId: record.deliveryId, text: record.text, urgent: record.urgent, answerRunId: record.answerRunId, persist: true, retryAfterGesture: true };
      this.queue = item.urgent ? [item, ...this.queue] : [...this.queue, item];
    }
    void this.drain();
  }

  activate(): void {
    this.shuttingDown = false;
  }

  onStateChanged(): void { void this.drain(); }

  retryPending(): void {
    this.queue.forEach((item) => { item.retryAfterGesture = false; });
    void this.drain();
  }

  async resumeListening(): Promise<void> {
    if (this.shuttingDown || !this.machine.current.micEnabled) return;
    const hasSpeakableUpdate = this.findNextIndex() >= 0;
    if (hasSpeakableUpdate && this.canDrain()) {
      await this.drain();
      return;
    }
    if (!this.draining) await this.dependencies.resumeVad();
  }

  async interrupt(): Promise<void> {
    if (!this.current) return;
    this.stopDisposition = "dismissed";
    this.suppressNextDrain = true;
    this.playback?.stop();
  }

  async dismissQuestion(runId: string): Promise<void> {
    if (!runId) return;
    this.machine.clearQuestion(runId);
    const discarded = this.queue.filter((item) => item.answerRunId === runId);
    this.queue = this.queue.filter((item) => item.answerRunId !== runId);
    await Promise.all(discarded.filter((item) => item.persist).map((item) => setVoiceDeliveryState(item.eventId, "dismissed", item.deliveryId)));
    if (this.current?.answerRunId === runId) {
      this.stopDisposition = "dismissed";
      this.playback?.stop();
    }
    void this.drain();
  }

  async shutdown(): Promise<void> {
    this.shuttingDown = true;
    this.stopDisposition = "pending";
    this.playback?.stop();
    const recoverable = this.queue.splice(0);
    await Promise.all(recoverable.filter((item) => item.persist).map((item) => setVoiceDeliveryState(item.eventId, "pending", item.deliveryId)));
    if (!this.draining) this.stopDisposition = null;
  }

  private canDrain(): boolean {
    const state = this.machine.current;
    return !this.draining
      && !this.shuttingDown
      && !state.muted
      && this.dependencies.isVisible()
      && !["listening", "transcribing", "thinking", "speaking"].includes(state.phase);
  }

  private isEligible(item: SpeechQueueItem): boolean {
    if (item.retryAfterGesture) return false;
    const activeQuestion = this.machine.current.awaitingAnswerRunId;
    return !item.answerRunId || !activeQuestion || item.answerRunId === activeQuestion;
  }

  private findNextIndex(urgentOnly = false): number {
    return this.queue.findIndex((candidate) => this.isEligible(candidate) && (!urgentOnly || candidate.urgent));
  }

  private async drain(): Promise<void> {
    if (!this.canDrain()) return;
    const nextIndex = this.findNextIndex();
    if (nextIndex < 0) return;
    const [item] = this.queue.splice(nextIndex, 1);
    if (!item) return;
    this.draining = true;
    this.current = item;
    this.stopDisposition = null;
    if (item.persist) await setVoiceDeliveryState(item.eventId, "playing", item.deliveryId);
    // Select the question before audio starts so a mic-tap barge-in can route
    // the user's immediate response back to the paused run.
    if (item.answerRunId) this.machine.presentQuestion(item.answerRunId);
    this.machine.setPhase("speaking");
    const micWasEnabled = this.machine.current.micEnabled;
    let yieldedToUrgent = false;
    try {
      if (micWasEnabled) await this.dependencies.pauseVad();
      const sentences = splitSentences(item.text);
      for (let index = 0; index < sentences.length; index += 1) {
        const sentence = sentences[index];
        const audio = await this.dependencies.synthesize(sentence);
        if (this.stopDisposition) throw new Error("Speech stopped");
        this.playback = this.dependencies.play(audio);
        await this.playback.finished;
        this.playback = null;
        if (index < sentences.length - 1 && this.findNextIndex(true) >= 0) {
          const continuation = { ...item, text: sentences.slice(index + 1).join(" ") };
          const eligibleUrgentIndexes = this.queue
            .map((candidate, queueIndex) => candidate.urgent && this.isEligible(candidate) ? queueIndex : -1)
            .filter((queueIndex) => queueIndex >= 0);
          const continuationIndex = Math.max(...eligibleUrgentIndexes) + 1;
          this.queue.splice(continuationIndex, 0, continuation);
          if (item.persist) await setVoiceDeliveryState(item.eventId, "queued", item.deliveryId, continuation.text);
          yieldedToUrgent = true;
          break;
        }
      }
      if (yieldedToUrgent) return;
      if (item.persist) {
        await setVoiceDeliveryState(item.eventId, "spoken", item.deliveryId);
        if (item.deliveryId) {
          try {
            await this.dependencies.acknowledgeSpoken(item.deliveryId);
          } catch (error) {
            // The audio has already played.  An acknowledgement transport
            // failure must not make the event speech-retryable and cause the
            // user to hear the same update twice.
            this.dependencies.onError(error);
          }
        }
      }
      this.machine.openFollowUp();
    } catch (error) {
      const disposition = this.stopDisposition;
      if (item.persist) await setVoiceDeliveryState(item.eventId, disposition || "pending", item.deliveryId);
      if (!disposition) {
        if ((item.retryCount || 0) < 3) this.queue = [{ ...item, retryCount: (item.retryCount || 0) + 1, retryAfterGesture: true }, ...this.queue];
        this.dependencies.onError(error);
      }
    } finally {
      this.playback = null;
      this.current = null;
      this.draining = false;
      this.stopDisposition = null;
      const stateBeforeBoundary = this.machine.current;
      const canContinueSpeech = !this.shuttingDown
        && !this.suppressNextDrain
        && !stateBeforeBoundary.muted
        && this.dependencies.isVisible()
        && this.findNextIndex() >= 0;
      if (micWasEnabled && !this.shuttingDown && !canContinueSpeech) await this.dependencies.resumeVad();
      const state = this.machine.current;
      if (state.phase === "speaking") this.machine.setPhase(state.micEnabled && !this.shuttingDown ? "armed" : "stopped");
      const suppressDrain = this.suppressNextDrain;
      this.suppressNextDrain = false;
      if (!this.shuttingDown && !suppressDrain) void this.drain();
    }
  }
}
