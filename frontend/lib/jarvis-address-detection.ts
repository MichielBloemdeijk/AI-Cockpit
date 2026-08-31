export interface JarvisAddressEvidence {
  transcript: string;
  wakeScore: number;
  speechStartedMsBeforeWake: number;
  speechContinuedMsAfterWake: number;
  followUpWindowOpen: boolean;
}

export interface JarvisAddressDecision {
  accepted: boolean;
  confidence: number;
  reason: string;
}

const DIRECT_PATTERN = /^(?:hey\s+)?jarvis(?:[,.!?\s]|$)/i;
const INCIDENTAL_PATTERN = /\b(?:about|called|mention(?:ed|ing)?|said)\s+jarvis\b|\bjarvis\s+(?:movie|character|actor)\b/i;

/** Second-stage local direct-address gate used after the wake model fires. */
export function classifyJarvisAddress(evidence: JarvisAddressEvidence): JarvisAddressDecision {
  const transcript = evidence.transcript.trim();
  if (evidence.followUpWindowOpen && evidence.speechContinuedMsAfterWake >= 120) {
    return { accepted: true, confidence: 0.98, reason: "follow_up_window" };
  }
  if (INCIDENTAL_PATTERN.test(transcript)) {
    return { accepted: false, confidence: 0.97, reason: "incidental_mention" };
  }
  const direct = DIRECT_PATTERN.test(transcript);
  const timingScore = evidence.speechStartedMsBeforeWake <= 1200 && evidence.speechContinuedMsAfterWake >= 180 ? 0.18 : -0.12;
  const confidence = Math.max(0, Math.min(1, evidence.wakeScore * 0.62 + (direct ? 0.28 : 0) + timingScore));
  return {
    accepted: direct && confidence >= 0.72,
    confidence,
    reason: direct ? (confidence >= 0.72 ? "direct_address" : "low_confidence") : "wake_word_not_addressed",
  };
}
