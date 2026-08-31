export interface ProvisionalTranscript {
  id: string;
  text: string;
}

export interface TranscriptUpdate {
  id: string;
  delta?: string;
  transcript?: string;
  replace?: boolean;
}

export function mergeProvisionalTranscript(
  current: ProvisionalTranscript | null,
  update: TranscriptUpdate,
): ProvisionalTranscript | null {
  const id = update.id || current?.id || "live-turn";
  if (update.transcript !== undefined) {
    if (!update.transcript) return null;
    if (
      !update.replace
      && current?.id === id
      && current.text.startsWith(update.transcript)
    ) return current;
    return { id, text: update.transcript };
  }

  const delta = update.delta ?? "";
  if (!delta) return current;
  return {
    id,
    text: `${current?.id === id ? current.text : ""}${delta}`,
  };
}
