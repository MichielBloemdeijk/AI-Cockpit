/**
 * Shared React hooks for the AI Cockpit frontend.
 */
"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { checkAuth } from "./api";
import type { ConversationSelection } from "./conversation-selection";
import { useConversationActions } from "./use-conversation-actions";
import { useConversationDetail } from "./use-conversation-detail";
import { useConversationList } from "./use-conversation-list";
import type { ConversationKindFilter } from "./use-conversation-list";
import { useConversationPolling } from "./use-conversation-polling";

export type { AgentStatus, ChatActionError, ChatMessage } from "./chat-state-types";

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
    agentStatus,
    loadingConversation,
    loadConversation,
    resetConversationDetail,
    setEvents,
    setMessages,
    setAgentStatus,
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
    setAgentStatus,
    setLoading,
    setCanStop,
  });

  return {
    messages,
    actionError,
    agentStatus,
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

