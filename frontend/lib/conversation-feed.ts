import type { ActiveRunView, ConversationArtifactView, ConversationDetail, ConversationEventView, TimelineItemView } from "./api";
import { mapConversationMessages } from "./chat-history";
import type { ChatMessage } from "./chat-state-types";

function mapTimelineItemToChatMessage(item: TimelineItemView, id: string, streaming: boolean): ChatMessage {
  return {
    id,
    runId: item.run_id,
    step: item.step ?? undefined,
    role: item.role,
    kind: item.kind,
    label: item.label ?? undefined,
    title: item.title ?? undefined,
    content: item.content,
    createdAt: item.created_at,
    tone: item.tone ?? undefined,
    badges: item.badges,
    code: item.code ?? undefined,
    sections: item.sections,
    streaming,
  };
}

export function buildCommittedConversationMessages(
  detail: ConversationDetail,
  artifacts: ConversationArtifactView[],
  events: ConversationEventView[],
  timelineItems: TimelineItemView[],
): ChatMessage[] {
  if (!timelineItems.length) {
    return mapConversationMessages(detail, artifacts, events);
  }

  return timelineItems.map((item) => mapTimelineItemToChatMessage(item, item.item_id, false));
}

export function buildLiveConversationMessage(activeRun: ActiveRunView | null | undefined): ChatMessage | null {
  if (!activeRun?.live_item) {
    return null;
  }
  return mapTimelineItemToChatMessage(activeRun.live_item, `live:${activeRun.live_item.item_id}`, true);
}

export function buildTimelineMessageFromEvent(event: ConversationEventView): ChatMessage | null {
  if (event.event_type !== "chat.timeline.item.committed" || !event.payload_json) {
    return null;
  }

  return mapTimelineItemToChatMessage({
    item_id: String(event.payload_json.item_id ?? ""),
    run_id: event.run_id,
    sequence: event.sequence,
    role: (event.payload_json.role as TimelineItemView["role"]) ?? "assistant",
    kind: (event.payload_json.kind as TimelineItemView["kind"]) ?? "message",
    content: String(event.payload_json.content ?? ""),
    title: typeof event.payload_json.title === "string" ? event.payload_json.title : null,
    label: typeof event.payload_json.label === "string" ? event.payload_json.label : null,
    tone: (event.payload_json.tone as TimelineItemView["tone"]) ?? null,
    status: typeof event.payload_json.status === "string" ? event.payload_json.status : null,
    step: typeof event.payload_json.step === "number" ? event.payload_json.step : null,
    tool_name: typeof event.payload_json.tool_name === "string" ? event.payload_json.tool_name : null,
    badges: Array.isArray(event.payload_json.badges) ? event.payload_json.badges.filter((value): value is string => typeof value === "string") : [],
    code: typeof event.payload_json.code === "string" ? event.payload_json.code : null,
    sections: Array.isArray(event.payload_json.sections)
      ? event.payload_json.sections.filter((section): section is { title: string; items: string[] } => (
        Boolean(section)
        && typeof section === "object"
        && typeof (section as { title?: unknown }).title === "string"
        && Array.isArray((section as { items?: unknown }).items)
      ))
      : [],
    metadata: (event.payload_json.metadata as Record<string, unknown> | null | undefined) ?? null,
    created_at: event.created_at,
  }, String(event.payload_json.item_id ?? event.id), false);
}