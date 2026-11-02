"use client";

import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";

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
import { isSelected, type ConversationSelectionRef } from "./conversation-selection";

interface UseConversationDetailOptions {
  activeConversationId: string | null;
  activeBranchKey: string;
  activeStreamRunIdRef: MutableRefObject<string | null>;
  selectionRef: ConversationSelectionRef;
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
  selectionRef,
}: UseConversationDetailOptions) {
  const [activeConversation, setActiveConversation] = useState<ConversationDetail | null>(null);
  const [events, setEvents] = useState<ConversationEventView[]>([]);
  const [artifacts, setArtifacts] = useState<ConversationArtifactView[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [agentStatus, setAgentStatus] = useState<AgentStatus | null>(null);
  const [loadingConversation, setLoadingConversation] = useState(false);
  const loadGenerationRef = useRef(0);
  const foregroundLoadRef = useRef(0);

  const resetConversationDetail = useCallback(() => {
    loadGenerationRef.current += 1;
    foregroundLoadRef.current = 0;
    setLoadingConversation(false);
    setActiveConversation(null);
    setEvents([]);
    setArtifacts([]);
    setMessages([]);
    setAgentStatus(null);
  }, []);

  const loadConversation = useCallback(async (conversationId: string, branchKey: string, options?: { background?: boolean }) => {
    const shouldBackgroundLoad = Boolean(options?.background || activeStreamRunIdRef.current);
    // Only the newest load for the selected conversation and branch may write
    // the view; an older or redirected load finishes without effect.
    const generation = ++loadGenerationRef.current;
    const current = () => generation === loadGenerationRef.current && isSelected(selectionRef, conversationId, branchKey);
    if (!current()) {
      return;
    }
    if (!shouldBackgroundLoad) {
      foregroundLoadRef.current = generation;
      setLoadingConversation(true);
    }
    try {
      const [detail, eventData] = await Promise.all([
        getConversation(conversationId, branchKey),
        getConversationEvents(conversationId, branchKey),
      ]);
      if (!current()) {
        return;
      }
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
    } catch (error) {
      if (current()) {
        throw error;
      }
    } finally {
      if (foregroundLoadRef.current === generation) {
        foregroundLoadRef.current = 0;
        setLoadingConversation(false);
      }
    }
  }, [activeStreamRunIdRef, selectionRef]);

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