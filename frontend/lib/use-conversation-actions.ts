"use client";

import { useCallback, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from "react";

import {
  archiveConversation,
  createConversation,
  createConversationMessage,
  executeConversationTool,
  resendConversationBranch,
  streamChat,
  type ConversationDetail,
  type ConversationEventView,
  type ConversationSessionMetadata,
} from "./api";
import {
  buildAgentStatusFromEvent,
  buildAgentStatusFromStreamUpdate,
  buildFallbackAgentStatus,
  clearLiveAgentStreamMessages,
  mergeAgentStatus,
  mergeStreamEventMessages,
  upsertLiveAgentStreamMessages,
} from "./agent-stream-state";
import { mapConversationEventMessages } from "./chat-history";
import type { AgentStatus, ChatActionError, ChatMessage } from "./chat-state-types";

export interface ConversationToolPayload {
  tool: "workspace_search" | "python_execution";
  value: string;
}

interface UseConversationActionsOptions {
  loading: boolean;
  activeConversation: ConversationDetail | null;
  activeConversationId: string | null;
  activeBranchKey: string;
  activeStreamRunIdRef: MutableRefObject<string | null>;
  loadConversation: (conversationId: string, branchKey: string, options?: { background?: boolean }) => Promise<void>;
  refreshConversations: (preferredConversationId?: string | null) => Promise<void>;
  resetConversationDetail: () => void;
  setActiveConversationId: (conversationId: string | null) => void;
  setActiveBranchKey: (branchKey: string) => void;
  setEvents: Dispatch<SetStateAction<ConversationEventView[]>>;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  setAgentStatus: Dispatch<SetStateAction<AgentStatus | null>>;
  setLoading: Dispatch<SetStateAction<boolean>>;
  setCanStop: Dispatch<SetStateAction<boolean>>;
}

export function useConversationActions({
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
}: UseConversationActionsOptions) {
  const abortRef = useRef<AbortController | null>(null);
  const optimisticMessageCounterRef = useRef(0);
  const streamedEventsRef = useRef<ConversationEventView[]>([]);
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
        streaming: true,
      },
    ]);
    return optimisticId;
  }, [setMessages]);

  const removeOptimisticMessage = useCallback((messageId: string) => {
    setMessages((prev) => prev.filter((message) => message.id !== messageId));
  }, [setMessages]);

  const appendOptimisticAssistantMessage = useCallback(() => {
    const optimisticId = `optimistic-assistant-${Date.now()}-${optimisticMessageCounterRef.current++}`;
    setMessages((prev) => [
      ...prev,
      {
        id: optimisticId,
        role: "assistant",
        content: "",
        streaming: true,
      },
    ]);
    return optimisticId;
  }, [setMessages]);

  const appendLocalAssistantMessage = useCallback((content: string) => {
    const localId = `local-assistant-${Date.now()}-${optimisticMessageCounterRef.current++}`;
    setMessages((prev) => [
      ...prev,
      {
        id: localId,
        role: "assistant",
        content,
      },
    ]);
  }, [setMessages]);

  const appendToMessage = useCallback((messageId: string, chunk: string) => {
    setMessages((prev) => prev.map((message) => (
      message.id === messageId
        ? { ...message, content: message.content + chunk, streaming: true }
        : message
    )));
  }, [setMessages]);

  const sendMessage = useCallback(async (content: string, sessionMetadata: ConversationSessionMetadata) => {
    if (loading) return;
    clearActionError();
    setLoading(true);
    setCanStop(false);
    setAgentStatus(buildFallbackAgentStatus(activeConversationId));
    setMessages((currentMessages) => clearLiveAgentStreamMessages(currentMessages));
    const optimisticMessageId = appendOptimisticUserMessage(content);

    const effectiveSessionMetadata = activeConversation?.session_metadata ?? sessionMetadata;
    let streamedConversationId: string | null = activeConversationId;
    let assistantMessageId: string | null = null;
    let resolvedFinalStatus = false;
    try {
      if (effectiveSessionMetadata.mode === "single") {
        streamedEventsRef.current = [];
        activeStreamRunIdRef.current = null;
        assistantMessageId = appendOptimisticAssistantMessage();
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
              streamedConversationId = data.conversation_id;
              activeStreamRunIdRef.current = data.run_id;
              streamedEventsRef.current = [];
              setAgentStatus(buildFallbackAgentStatus(data.conversation_id, data.run_id));
              setActiveConversationId(data.conversation_id);
              setActiveBranchKey("main");
            },
            onEvent: (event) => {
              setEvents((currentEvents) => {
                if (currentEvents.some((currentEvent) => currentEvent.id === event.id)) {
                  return currentEvents;
                }
                return [...currentEvents, event].sort((left, right) => left.sequence - right.sequence);
              });

              if (!activeStreamRunIdRef.current) {
                activeStreamRunIdRef.current = event.run_id;
              }

              const existingIndex = streamedEventsRef.current.findIndex((currentEvent) => currentEvent.id === event.id);
              if (existingIndex === -1) {
                streamedEventsRef.current = [...streamedEventsRef.current, event].sort((left, right) => left.sequence - right.sequence);
              } else {
                streamedEventsRef.current = streamedEventsRef.current.map((currentEvent, index) => index === existingIndex ? event : currentEvent);
              }

              const streamRunId = activeStreamRunIdRef.current ?? event.run_id;
              if (!streamRunId) {
                return;
              }

              const nextStatus = buildAgentStatusFromEvent(event);
              if (nextStatus) {
                setAgentStatus((current) => mergeAgentStatus(current, nextStatus));
              }

              const eventMessages = mapConversationEventMessages(streamedEventsRef.current)
                .filter((message) => message.runId === streamRunId);
              setMessages((currentMessages) => mergeStreamEventMessages(currentMessages, eventMessages, streamRunId, assistantMessageId));
            },
            onAgentStream: (update) => {
              const nextStatus = buildAgentStatusFromStreamUpdate(update);
              if (nextStatus) {
                setAgentStatus((current) => mergeAgentStatus(current, nextStatus));
              }
              setMessages((currentMessages) => upsertLiveAgentStreamMessages(currentMessages, update, assistantMessageId));
            },
            onChunk: (chunk) => {
              if (assistantMessageId) {
                appendToMessage(assistantMessageId, chunk);
              }
            },
            onDone: () => resolve(),
            onError: (err) => reject(new Error(err)),
          });
        });

        if (streamedConversationId) {
          activeStreamRunIdRef.current = null;
          await refreshConversations(streamedConversationId);
          await loadConversation(streamedConversationId, activeBranchKey, { background: true });
          resolvedFinalStatus = true;
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
      if (streamedConversationId) {
        activeStreamRunIdRef.current = null;
        await refreshConversations(streamedConversationId).catch(() => {});
        await loadConversation(streamedConversationId, activeBranchKey, { background: true }).then(() => {
          resolvedFinalStatus = true;
        }).catch(() => {});
      } else {
        removeOptimisticMessage(optimisticMessageId);
        if (assistantMessageId) {
          removeOptimisticMessage(assistantMessageId);
        }
      }
      setAgentStatus({
        title: "Run failed",
        content: errorMessage,
        tone: "error",
        active: false,
        runId: activeStreamRunIdRef.current,
      });
      setActionError({ scope: "send", message: errorMessage });
      appendLocalAssistantMessage(`Error: ${errorMessage}`);
    } finally {
      abortRef.current = null;
      setCanStop(false);
      setLoading(false);
      streamedEventsRef.current = [];
      activeStreamRunIdRef.current = null;
      if (!resolvedFinalStatus) {
        setAgentStatus(null);
      }
    }
  }, [
    loading,
    clearActionError,
    setLoading,
    setCanStop,
    setAgentStatus,
    activeConversationId,
    setMessages,
    appendOptimisticUserMessage,
    activeConversation,
    appendOptimisticAssistantMessage,
    activeStreamRunIdRef,
    activeBranchKey,
    setActiveConversationId,
    setActiveBranchKey,
    setEvents,
    appendToMessage,
    refreshConversations,
    loadConversation,
    removeOptimisticMessage,
    appendLocalAssistantMessage,
  ]);

  const runTool = useCallback(async (payload: ConversationToolPayload, sessionMetadata: ConversationSessionMetadata) => {
    if (loading) return;
    clearActionError();
    setLoading(true);
    setCanStop(false);
    try {
      let conversationId = activeConversationId;
      if (!conversationId) {
        const created = await createConversation(undefined, sessionMetadata.mode, sessionMetadata);
        conversationId = created.conversation.id;
        setActiveConversationId(conversationId);
        setActiveBranchKey("main");
      }

      await executeConversationTool(conversationId, {
        tool: payload.tool,
        branch_key: activeBranchKey,
        query: payload.tool === "workspace_search" ? payload.value : undefined,
        code: payload.tool === "python_execution" ? payload.value : undefined,
      });
      await refreshConversations(conversationId);
      await loadConversation(conversationId, activeBranchKey);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      setActionError({ scope: "tool", message: errorMessage });
    } finally {
      setLoading(false);
    }
  }, [activeConversationId, activeBranchKey, clearActionError, loadConversation, loading, refreshConversations, setActiveBranchKey, setActiveConversationId, setCanStop, setLoading]);

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
    setAgentStatus(null);
    setMessages((prev) =>
      prev.map((message) => (message.streaming ? { ...message, streaming: false } : message))
    );
  }, [setAgentStatus, setCanStop, setLoading, setMessages]);

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
    runTool,
    selectBranch,
    selectConversation,
    sendMessage,
    stopStreaming,
  };
}