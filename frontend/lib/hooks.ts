/**
 * Shared React hooks for the AI Cockpit frontend.
 */
"use client";

import { useEffect, useRef, useState } from "react";

import { checkAuth } from "./api";
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
  const [activeBranchKey, setActiveBranchKey] = useState("main");
  const [showArchived, setShowArchived] = useState(false);
  const [conversationKindFilter, setConversationKindFilter] = useState<ConversationKindFilter>("all");
  const [loading, setLoading] = useState(false);
  const [canStop, setCanStop] = useState(false);
  const activeStreamRunIdRef = useRef<string | null>(null);
  const {
    conversations,
    activeConversationId,
    setActiveConversationId,
    refreshConversations,
  } = useConversationList(showArchived, conversationKindFilter);
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
    runTool,
    selectBranch,
    selectConversation,
    sendMessage,
    stopStreaming,
  } = useConversationActions({
    loading,
    activeConversation,
    activeConversationId,
    activeBranchKey,
    activeStreamRunIdRef,
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
    showArchived,
    conversationKindFilter,
    loading,
    canStop,
    loadingConversation,
    sendMessage,
    runTool,
    archiveActiveConversation,
    clearActionError,
    resendFromMessage,
    stopStreaming,
    clearMessages,
    selectConversation,
    selectBranch,
    setShowArchived,
    setConversationKindFilter,
  };
}

