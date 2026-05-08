/**
 * Shared React hooks for the AI Cockpit frontend.
 */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  archiveConversation,
  checkAuth,
  ConversationArtifactView,
  ConversationDetail,
  ConversationEventView,
  ConversationSessionMetadata,
  ConversationSummary,
  createConversation,
  createConversationMessage,
  executeConversationTool,
  getConversation,
  getConversationEvents,
  listConversations,
  resendConversationBranch,
  streamChat,
} from "./api";
import {
  buildAgentStatusFromEvent,
  buildAgentStatusFromStreamUpdate,
  buildFallbackAgentStatus,
  clearLiveAgentStreamMessages,
  findLatestAgentStatus,
  mergeAgentStatus,
  mergeLoadedMessages,
  mergeStreamEventMessages,
  upsertLiveAgentStreamMessages,
} from "./agent-stream-state";
import { mapConversationEventMessages, mapConversationMessages } from "./chat-history";
import type { AgentStatus, ChatMessage } from "./chat-state-types";
import { useConversationList } from "./use-conversation-list";
import { useConversationPolling } from "./use-conversation-polling";

export type { AgentStatus, ChatMessage } from "./chat-state-types";

// ─── useAuth ──────────────────────────────────────────────────────────────────

export function useAuth() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);

  useEffect(() => {
    checkAuth().then(setAuthenticated);
  }, []);

  return { authenticated, setAuthenticated };
}

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [agentStatus, setAgentStatus] = useState<AgentStatus | null>(null);
  const [activeBranchKey, setActiveBranchKey] = useState("main");
  const [showArchived, setShowArchived] = useState(false);
  const [activeConversation, setActiveConversation] = useState<ConversationDetail | null>(null);
  const [events, setEvents] = useState<ConversationEventView[]>([]);
  const [artifacts, setArtifacts] = useState<ConversationArtifactView[]>([]);
  const [loadingConversation, setLoadingConversation] = useState(false);
  const [loading, setLoading] = useState(false);
  const [canStop, setCanStop] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const optimisticMessageCounterRef = useRef(0);
  const streamedEventsRef = useRef<ConversationEventView[]>([]);
  const activeStreamRunIdRef = useRef<string | null>(null);
  const {
    conversations,
    activeConversationId,
    setActiveConversationId,
    refreshConversations,
  } = useConversationList(showArchived);

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
  }, []);

  const removeOptimisticMessage = useCallback((messageId: string) => {
    setMessages((prev) => prev.filter((message) => message.id !== messageId));
  }, []);

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
  }, []);

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
  }, []);

  const appendToMessage = useCallback((messageId: string, chunk: string) => {
    setMessages((prev) => prev.map((message) => (
      message.id === messageId
        ? { ...message, content: message.content + chunk, streaming: true }
        : message
    )));
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
      setActiveConversationId(conversationId);
    } finally {
      if (!options?.background) {
        setLoadingConversation(false);
      }
    }
  }, []);

  useEffect(() => {
    if (!activeConversationId) {
      setActiveConversation(null);
      setEvents([]);
      setArtifacts([]);
      setMessages([]);
      setAgentStatus(null);
      return;
    }
    loadConversation(activeConversationId, activeBranchKey).catch(() => {});
  }, [activeConversationId, activeBranchKey, loadConversation]);

  const activeConversationSummary = conversations.find((conversation) => conversation.id === activeConversationId) ?? null;
  const shouldPollActiveConversation = Boolean(activeConversationId)
    && (loading || canStop || activeConversationSummary?.latest_run_status === "running");

  useConversationPolling({
    activeConversationId,
    activeBranchKey,
    shouldPoll: shouldPollActiveConversation,
    refreshConversations,
    loadConversation,
  });

  const sendMessage = useCallback(
    async (content: string, sessionMetadata: ConversationSessionMetadata) => {
      if (loading) return;
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
    },
    [
      loading,
      appendOptimisticUserMessage,
      appendOptimisticAssistantMessage,
      appendLocalAssistantMessage,
      appendToMessage,
      activeConversation,
      activeConversationId,
      activeBranchKey,
      loadConversation,
      refreshConversations,
      removeOptimisticMessage,
    ]
  );

  const runTool = useCallback(
    async (
      payload: { tool: "workspace_search" | "python_execution"; value: string },
      sessionMetadata: ConversationSessionMetadata,
    ) => {
      if (loading) return;
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
      } finally {
        setLoading(false);
      }
    },
    [activeConversationId, activeBranchKey, loadConversation, loading, refreshConversations]
  );

  const archiveActiveConversation = useCallback(async () => {
    if (!activeConversationId) return;
    await archiveConversation(activeConversationId);
    setActiveConversationId(null);
    setActiveBranchKey("main");
    await refreshConversations();
  }, [activeConversationId, refreshConversations]);

  const resendFromMessage = useCallback(async (sourceMessageId: string, content: string) => {
    if (!activeConversationId) return;
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
    } finally {
      setLoading(false);
    }
  }, [activeConversationId, activeBranchKey, loadConversation, refreshConversations]);

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
    setCanStop(false);
    setLoading(false);
    setAgentStatus(null);
    setMessages((prev) =>
      prev.map((m) => (m.streaming ? { ...m, streaming: false } : m))
    );
  }, []);

  const clearMessages = useCallback(() => {
    abortRef.current?.abort();
    setCanStop(false);
    setLoading(false);
    setActiveConversationId(null);
    setActiveBranchKey("main");
    setActiveConversation(null);
    setEvents([]);
    setArtifacts([]);
    setAgentStatus(null);
    setMessages([]);
  }, []);

  return {
    messages,
    agentStatus,
    conversations,
    activeConversationId,
    activeBranchKey,
    activeConversation,
    events,
    artifacts,
    showArchived,
    loading,
    canStop,
    loadingConversation,
    sendMessage,
    runTool,
    archiveActiveConversation,
    resendFromMessage,
    stopStreaming,
    clearMessages,
    selectConversation: (conversationId: string) => {
      setActiveConversationId(conversationId);
      setActiveBranchKey("main");
    },
    selectBranch: setActiveBranchKey,
    setShowArchived,
  };
}

