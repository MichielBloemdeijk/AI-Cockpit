import {
  areEquivalentMessages,
  isLocalLiveMessage,
  isOptimisticUserMessage,
  isPendingLocalMessage,
} from "./conversation-message-lifecycle";
import type { ChatMessage } from "./chat-state-types";

function shouldPreservePriorLiveMessage(current: ChatMessage, next: ChatMessage): boolean {
  if (!current.content.trim()) {
    return false;
  }
  const currentKind = current.kind ?? "message";
  const nextKind = next.kind ?? "message";
  return currentKind !== "tool_call"
    && currentKind !== "tool_result"
    && (nextKind === "tool_call" || nextKind === "tool_result");
}

export function upsertLocalLiveMessage(
  currentMessages: ChatMessage[],
  message: ChatMessage,
  options?: { preservePriorMessage?: boolean },
): ChatMessage[] {
  const currentLiveMessage = currentMessages.find((current) => isLocalLiveMessage(current)) ?? null;
  let next = currentMessages.filter((current) => !isLocalLiveMessage(current) && !(isPendingLocalMessage(current) && areEquivalentMessages(current, message)));
  if (options?.preservePriorMessage && currentLiveMessage && shouldPreservePriorLiveMessage(currentLiveMessage, message) && !next.some((current) => areEquivalentMessages(current, currentLiveMessage))) {
    next = [
      ...next,
      {
        ...currentLiveMessage,
        id: currentLiveMessage.id.replace(/^live-local:/, "pending-local:"),
        streaming: false,
      },
    ];
  }
  return [...next, message];
}

export function appendCommittedTimelineMessage(currentMessages: ChatMessage[], message: ChatMessage): ChatMessage[] {
  let next = currentMessages.filter((current) => {
    if (isPendingLocalMessage(current) && areEquivalentMessages(current, message)) {
      return false;
    }
    if (isLocalLiveMessage(current) && areEquivalentMessages(current, message)) {
      return false;
    }
    return true;
  });
  if (message.role === "user") {
    const optimisticMatchIndex = next.findIndex((current) => isOptimisticUserMessage(current) && current.content === message.content);
    if (optimisticMatchIndex >= 0) {
      next = next.filter((_, index) => index !== optimisticMatchIndex);
    }
  }
  if (next.some((current) => current.id === message.id)) {
    return next;
  }
  const firstLiveIndex = next.findIndex((current) => isLocalLiveMessage(current));
  if (firstLiveIndex < 0) {
    return [...next, message];
  }
  return [
    ...next.slice(0, firstLiveIndex),
    message,
    ...next.slice(firstLiveIndex),
  ];
}

export function mergeConversationFeed(
  currentMessages: ChatMessage[],
  committedMessages: ChatMessage[],
  loadedLiveMessage: ChatMessage | null,
  activeRunId: string | null,
): ChatMessage[] {
  const preservedCommittedMessages = activeRunId
    ? currentMessages.filter((message) => (
      !isOptimisticUserMessage(message)
      && !isLocalLiveMessage(message)
      && message.runId === activeRunId
      && !committedMessages.some((committedMessage) => committedMessage.id === message.id)
    ))
    : [];
  const optimisticUserMessages = currentMessages.filter((message) => isOptimisticUserMessage(message)).filter((message) => !committedMessages.some(
    (committedMessage) => committedMessage.role === "user" && committedMessage.content === message.content,
  ));
  const localLiveMessage = activeRunId
    ? currentMessages.find((message) => isLocalLiveMessage(message) && message.runId === activeRunId) ?? null
    : null;
  const nextMessages = [...committedMessages, ...preservedCommittedMessages, ...optimisticUserMessages];
  const effectiveLiveMessage = localLiveMessage ?? loadedLiveMessage;

  if (effectiveLiveMessage) {
    nextMessages.push(effectiveLiveMessage);
  }

  return nextMessages;
}