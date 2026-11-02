"use client";

import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";

import { type ConversationArtifactView, type ConversationDetail, type ConversationEventView, getConversation, getConversationEvents } from "../api/conversations";
import { buildCommittedConversationMessages } from "./feed";
import { mergeConversationFeed } from "./feed-state";
import type { ChatMessage } from "./state-types";
import { isSelected, type ConversationSelectionRef } from "./selection";

interface UseConversationDetailOptions {
  activeConversationId: string | null;
  activeBranchKey: string;
  activeStreamRunIdRef: MutableRefObject<string | null>;
  selectionRef: ConversationSelectionRef;
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
      const activeRunId = activeStreamRunIdRef.current ?? eventData.active_run?.run_id ?? detail.active_run?.run_id ?? null;
      const committedMessages = buildCommittedConversationMessages(eventData.timeline_items ?? []);
      setMessages((current) => (
        mergeConversationFeed(current, committedMessages, activeRunId)
      ));
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
    loadingConversation,
    loadConversation,
    resetConversationDetail,
    setActiveConversation,
    setArtifacts,
    setEvents,
    setMessages,
  };
}