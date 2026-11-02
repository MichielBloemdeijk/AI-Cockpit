import type { ConversationSessionMetadata } from "../../lib/api/conversations";

export function formatConversationModeLabel(mode: ConversationSessionMetadata["mode"] | string | null | undefined): string {
  if (mode === "council") {
    return "Council";
  }
  if (mode === "single") {
    return "Chat";
  }
  return String(mode || "Chat");
}

export function formatConversationLabel(isoTimestamp: string): string {
  const date = new Date(isoTimestamp);
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

export function formatTraceLabel(eventType: string): string {
  return eventType
    .split(".")
    .map((part) => part.replace(/_/g, " "))
    .join(" / ");
}