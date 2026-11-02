"use client";

import { useCallback, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from "react";

import { archiveConversation, createConversation, createConversationMessage, resendConversationBranch, streamChat, unarchiveConversation, type ConversationDetail, type ConversationEventView, type ConversationSessionMetadata } from "../api/conversations";
import { buildTimelineMessageFromEvent } from "./feed";
import {
  isLocalLiveMessage,
} from "./message-lifecycle";
import {
  appendCommittedTimelineMessage as appendCommittedTimelineMessageToFeed,
  upsertLocalLiveMessage as upsertLocalLiveMessageInFeed,
} from "./feed-state";
import type { ChatActionError, ChatMessage } from "./state-types";
import { isSelected, type ConversationSelectionRef } from "./selection";

/** Whether the live assistant row has received streamed text yet. */
type LiveAssistantContentSource = "none" | "chunk";

interface UseConversationActionsOptions {
  loading: boolean;
  activeConversation: ConversationDetail | null;
  activeConversationId: string | null;
  activeBranchKey: string;
  activeStreamRunIdRef: MutableRefObject<string | null>;
  selectionRef: ConversationSelectionRef;
  loadConversation: (conversationId: string, branchKey: string, options?: { background?: boolean }) => Promise<void>;
  refreshConversations: (preferredConversationId?: string | null) => Promise<void>;
  resetConversationDetail: () => void;
  setActiveConversationId: (conversationId: string | null) => void;
  setActiveBranchKey: (branchKey: string) => void;
  setEvents: Dispatch<SetStateAction<ConversationEventView[]>>;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  setLoading: Dispatch<SetStateAction<boolean>>;
  setCanStop: Dispatch<SetStateAction<boolean>>;
}

export function useConversationActions({
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
}: UseConversationActionsOptions) {
  const abortRef = useRef<AbortController | null>(null);
  const optimisticMessageCounterRef = useRef(0);
  const liveAssistantContentSourceRef = useRef<LiveAssistantContentSource>("none");
  const liveAssistantMessageContentRef = useRef(new Map<string, string>());
  const [actionError, setActionError] = useState<ChatActionError | null>(null);

  const clearActionError = useCallback(() => {
    setActionError(null);
  }, []);

  const appendOptimisticUserMessage = useCallback((content: string) => {
    const optimisticId = `optimistic-user-${Date.now()}-${optimisticMessageCounterRef.current++}`;
    setMessages((prev) => [
      ...prev,
      {
        id: optimisticId,
        role: "user",
        content,
      },
    ]);
    return optimisticId;
  }, [setMessages]);

  const removeOptimisticMessage = useCallback((messageId: string) => {
    setMessages((prev) => prev.filter((message) => message.id !== messageId));
  }, [setMessages]);

  const clearLocalLiveMessage = useCallback(() => {
    liveAssistantMessageContentRef.current.clear();
    setMessages((prev) => prev.filter((message) => !isLocalLiveMessage(message)));
  }, [setMessages]);

  const upsertLocalLiveMessage = useCallback((message: ChatMessage) => {
    setMessages((prev) => upsertLocalLiveMessageInFeed(prev, message));
  }, [setMessages]);

  const appendCommittedTimelineMessage = useCallback((message: ChatMessage) => {
    setMessages((prev) => appendCommittedTimelineMessageToFeed(prev, message));
  }, [setMessages]);

  const resetLiveAssistantState = useCallback(() => {
    liveAssistantContentSourceRef.current = "none";
    liveAssistantMessageContentRef.current.clear();
  }, []);

  const sendMessage = useCallback(async (content: string, sessionMetadata: ConversationSessionMetadata) => {
    if (loading) return;
    clearActionError();
    setLoading(true);
    setCanStop(false);
    resetLiveAssistantState();
    clearLocalLiveMessage();
    const optimisticMessageId = appendOptimisticUserMessage(content);

    const effectiveSessionMetadata = activeConversation?.session_metadata ?? sessionMetadata;
    let streamedConversationId: string | null = activeConversationId;
    // The send writes the shared view only while the view still shows the
    // conversation and branch it was sent from (or, for a new conversation,
    // the one the stream created). Switching away leaves the stream running
    // in the background without touching the newly selected conversation.
    const sentBranchKey = activeBranchKey;
    const ownsView = () => isSelected(selectionRef, streamedConversationId, sentBranchKey);
    try {
      if (effectiveSessionMetadata.mode === "single") {
        activeStreamRunIdRef.current = null;
        resetLiveAssistantState();
        const controller = new AbortController();
        abortRef.current = controller;
        setCanStop(true);

        await new Promise<void>((resolve, reject) => {
          streamChat({
            messages: [{ role: "user", content }],
            conversationId: activeConversationId,
            branchKey: activeBranchKey,
            sessionMetadata: effectiveSessionMetadata,
            model: effectiveSessionMetadata.single_model,
            signal: controller.signal,
            onMetadata: (data) => {
              const owned = ownsView();
              streamedConversationId = data.conversation_id;
              if (!owned) return;
              activeStreamRunIdRef.current = data.run_id;
              setActiveConversationId(data.conversation_id);
            },
            onEvent: (event) => {
              if (!ownsView()) return;
              setEvents((currentEvents) => {
                if (currentEvents.some((currentEvent) => currentEvent.id === event.id)) {
                  return currentEvents;
                }
                return [...currentEvents, event].sort((left, right) => left.sequence - right.sequence);
              });

              if (!activeStreamRunIdRef.current) {
                activeStreamRunIdRef.current = event.run_id;
              }

              const timelineMessage = buildTimelineMessageFromEvent(event);
              if (timelineMessage) {
                appendCommittedTimelineMessage(timelineMessage);
                if (timelineMessage.role !== "user" && event.run_id === activeStreamRunIdRef.current) {
                  resetLiveAssistantState();
                }
              }
            },
            onChunk: (chunk) => {
              if (ownsView() && activeStreamRunIdRef.current) {
                const priorChunkContent = liveAssistantMessageContentRef.current.get("chunk") ?? "";
                const content = liveAssistantContentSourceRef.current === "chunk"
                  ? `${priorChunkContent}${chunk}`
                  : chunk;
                liveAssistantMessageContentRef.current.set("chunk", content);
                upsertLocalLiveMessage({
                  id: `live-local:${activeStreamRunIdRef.current}:chunk`,
                  activityId: `assistant:${activeStreamRunIdRef.current}:final`,
                  runId: activeStreamRunIdRef.current,
                  role: "assistant",
                  kind: "message",
                  content,
                  streaming: true,
                });
                liveAssistantContentSourceRef.current = "chunk";
              }
            },
            onDone: () => resolve(),
            onError: (err) => reject(new Error(err)),
          });
        });

        if (streamedConversationId) {
          activeStreamRunIdRef.current = null;
          await refreshConversations(ownsView() ? streamedConversationId : selectionRef.current.conversationId);
          await loadConversation(streamedConversationId, activeBranchKey, { background: true });
        }
        return;
      }

      if (!activeConversationId) {
        const created = await createConversation(undefined, effectiveSessionMetadata.mode, effectiveSessionMetadata, content);
        setActiveConversationId(created.conversation.id);
        setActiveBranchKey("main");
        await refreshConversations(created.conversation.id);
        await loadConversation(created.conversation.id, "main");
        return;
      }

      await createConversationMessage(activeConversationId, {
        content,
        branch_key: activeBranchKey,
        model: effectiveSessionMetadata.single_model,
      });
      await refreshConversations(activeConversationId);
      await loadConversation(activeConversationId, activeBranchKey);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      if (streamedConversationId && !ownsView()) {
        await refreshConversations(selectionRef.current.conversationId).catch(() => {});
      } else if (streamedConversationId) {
        activeStreamRunIdRef.current = null;
        clearLocalLiveMessage();
        await refreshConversations(streamedConversationId).catch(() => {});
        await loadConversation(streamedConversationId, activeBranchKey, { background: true }).catch(() => {});
      } else {
        removeOptimisticMessage(optimisticMessageId);
        clearLocalLiveMessage();
      }
      setActionError({ scope: "send", message: errorMessage });
    } finally {
      abortRef.current = null;
      resetLiveAssistantState();
      setCanStop(false);
      setLoading(false);
      activeStreamRunIdRef.current = null;
    }
  }, [
    loading,
    clearActionError,
    setLoading,
    setCanStop,
    activeConversationId,
    appendOptimisticUserMessage,
    activeConversation,
    activeStreamRunIdRef,
    activeBranchKey,
    selectionRef,
    setActiveConversationId,
    setActiveBranchKey,
    setEvents,
    refreshConversations,
    loadConversation,
    removeOptimisticMessage,
    clearLocalLiveMessage,
    appendCommittedTimelineMessage,
    resetLiveAssistantState,
    upsertLocalLiveMessage,
  ]);

  const archiveActiveConversation = useCallback(async () => {
    if (!activeConversationId) return;
    clearActionError();
    try {
      await archiveConversation(activeConversationId);
      setActiveConversationId(null);
      setActiveBranchKey("main");
      await refreshConversations();
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      setActionError({ scope: "archive", message: errorMessage });
    }
  }, [activeConversationId, clearActionError, refreshConversations, setActiveBranchKey, setActiveConversationId]);

  const unarchiveActiveConversation = useCallback(async () => {
    if (!activeConversationId) return;
    clearActionError();
    try {
      await unarchiveConversation(activeConversationId);
      await refreshConversations(activeConversationId);
      await loadConversation(activeConversationId, activeBranchKey);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      setActionError({ scope: "archive", message: errorMessage });
    }
  }, [activeBranchKey, activeConversationId, clearActionError, loadConversation, refreshConversations]);

  const resendFromMessage = useCallback(async (sourceMessageId: string, content: string) => {
    if (!activeConversationId) return;
    clearActionError();
    setLoading(true);
    try {
      const response = await resendConversationBranch(activeConversationId, {
        source_message_id: sourceMessageId,
        content,
        parent_branch_key: activeBranchKey,
      });
      setActiveBranchKey(response.branch.branch_key);
      await refreshConversations(activeConversationId);
      await loadConversation(activeConversationId, response.branch.branch_key);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      setActionError({ scope: "branch", message: errorMessage });
    } finally {
      setLoading(false);
    }
  }, [activeConversationId, activeBranchKey, clearActionError, loadConversation, refreshConversations, setActiveBranchKey, setLoading]);

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
    setCanStop(false);
    setLoading(false);
    resetLiveAssistantState();
    clearLocalLiveMessage();
  }, [clearLocalLiveMessage, resetLiveAssistantState, setCanStop, setLoading]);

  const clearMessages = useCallback(() => {
    abortRef.current?.abort();
    clearActionError();
    setCanStop(false);
    setLoading(false);
    setActiveConversationId(null);
    setActiveBranchKey("main");
    resetConversationDetail();
  }, [clearActionError, resetConversationDetail, setActiveBranchKey, setActiveConversationId, setCanStop, setLoading]);

  const selectConversation = useCallback((conversationId: string) => {
    setActiveConversationId(conversationId);
    setActiveBranchKey("main");
  }, [setActiveBranchKey, setActiveConversationId]);

  const selectBranch = useCallback((branchKey: string) => {
    setActiveBranchKey(branchKey);
  }, [setActiveBranchKey]);

  return {
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
  };
}
