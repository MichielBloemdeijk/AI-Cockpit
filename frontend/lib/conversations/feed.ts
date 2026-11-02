import type { ConversationEventView, TimelineItemView } from "../api/conversations";
import type { ChatMessage } from "./state-types";
import { readUsageStats } from "../usage";

function mapTimelineItemToChatMessage(item: TimelineItemView, id: string): ChatMessage {
  const sourceTaskId = item.metadata?.source_task_id;
  return {
    id,
    activityId: item.item_id,
    runId: item.run_id,
    role: item.role,
    content: item.content,
    createdAt: item.created_at,
    streaming: false,
    usage: readUsageStats(item.usage),
    councilData: item.council ?? undefined,
    taskId: typeof sourceTaskId === "string" ? sourceTaskId : undefined,
  };
}

/** Committed messages come from the server's timeline: every message, chat or Jarvis, is an item. */
export function buildCommittedConversationMessages(timelineItems: TimelineItemView[]): ChatMessage[] {
  return timelineItems.map((item) => mapTimelineItemToChatMessage(item, item.item_id));
}

export function buildTimelineMessageFromEvent(event: ConversationEventView): ChatMessage | null {
  if (event.event_type !== "chat.timeline.item.committed" || !event.payload_json) {
    return null;
  }
  const payload = event.payload_json;
  return mapTimelineItemToChatMessage({
    item_id: String(payload.item_id ?? ""),
    run_id: event.run_id,
    sequence: event.sequence,
    role: (payload.role as TimelineItemView["role"]) ?? "assistant",
    kind: "message",
    content: String(payload.content ?? ""),
    label: typeof payload.label === "string" ? payload.label : null,
    metadata: (payload.metadata as Record<string, unknown> | null | undefined) ?? null,
    usage: readUsageStats(payload.usage) ?? null,
    council: (payload.council as TimelineItemView["council"] | undefined) ?? null,
    created_at: event.created_at,
  }, String(payload.item_id ?? event.id));
}
