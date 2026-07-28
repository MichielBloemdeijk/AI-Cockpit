import {
  areEquivalentMessages,
  isLocalLiveMessage,
  isOptimisticUserMessage,
  isPendingLocalMessage,
} from "./conversation-message-lifecycle";
import type { ChatMessage } from "./chat-state-types";

export function upsertLocalLiveMessage(
  currentMessages: ChatMessage[],
  message: ChatMessage,
): ChatMessage[] {
  const existingIndex = currentMessages.findIndex((current) => (
    (Boolean(message.activityId) && current.activityId === message.activityId)
    || current.id === message.id
  ));
  if (existingIndex >= 0) {
    return currentMessages.map((current, index) => {
      if (index !== existingIndex) {
        return current;
      }
      if (!isLocalLiveMessage(current) && !message.streaming) {
        return current;
      }
      return message;
    });
  }
  return [
    ...currentMessages.filter((current) => !(isPendingLocalMessage(current) && areEquivalentMessages(current, message))),
    message,
  ];
}

export function appendCommittedTimelineMessage(currentMessages: ChatMessage[], message: ChatMessage): ChatMessage[] {
  const activityIndex = message.activityId
    ? currentMessages.findIndex((current) => current.activityId === message.activityId)
    : -1;
  if (activityIndex >= 0) {
    return currentMessages.map((current, index) => index === activityIndex ? message : current);
  }
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
  const localLiveMessages = activeRunId
    ? currentMessages.filter((message) => isLocalLiveMessage(message) && message.runId === activeRunId)
    : [];
  const nextMessages = [...committedMessages, ...preservedCommittedMessages, ...optimisticUserMessages];
  for (const liveMessage of localLiveMessages) {
    const existingIndex = liveMessage.activityId
      ? nextMessages.findIndex((message) => message.activityId === liveMessage.activityId)
      : nextMessages.findIndex((message) => message.id === liveMessage.id);
    if (existingIndex >= 0) {
      nextMessages[existingIndex] = liveMessage;
    } else {
      nextMessages.push(liveMessage);
    }
  }
  if (loadedLiveMessage && !nextMessages.some((message) => (
    loadedLiveMessage.activityId
      ? message.activityId === loadedLiveMessage.activityId
      : message.id === loadedLiveMessage.id
  ))) {
    nextMessages.push(loadedLiveMessage);
  }

  return nextMessages;
}
