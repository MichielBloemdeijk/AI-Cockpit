"use client";

import { useCallback, useEffect, useState } from "react";

import { listConversations, type ConversationSummary } from "./api";

export type ConversationKindFilter = "all" | "standard" | "jarvis" | "archived";

function firstChatConversation(conversations: ConversationSummary[]): ConversationSummary | undefined {
  return conversations.find((conversation) => !conversation.is_current_jarvis);
}

function loadConversations(kindFilter: ConversationKindFilter) {
  const archivedOnly = kindFilter === "archived";
  return listConversations(archivedOnly, archivedOnly ? "all" : kindFilter, archivedOnly);
}

export function useConversationList(kindFilter: ConversationKindFilter) {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);

  const refreshConversations = useCallback(async (preferredConversationId?: string | null) => {
    const nextConversations = await loadConversations(kindFilter);
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
  }, [kindFilter]);

  useEffect(() => {
    let cancelled = false;
    void loadConversations(kindFilter).then((nextConversations) => {
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
  }, [kindFilter]);

  return {
    conversations,
    activeConversationId,
    setActiveConversationId,
    refreshConversations,
  };
}
