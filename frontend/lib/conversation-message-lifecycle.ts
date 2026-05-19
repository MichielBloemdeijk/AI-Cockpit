import type { ChatMessage } from "./chat-state-types";

export function isOptimisticUserMessage(message: ChatMessage): boolean {
  return message.id.startsWith("optimistic-user-");
}

export function isLocalLiveMessage(message: ChatMessage): boolean {
  return message.id.startsWith("live-local:");
}

export function isPendingLocalMessage(message: ChatMessage): boolean {
  return message.id.startsWith("pending-local:");
}

export function areEquivalentMessages(left: ChatMessage, right: ChatMessage): boolean {
  return left.runId === right.runId
    && left.step === right.step
    && left.role === right.role
    && left.kind === right.kind
    && left.title === right.title
    && left.label === right.label
    && left.content === right.content;
}