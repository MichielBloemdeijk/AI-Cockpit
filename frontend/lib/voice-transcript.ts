import type { ConversationEventView, ConversationMessageView } from "./api";

export type VoiceTranscriptRole = "user" | "assistant";

export interface VoiceTranscriptEntry {
  id: string;
  sourceEventId: string | null;
  role: VoiceTranscriptRole;
  content: string;
  createdAt: string;
  sequence?: number;
}

const ASSISTANT_EVENT_TYPES = new Set([
  "voice.acknowledgement",
  "conversation.assistant.message.completed",
  "agent.question.asked",
  "task.progress",
  "task.completed",
  "task.failed",
  "task.cancelled",
  "task.interrupted",
  "task.interruptions_summarized",
  "task.requires_input",
]);

function compactContent(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function compareEntries(left: VoiceTranscriptEntry, right: VoiceTranscriptEntry): number {
  if (left.sequence !== undefined && right.sequence !== undefined && left.sequence !== right.sequence) {
    return left.sequence - right.sequence;
  }
  const byTime = new Date(left.createdAt).getTime() - new Date(right.createdAt).getTime();
  return Number.isFinite(byTime) && byTime !== 0 ? byTime : 0;
}

export function voiceTranscriptFromMessages(messages: ConversationMessageView[]): VoiceTranscriptEntry[] {
  return messages
    .filter((message): message is ConversationMessageView & { role: VoiceTranscriptRole } =>
      (message.role === "user" || message.role === "assistant") && Boolean(compactContent(message.content)),
    )
    .map((message) => ({
      id: message.id,
      sourceEventId: message.source_event_id,
      role: message.role,
      content: message.content,
      createdAt: message.created_at,
    }))
    .sort(compareEntries);
}

export function voiceTranscriptFromEvent(event: ConversationEventView): VoiceTranscriptEntry | null {
  const payload = event.payload_json || {};
  const isUserMessage = event.event_type === "conversation.user_message.accepted";
  if (!isUserMessage && !ASSISTANT_EVENT_TYPES.has(event.event_type)) return null;
  // Background questions get their one user-visible projection from
  // task.requires_input. Interactive questions carry voice_delivery and are
  // persisted directly as agent.question.asked.
  if (event.event_type === "agent.question.asked" && (payload.voice_delivery as { speak?: boolean } | undefined)?.speak !== true) return null;
  const content = compactContent(payload.content)
    || compactContent(payload.summary)
    || compactContent(payload.question)
    || compactContent(payload.speech_text);
  if (!content) return null;
  return {
    id: `event:${event.id}`,
    sourceEventId: event.id,
    role: isUserMessage ? "user" : "assistant",
    content,
    createdAt: event.created_at,
    sequence: event.sequence,
  };
}

export function mergeVoiceTranscript(
  current: VoiceTranscriptEntry[],
  incoming: VoiceTranscriptEntry | VoiceTranscriptEntry[],
): VoiceTranscriptEntry[] {
  const next = Array.isArray(incoming) ? incoming : [incoming];
  const knownIds = new Set(current.map((entry) => entry.id));
  const knownSourceEvents = new Set(current.flatMap((entry) => entry.sourceEventId ? [entry.sourceEventId] : []));
  const additions = next.filter((entry) => {
    if (knownIds.has(entry.id) || (entry.sourceEventId && knownSourceEvents.has(entry.sourceEventId))) return false;
    knownIds.add(entry.id);
    if (entry.sourceEventId) knownSourceEvents.add(entry.sourceEventId);
    return true;
  });
  return additions.length ? [...current, ...additions].sort(compareEntries) : current;
}
