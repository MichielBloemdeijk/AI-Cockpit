/**
 * Shared React hooks for the AI Cockpit frontend.
 */
"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { checkAuth } from "./api/auth";
import type { ConversationSelection } from "./conversations/selection";
import { useConversationActions } from "./conversations/use-actions";
import { useConversationDetail } from "./conversations/use-detail";
import { useConversationList } from "./conversations/use-list";
import type { ConversationKindFilter } from "./conversations/use-list";
import { useConversationPolling } from "./conversations/use-polling";

export type { ChatActionError, ChatMessage } from "./conversations/state-types";

// ─── useAuth ──────────────────────────────────────────────────────────────────

export function useAuth() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);

  useEffect(() => {
    checkAuth().then(setAuthenticated);
  }, []);

  return { authenticated, setAuthenticated };
}

export function useChat() {
  const [activeBranchKey, setActiveBranchState] = useState("main");
  const [conversationKindFilter, setConversationKindFilter] = useState<ConversationKindFilter>("all");
  const [loading, setLoading] = useState(false);
  const [canStop, setCanStop] = useState(false);
  const activeStreamRunIdRef = useRef<string | null>(null);
  const {
    conversations,
    activeConversationId,
    setActiveConversationId: setActiveConversationState,
    refreshConversations,
  } = useConversationList(conversationKindFilter);
  // Mirrors the committed selection (before the load effects run), and moves
  // ahead of it the moment this hook changes the selection, so in-flight loads
  // and streams can tell they were superseded.
  const selectionRef = useRef<ConversationSelection>({ conversationId: activeConversationId, branchKey: activeBranchKey });
  useLayoutEffect(() => {
    selectionRef.current = { conversationId: activeConversationId, branchKey: activeBranchKey };
  }, [activeBranchKey, activeConversationId]);
  const setActiveConversationId = useCallback((conversationId: string | null) => {
    selectionRef.current = { ...selectionRef.current, conversationId };
    setActiveConversationState(conversationId);
  }, [setActiveConversationState]);
  const setActiveBranchKey = useCallback((branchKey: string) => {
    selectionRef.current = { ...selectionRef.current, branchKey };
    setActiveBranchState(branchKey);
  }, []);
  const {
    activeConversation,
    artifacts,
    events,
    messages,
    loadingConversation,
    loadConversation,
    resetConversationDetail,
    setEvents,
    setMessages,
  } = useConversationDetail({
    activeConversationId,
    activeBranchKey,
    activeStreamRunIdRef,
    selectionRef,
  });

  const activeConversationSummary = conversations.find((conversation) => conversation.id === activeConversationId) ?? null;
  const shouldPollActiveConversation = Boolean(activeConversationId)
    && !loading
    && !canStop
    && activeConversationSummary?.latest_run_status === "running";

  useConversationPolling({
    activeConversationId,
    activeBranchKey,
    shouldPoll: shouldPollActiveConversation,
    refreshConversations,
    loadConversation,
  });
  const {
    actionError,
    archiveActiveConversation,
    clearActionError,
    clearMessages,
    resendFromMessage,
    selectBranch,
    selectConversation,
    sendMessage,
    stopStreaming,
    unarchiveActiveConversation,
  } = useConversationActions({
    loading,
    activeConversation,
    activeConversationId,
    activeBranchKey,
    activeStreamRunIdRef,
    selectionRef,
    loadConversation,
    refreshConversations,
    resetConversationDetail,
    setActiveConversationId,
    setActiveBranchKey,
    setEvents,
    setMessages,
    setLoading,
    setCanStop,
  });

  return {
    messages,
    actionError,
    conversations,
    activeConversationId,
    activeBranchKey,
    activeConversation,
    events,
    artifacts,
    conversationKindFilter,
    loading,
    canStop,
    loadingConversation,
    sendMessage,
    archiveActiveConversation,
    clearActionError,
    resendFromMessage,
    stopStreaming,
    unarchiveActiveConversation,
    clearMessages,
    selectConversation,
    selectBranch,
    setConversationKindFilter,
  };
}

