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
  buildCommittedConversationMessages,
  buildLiveConversationMessage,
} from "./conversation-feed";
import { mergeConversationFeed } from "./conversation-feed-state";
import {
  buildFallbackAgentStatus,
  findLatestAgentStatus,
} from "./agent-stream-state";
import type { AgentStatus, ChatMessage } from "./chat-state-types";

interface UseConversationDetailOptions {
  activeConversationId: string | null;
  activeBranchKey: string;
  activeStreamRunIdRef: MutableRefObject<string | null>;
}

function isLegacyActiveRunTransientMessage(message: ChatMessage, activeRunId: string): boolean {
  if (message.runId !== activeRunId) {
    return false;
  }
  return message.id.startsWith("event:") || message.id.startsWith("toolrow:");
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
    const shouldBackgroundLoad = Boolean(options?.background || activeStreamRunIdRef.current);
    if (!shouldBackgroundLoad) {
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
      const runStatus = String(detail.latest_run_status ?? "").toLowerCase();
      const activeRunId = activeStreamRunIdRef.current ?? eventData.active_run?.run_id ?? detail.active_run?.run_id ?? null;
      const hasActiveStreamRun = Boolean(activeRunId);
      const committedMessages = buildCommittedConversationMessages(
        detail,
        eventData.artifacts,
        eventData.events,
        eventData.timeline_items ?? [],
      );
      const stableCommittedMessages = runStatus === "running" && activeRunId && !(eventData.timeline_items?.length)
        ? committedMessages.filter((message) => !isLegacyActiveRunTransientMessage(message, activeRunId))
        : committedMessages;
      const loadedLiveMessage = buildLiveConversationMessage(eventData.active_run ?? detail.active_run ?? null);
      setMessages((current) => (
        mergeConversationFeed(current, stableCommittedMessages, loadedLiveMessage, activeRunId)
      ));
      if (runStatus === "running") {
        setAgentStatus(findLatestAgentStatus(eventData.events) ?? buildFallbackAgentStatus(conversationId, activeRunId));
      } else if (runStatus === "paused" || runStatus === "failed") {
        setAgentStatus(findLatestAgentStatus(eventData.events));
      } else if (!hasActiveStreamRun) {
        setAgentStatus(null);
      }
    } finally {
      if (!shouldBackgroundLoad) {
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