"use client";

import { useCallback, useEffect, useState, type MutableRefObject } from "react";

import {
  type ConversationArtifactView,
  type ConversationDetail,
  type ConversationEventView,
  getConversation,
  getConversationEvents,
} from "./api";
import {
  buildFallbackAgentStatus,
  findLatestAgentStatus,
  mergeLoadedMessages,
} from "./agent-stream-state";
import { mapConversationMessages } from "./chat-history";
import type { AgentStatus, ChatMessage } from "./chat-state-types";

interface UseConversationDetailOptions {
  activeConversationId: string | null;
  activeBranchKey: string;
  activeStreamRunIdRef: MutableRefObject<string | null>;
}

export function useConversationDetail({
  activeConversationId,
  activeBranchKey,
  activeStreamRunIdRef,
}: UseConversationDetailOptions) {
  const [activeConversation, setActiveConversation] = useState<ConversationDetail | null>(null);
  const [events, setEvents] = useState<ConversationEventView[]>([]);
  const [artifacts, setArtifacts] = useState<ConversationArtifactView[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [agentStatus, setAgentStatus] = useState<AgentStatus | null>(null);
  const [loadingConversation, setLoadingConversation] = useState(false);

  const resetConversationDetail = useCallback(() => {
    setActiveConversation(null);
    setEvents([]);
    setArtifacts([]);
    setMessages([]);
    setAgentStatus(null);
  }, []);

  const loadConversation = useCallback(async (conversationId: string, branchKey: string, options?: { background?: boolean }) => {
    if (!options?.background) {
      setLoadingConversation(true);
    }
    try {
      const [detail, eventData] = await Promise.all([
        getConversation(conversationId, branchKey),
        getConversationEvents(conversationId, branchKey),
      ]);
      setActiveConversation(detail);
      setEvents(eventData.events);
      setArtifacts(eventData.artifacts);
      setMessages((current) => mergeLoadedMessages(current, mapConversationMessages(detail, eventData.artifacts, eventData.events)));
      const runStatus = String(detail.latest_run_status ?? "").toLowerCase();
      const hasActiveStreamRun = Boolean(activeStreamRunIdRef.current);
      if (runStatus === "running") {
        setAgentStatus(findLatestAgentStatus(eventData.events) ?? buildFallbackAgentStatus(conversationId, activeStreamRunIdRef.current));
      } else if (runStatus === "paused" || runStatus === "failed") {
        setAgentStatus(findLatestAgentStatus(eventData.events));
      } else if (!hasActiveStreamRun) {
        setAgentStatus(null);
      }
    } finally {
      if (!options?.background) {
        setLoadingConversation(false);
      }
    }
  }, [activeStreamRunIdRef]);

  useEffect(() => {
    if (!activeConversationId) {
      resetConversationDetail();
      return;
    }
    loadConversation(activeConversationId, activeBranchKey).catch(() => {});
  }, [activeBranchKey, activeConversationId, loadConversation, resetConversationDetail]);

  return {
    activeConversation,
    artifacts,
    events,
    messages,
    agentStatus,
    loadingConversation,
    loadConversation,
    resetConversationDetail,
    setActiveConversation,
    setArtifacts,
    setEvents,
    setMessages,
    setAgentStatus,
  };
}