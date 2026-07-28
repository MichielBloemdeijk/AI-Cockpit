"use client";

import { useCallback, useEffect, useState } from "react";

import { listConversations, type ConversationSummary } from "./api";

export function useConversationList(showArchived: boolean) {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);

  const refreshConversations = useCallback(async (preferredConversationId?: string | null) => {
    const nextConversations = await listConversations(showArchived);
    setConversations(nextConversations);
    if (preferredConversationId) {
      setActiveConversationId(preferredConversationId);
      return;
    }
    setActiveConversationId((current) => current ?? nextConversations[0]?.id ?? null);
  }, [showArchived]);

  useEffect(() => {
    let cancelled = false;
    void listConversations(showArchived).then((nextConversations) => {
      if (cancelled) return;
      setConversations(nextConversations);
      setActiveConversationId((current) => current ?? nextConversations[0]?.id ?? null);
    }).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [showArchived]);

  return {
    conversations,
    activeConversationId,
    setActiveConversationId,
    refreshConversations,
  };
}
