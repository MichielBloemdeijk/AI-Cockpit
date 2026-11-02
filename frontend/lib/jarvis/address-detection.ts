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
const RECALL_FIRST_WAKE_THRESHOLD = 0.18;

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
  if (direct) {
    return {
      accepted: true,
      confidence: Math.max(0.8, Math.min(1, evidence.wakeScore + 0.35)),
      reason: "direct_address",
    };
  }
  const plausibleTiming = evidence.speechStartedMsBeforeWake <= 1_500;
  const accepted = plausibleTiming && evidence.wakeScore >= RECALL_FIRST_WAKE_THRESHOLD;
  return {
    accepted,
    confidence: accepted ? Math.max(0.52, evidence.wakeScore) : evidence.wakeScore,
    reason: accepted ? "wake_model_candidate" : "wake_word_not_addressed",
  };
}
