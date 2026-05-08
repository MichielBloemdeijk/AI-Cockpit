"use client";

import { useEffect } from "react";

const ACTIVE_CONVERSATION_POLL_MS = 750;

interface UseConversationPollingOptions {
  activeConversationId: string | null;
  activeBranchKey: string;
  shouldPoll: boolean;
  refreshConversations: (preferredConversationId?: string | null) => Promise<void>;
  loadConversation: (conversationId: string, branchKey: string, options?: { background?: boolean }) => Promise<void>;
}

export function useConversationPolling({
  activeConversationId,
  activeBranchKey,
  shouldPoll,
  refreshConversations,
  loadConversation,
}: UseConversationPollingOptions) {
  useEffect(() => {
    if (!activeConversationId || !shouldPoll) {
      return;
    }

    let cancelled = false;
    const timeoutId = window.setTimeout(async () => {
      try {
        await refreshConversations(activeConversationId);
        if (cancelled) {
          return;
        }
        await loadConversation(activeConversationId, activeBranchKey, { background: true });
      } catch {
        // Keep polling best-effort while the run is active.
      }
    }, ACTIVE_CONVERSATION_POLL_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [activeBranchKey, activeConversationId, loadConversation, refreshConversations, shouldPoll]);
}