export interface ProvisionalTranscript {
  id: string;
  text: string;
  afterMessageId: string | null;
}

export interface TranscriptUpdate {
  id: string;
  delta?: string;
  transcript?: string;
  replace?: boolean;
  afterMessageId?: string | null;
}

export interface DurableTranscriptMessage {
  id: string;
  role: string;
  content: string;
}

export function mergeProvisionalTranscript(
  current: ProvisionalTranscript | null,
  update: TranscriptUpdate,
): ProvisionalTranscript | null {
  const id = update.id || current?.id || "live-turn";
  const afterMessageId = current?.id === id
    ? current.afterMessageId
    : (update.afterMessageId ?? null);
  if (update.transcript !== undefined) {
    if (!update.transcript) return null;
    if (
      !update.replace
      && current?.id === id
      && current.text.startsWith(update.transcript)
    ) return current;
    return { id, text: update.transcript, afterMessageId };
  }

  const delta = update.delta ?? "";
  if (!delta) return current;
  return {
    id,
    text: `${current?.id === id ? current.text : ""}${delta}`,
    afterMessageId,
  };
}

function normalizedTranscript(value: string): string {
  return value.trim().replace(/\s+/g, " ");
}

export function provisionalTranscriptIsPersisted(
  messages: DurableTranscriptMessage[],
  provisional: ProvisionalTranscript | null,
  role: "user" | "assistant",
): boolean {
  if (!provisional?.text.trim()) return false;
  const anchorIndex = provisional.afterMessageId === null
    ? -1
    : messages.findIndex((message) => message.id === provisional.afterMessageId);
  if (provisional.afterMessageId !== null && anchorIndex < 0) return false;
  const expected = normalizedTranscript(provisional.text);
  return messages.slice(anchorIndex + 1).some((message) => (
    message.role === role && normalizedTranscript(message.content) === expected
  ));
}
