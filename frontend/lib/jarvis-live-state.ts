import type { ConversationEventView, JarvisSessionEvent, TaskEvent } from "./api";

export type JarvisActivityDomain = "conversation" | "task" | "session";

export interface JarvisActivityItem {
  key: string;
  domain: JarvisActivityDomain;
  sequence: number;
  eventType: string;
  createdAt: string;
  payload: Record<string, unknown> | null;
}

export interface JarvisLiveStreamState {
  cursors: { conversation: number; task: number; session: number };
  activity: JarvisActivityItem[];
}

export const EMPTY_JARVIS_LIVE_STREAMS: JarvisLiveStreamState = {
  cursors: { conversation: 0, task: 0, session: 0 },
  activity: [],
};

type DomainEvent = ConversationEventView | TaskEvent | JarvisSessionEvent;

function eventPayload(event: DomainEvent): Record<string, unknown> | null {
  if ("payload_json" in event) return event.payload_json ?? null;
  return (event as TaskEvent | JarvisSessionEvent).payload ?? null;
}

export function mergeJarvisDomainReplay(
  state: JarvisLiveStreamState,
  domain: JarvisActivityDomain,
  events: DomainEvent[],
): JarvisLiveStreamState {
  const currentCursor = state.cursors[domain];
  const ordered = [...events]
    .filter((event) => event.sequence > currentCursor)
    .sort((left, right) => left.sequence - right.sequence);
  const seen = new Set(state.activity.map((item) => item.key));
  const appended: JarvisActivityItem[] = [];
  let cursor = currentCursor;
  for (const event of ordered) {
    const key = `${domain}:${event.id}`;
    cursor = Math.max(cursor, event.sequence);
    if (seen.has(key)) continue;
    seen.add(key);
    appended.push({
      key,
      domain,
      sequence: event.sequence,
      eventType: event.event_type,
      createdAt: event.created_at,
      payload: eventPayload(event),
    });
  }
  if (!appended.length && cursor === currentCursor) return state;
  const activity = [...state.activity, ...appended]
    .sort((left, right) => {
      const time = new Date(left.createdAt).getTime() - new Date(right.createdAt).getTime();
      if (time !== 0) return time;
      const domainOrder = left.domain.localeCompare(right.domain);
      return domainOrder !== 0 ? domainOrder : left.sequence - right.sequence;
    })
    .slice(-250);
  return {
    cursors: { ...state.cursors, [domain]: cursor },
    activity,
  };
}

export function resetJarvisConversationStream(state: JarvisLiveStreamState): JarvisLiveStreamState {
  return {
    cursors: { ...state.cursors, conversation: 0, session: 0 },
    activity: state.activity.filter((item) => item.domain === "task"),
  };
}

export function resetJarvisSessionStream(state: JarvisLiveStreamState): JarvisLiveStreamState {
  return {
    cursors: { ...state.cursors, session: 0 },
    activity: state.activity.filter((item) => item.domain !== "session"),
  };
}
