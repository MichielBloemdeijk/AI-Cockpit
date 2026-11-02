import type { MutableRefObject } from "react";

/** The conversation and branch the chat view currently shows. */
export interface ConversationSelection {
  conversationId: string | null;
  branchKey: string;
}

/**
 * Updated synchronously on every selection change, so asynchronous work
 * (loads, stream callbacks) can check before writing shared view state that
 * the view still shows what it was started for.
 */
export type ConversationSelectionRef = MutableRefObject<ConversationSelection>;

export function isSelected(
  selection: ConversationSelectionRef,
  conversationId: string | null,
  branchKey: string,
): boolean {
  return selection.current.conversationId === conversationId && selection.current.branchKey === branchKey;
}
