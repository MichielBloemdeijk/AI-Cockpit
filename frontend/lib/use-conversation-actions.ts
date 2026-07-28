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
import { buildTimelineMessageFromEvent } from "./conversation-feed";
import {
  buildAgentStatusFromEvent,
  buildAgentStatusFromStreamUpdate,
  buildFallbackAgentStatus,
  mergeAgentStatus,
} from "./agent-stream-state";
import {
  isLocalLiveMessage,
} from "./conversation-message-lifecycle";
import {
  appendCommittedTimelineMessage as appendCommittedTimelineMessageToFeed,
  upsertLocalLiveMessage as upsertLocalLiveMessageInFeed,
} from "./conversation-feed-state";
import { humanizeToolName } from "./agent-event-presenter";
import type { AgentStatus, ChatActionError, ChatMessage } from "./chat-state-types";

export interface ConversationToolPayload {
  tool: "workspace_search" | "python_execution";
  value: string;
}

type LiveAssistantContentSource = "none" | "agent_stream" | "chunk";

interface LiveAssistantUpdateDescriptor {
  mode: "append" | "replace";
  content: string;
  streaming: boolean;
  messagePatch: Partial<ChatMessage>;
}

function isLowSignalToolIntentSummary(content: string): boolean {
  return /^\s*use tool\s+[a-z0-9_:-]+\s*[.!]?\s*$/i.test(content);
}

function extractDisplayableAgentStreamContent(update: { kind: string; delta?: string; content?: string; tool?: string; ok?: boolean }): LiveAssistantUpdateDescriptor | null {
  switch (update.kind) {
    case "thought_delta": {
      const content = update.delta ?? "";
      return content
        ? {
          mode: "append",
          content,
          streaming: true,
          messagePatch: {
            kind: "thought",
            title: "Thinking…",
            label: "Thinking",
            tone: undefined,
          },
        }
        : null;
    }
    case "text_delta": {
      const content = update.delta ?? "";
      return content
        ? {
          mode: "append",
          content,
          streaming: true,
          messagePatch: {
            kind: "message",
            title: undefined,
            label: undefined,
            tone: undefined,
          },
        }
        : null;
    }
    case "summary_delta": {
      const content = update.delta ?? "";
      return content
        ? {
          mode: "append",
          content,
          streaming: true,
          messagePatch: {
            kind: "message",
            title: undefined,
            label: undefined,
            tone: undefined,
          },
        }
        : null;
    }
    case "thought_done": {
      const content = (update.content ?? "").trim();
      return content
        ? {
          mode: "replace",
          content,
          streaming: false,
          messagePatch: {
            kind: "thought",
            title: "Thought",
            label: "Thinking",
            tone: undefined,
          },
        }
        : null;
    }
    case "text_done":
    {
      const content = (update.content ?? "").trim();
      if (!content || isLowSignalToolIntentSummary(content)) {
        return null;
      }
      return {
        mode: "replace",
        content,
        streaming: false,
        messagePatch: {
          kind: "message",
          title: undefined,
          label: undefined,
          tone: undefined,
        },
      };
    }
    case "summary_done":
    case "progress": {
      const content = (update.content ?? "").trim();
      if (!content || isLowSignalToolIntentSummary(content)) {
        return null;
      }
      return {
        mode: "replace",
        content,
        streaming: update.kind === "progress",
        messagePatch: {
          kind: "status",
          title: undefined,
          label: undefined,
          tone: undefined,
        },
      };
    }
    case "tool_started":
    case "tool_progress":
    case "tool_done": {
      const content = (update.content ?? "").trim();
      if (!content || isLowSignalToolIntentSummary(content)) {
        return null;
      }
      const toolTitle = humanizeToolName(update.tool ?? "tool");
      const isDone = update.kind === "tool_done";
      return {
        mode: "replace",
        content,
        streaming: !isDone,
        messagePatch: {
          kind: isDone ? "tool_result" : "tool_call",
          title: toolTitle,
          label: isDone ? (update.ok === false ? "Tool failure" : "Tool result") : "Tool call",
          tone: update.ok === false ? "error" : "info",
        },
      };
    }
    default:
      return null;
  }
}

function buildAgentStreamMessageKey(update: { kind: string; run_id: string; step: number; tool?: string; activity_id?: string }): string | null {
  if (update.activity_id) {
    return update.activity_id;
  }
  switch (update.kind) {
    case "thought_delta":
    case "thought_done":
      return `thought:${update.run_id}:${update.step}`;
    case "text_delta":
    case "text_done":
      return `assistant:${update.run_id}:${update.step}:text`;
    case "summary_delta":
    case "summary_done":
      return `status:${update.run_id}:${update.step}`;
    case "tool_started":
    case "tool_progress":
    case "tool_done":
      return `tool:${update.run_id}:${update.step}:${update.tool ?? "tool"}`;
    case "progress":
      return `status:${update.run_id}:${update.step}`;
    default:
      return null;
  }
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
    setAgentStatus(buildFallbackAgentStatus(activeConversationId));
    resetLiveAssistantState();
    clearLocalLiveMessage();
    const optimisticMessageId = appendOptimisticUserMessage(content);

    const effectiveSessionMetadata = activeConversation?.session_metadata ?? sessionMetadata;
    let streamedConversationId: string | null = activeConversationId;
    let resolvedFinalStatus = false;
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
              streamedConversationId = data.conversation_id;
              activeStreamRunIdRef.current = data.run_id;
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

              const nextStatus = buildAgentStatusFromEvent(event);
              if (nextStatus) {
                setAgentStatus((current) => mergeAgentStatus(current, nextStatus));
              }

              const timelineMessage = buildTimelineMessageFromEvent(event);
              if (timelineMessage) {
                appendCommittedTimelineMessage(timelineMessage);
                if (timelineMessage.role !== "user" && event.run_id === activeStreamRunIdRef.current) {
                  resetLiveAssistantState();
                }
              }
            },
            onAgentStream: (update) => {
              const nextStatus = buildAgentStatusFromStreamUpdate(update);
              if (nextStatus) {
                setAgentStatus((current) => mergeAgentStatus(current, nextStatus));
              }
              if (!activeStreamRunIdRef.current || liveAssistantContentSourceRef.current === "chunk") {
                return;
              }
              const nextContent = extractDisplayableAgentStreamContent(update);
              const nextKey = buildAgentStreamMessageKey(update);
              if (!nextContent || !nextKey) {
                return;
              }
              const priorContent = liveAssistantMessageContentRef.current.get(nextKey) ?? "";
              const content = nextContent.mode === "append"
                ? `${priorContent}${nextContent.content}`
                : nextContent.content;
              liveAssistantMessageContentRef.current.set(nextKey, content);
              const nextLiveMessage: ChatMessage = {
                id: `live-local:${nextKey}`,
                activityId: nextKey,
                runId: activeStreamRunIdRef.current,
                step: update.step,
                role: "assistant",
                content,
                streaming: nextContent.streaming,
                ...nextContent.messagePatch,
              };
              upsertLocalLiveMessage(nextLiveMessage);
              liveAssistantContentSourceRef.current = "agent_stream";
            },
            onChunk: (chunk) => {
              if (activeStreamRunIdRef.current) {
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
        clearLocalLiveMessage();
        await refreshConversations(streamedConversationId).catch(() => {});
        await loadConversation(streamedConversationId, activeBranchKey, { background: true }).then(() => {
          resolvedFinalStatus = true;
        }).catch(() => {});
      } else {
        removeOptimisticMessage(optimisticMessageId);
        clearLocalLiveMessage();
      }
      setAgentStatus({
        title: "Run failed",
        content: errorMessage,
        tone: "error",
        active: false,
        runId: activeStreamRunIdRef.current,
      });
      setActionError({ scope: "send", message: errorMessage });
    } finally {
      abortRef.current = null;
      resetLiveAssistantState();
      setCanStop(false);
      setLoading(false);
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
    appendOptimisticUserMessage,
    activeConversation,
    activeStreamRunIdRef,
    activeBranchKey,
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
    resetLiveAssistantState();
    clearLocalLiveMessage();
  }, [clearLocalLiveMessage, resetLiveAssistantState, setAgentStatus, setCanStop, setLoading]);

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
