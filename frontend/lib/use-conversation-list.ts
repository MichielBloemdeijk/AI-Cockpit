"use client";

import { useCallback, useEffect, useState } from "react";

import { listConversations, type ConversationSummary } from "./api";

export type ConversationKindFilter = "all" | "standard" | "jarvis";

function firstChatConversation(conversations: ConversationSummary[]): ConversationSummary | undefined {
  return conversations.find((conversation) => !conversation.is_current_jarvis);
}

export function useConversationList(showArchived: boolean, kindFilter: ConversationKindFilter) {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);

  const refreshConversations = useCallback(async (preferredConversationId?: string | null) => {
    const nextConversations = await listConversations(showArchived, kindFilter);
    setConversations(nextConversations);
    if (preferredConversationId) {
      setActiveConversationId(preferredConversationId);
      return;
    }
    setActiveConversationId((current) => {
      if (current && nextConversations.some((conversation) => conversation.id === current && !conversation.is_current_jarvis)) {
        return current;
      }
      return firstChatConversation(nextConversations)?.id ?? null;
    });
  }, [kindFilter, showArchived]);

  useEffect(() => {
    let cancelled = false;
    void listConversations(showArchived, kindFilter).then((nextConversations) => {
      if (cancelled) return;
      setConversations(nextConversations);
      setActiveConversationId((current) => {
        if (current && nextConversations.some((conversation) => conversation.id === current && !conversation.is_current_jarvis)) {
          return current;
        }
        return firstChatConversation(nextConversations)?.id ?? null;
      });
    }).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [kindFilter, showArchived]);

  return {
    conversations,
    activeConversationId,
    setActiveConversationId,
    refreshConversations,
  };
}
