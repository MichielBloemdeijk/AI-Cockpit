import type { VoiceMachineSnapshot } from "./jarvis-voice-state";

export interface AcceptedSpeechSegment {
  followUpAuthorized: boolean;
}

/** Serializes VAD segments into conversational turns. */
export class JarvisTurnGate {
  private processing = false;
  private segmentAccepted = false;
  private followUpAuthorized = false;

  beginSegment(state: VoiceMachineSnapshot, now = Date.now()): boolean {
    const safeBoundary = !this.processing && (state.phase === "armed" || state.phase === "follow-up");
    this.segmentAccepted = safeBoundary;
    this.followUpAuthorized = safeBoundary && state.phase === "follow-up" && state.followUpUntil > now;
    return safeBoundary;
  }

  beginProcessing(): AcceptedSpeechSegment | null {
    if (this.processing || !this.segmentAccepted) return null;
    this.processing = true;
    this.segmentAccepted = false;
    const accepted = { followUpAuthorized: this.followUpAuthorized };
    this.followUpAuthorized = false;
    return accepted;
  }

  finishProcessing(): void {
    this.processing = false;
    this.segmentAccepted = false;
    this.followUpAuthorized = false;
  }

  reset(): void {
    this.finishProcessing();
  }
}
