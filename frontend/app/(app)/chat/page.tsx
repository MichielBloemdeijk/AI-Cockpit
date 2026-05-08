"use client";

import { useEffect, useRef, useState } from "react";
import { Archive, Clock3, FileStack, FolderOpen, GitBranch, History, Layers3, Loader2, MessageSquare, PanelLeftClose, PanelLeftOpen, Plus } from "lucide-react";
import clsx from "clsx";
import { AgentStatusCard } from "@/components/AgentStatusCard";
import { ChatMessage } from "@/components/ChatMessage";
import { ChatInput, type ChatComposerMode } from "@/components/ChatInput";
import { summarizeAgentEvent } from "@/lib/agent-event-presenter";
import { useChat, type ChatMessage as ChatTranscriptMessage } from "@/lib/hooks";
import { type ConversationEventView, ConversationSessionMetadata, getChatSettings } from "@/lib/api";
import { formatTokenCount, formatUsageCost, summarizeConversationUsage } from "@/lib/usage";

function buildNewConversationMetadata(defaults: ConversationSessionMetadata): ConversationSessionMetadata {
  return { ...defaults, mode: "single" };
}

function formatConversationModeLabel(mode: ConversationSessionMetadata["mode"] | string | null | undefined): string {
  if (mode === "council") {
    return "Council";
  }
  if (mode === "single" || mode === "agent") {
    return "Agent";
  }
  return String(mode || "Agent");
}

function formatConversationLabel(isoTimestamp: string): string {
  const date = new Date(isoTimestamp);
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function formatTraceLabel(eventType: string): string {
  return eventType
    .split(".")
    .map((part) => part.replace(/_/g, " "))
    .join(" / ");
}

const MUTATING_TOOLS = new Set(["file_write", "python_execution", "shell_command", "app_initialize"]);

function branchHasUnsafeMutations(events: ConversationEventView[]): boolean {
  return events.some((event) => {
    const payload = event.payload_json ?? {};
    const eventToolName = typeof payload.tool === "string"
      ? payload.tool
      : event.event_type.startsWith("tool.")
        ? event.event_type.split(".")[1] ?? ""
        : "";

    if (MUTATING_TOOLS.has(eventToolName)) {
      return true;
    }

    if (event.event_type === "agent.run.completed") {
      const runSummary = payload.run_summary;
      if (
        runSummary
        && typeof runSummary === "object"
        && Array.isArray((runSummary as { changed_files?: unknown }).changed_files)
        && ((runSummary as { changed_files?: unknown[] }).changed_files?.length ?? 0) > 0
      ) {
        return true;
      }
    }

    return false;
  });
}

export default function ChatPage() {
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [defaultSessionMetadata, setDefaultSessionMetadata] = useState<ConversationSessionMetadata | null>(null);
  const [draftSessionMetadata, setDraftSessionMetadata] = useState<ConversationSessionMetadata | null>(null);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editedContent, setEditedContent] = useState("");
  const [mobileDrawer, setMobileDrawer] = useState<"conversations" | "details" | null>(null);
  const [showDesktopDetails, setShowDesktopDetails] = useState(false);
  const {
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
    selectConversation,
    selectBranch,
    setShowArchived,
  } = useChat();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getChatSettings().then((response) => {
      setAvailableModels(response.available_models);
      setDefaultSessionMetadata(response.defaults);
      setDraftSessionMetadata(buildNewConversationMetadata(response.defaults));
      setSettingsLoaded(true);
    }).catch(() => setSettingsLoaded(true));
  }, []);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const activeSessionMetadata = activeConversation?.session_metadata ?? draftSessionMetadata;
  const councilMode = activeSessionMetadata?.mode === "council";
  const activeConversationSummary = conversations.find((conversation) => conversation.id === activeConversationId) ?? null;
  const activeConversationTitle = activeConversationSummary?.title || "Chat";
  const mobileHeaderBadge = councilMode && activeSessionMetadata
    ? `${activeSessionMetadata.council_models.length} models`
    : null;
  const usageSummary = summarizeConversationUsage(events, artifacts);
  const sessionCostLabel = formatUsageCost(usageSummary.totalUsage?.cost);
  const sessionTokenLabel = formatTokenCount(usageSummary.totalUsage?.total_tokens);
  const hasUsageSummary = usageSummary.requestCount > 0;
  const hasUnsafeBranchMutations = branchHasUnsafeMutations(events);
  const hasConversationDetails = Boolean(activeConversationId && (events.length > 0 || artifacts.length > 0 || activeConversation?.workspace?.files.length));
  const renderedMessages = messages.map((message) => {
    if (message.role !== "user" || (message.kind ?? "message") !== "message") {
      return message;
    }
    if (!hasUnsafeBranchMutations) {
      return message;
    }
    return {
      ...message,
      branchable: false,
    };
  });

  const handleSend = async (payload: { mode: ChatComposerMode; value: string }) => {
    if (!activeSessionMetadata) return;
    if (payload.mode === "chat" || activeSessionMetadata.mode === "council") {
      await sendMessage(payload.value, activeSessionMetadata);
      return;
    }
    await runTool(
      {
        tool: payload.mode,
        value: payload.value,
      },
      activeSessionMetadata,
    );
  };

  const handleNewConversation = () => {
    clearMessages();
    setDraftSessionMetadata((current) => {
      if (defaultSessionMetadata) {
        return buildNewConversationMetadata(defaultSessionMetadata);
      }
      return current ? { ...current, mode: "single" } : current;
    });
    setMobileDrawer(null);
  };

  const handleEditStart = (message: { id: string; content: string }) => {
    setEditingMessageId(message.id);
    setEditedContent(message.content);
  };

  const handleEditSubmit = async () => {
    if (!editingMessageId || !editedContent.trim()) return;
    await resendFromMessage(editingMessageId, editedContent.trim());
    setEditingMessageId(null);
    setEditedContent("");
  };

  const closeMobileDrawer = () => setMobileDrawer(null);

  return (
    <div className="flex h-full min-h-0">
      <aside className="hidden md:flex md:w-80 md:flex-col border-r border-zinc-800 bg-zinc-950/80 backdrop-blur-sm">
        <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
          <div className="flex items-center gap-2 text-zinc-200">
            <History size={16} className="text-zinc-500" />
            <span className="text-sm font-medium">Recent Conversations</span>
          </div>
          <button
            onClick={() => setShowArchived(!showArchived)}
            className="inline-flex items-center gap-1 rounded-lg border border-zinc-700 px-2.5 py-1.5 text-xs text-zinc-300 hover:border-zinc-500 hover:text-white transition-colors"
          >
            <Archive size={12} />
            {showArchived ? "Hide Archived" : "Show Archived"}
          </button>
        </div>

        <div className="px-4 pt-3">
          <button
            onClick={handleNewConversation}
            className="inline-flex items-center gap-1 rounded-lg border border-zinc-700 px-2.5 py-1.5 text-xs text-zinc-300 hover:border-zinc-500 hover:text-white transition-colors"
          >
            <Plus size={12} />
            New
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-2">
          {conversations.map((conversation) => (
            <button
              key={conversation.id}
              onClick={() => selectConversation(conversation.id)}
              className={clsx(
                "w-full rounded-2xl border px-3 py-3 text-left transition-colors",
                activeConversationId === conversation.id
                  ? "border-blue-500/40 bg-blue-500/10"
                  : "border-zinc-800 bg-zinc-900/80 hover:border-zinc-700 hover:bg-zinc-900"
              )}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="truncate text-sm font-medium text-zinc-100">
                  {conversation.title || "Untitled conversation"}
                </div>
                <div className="text-[11px] uppercase tracking-wide text-zinc-500">
                  {formatConversationModeLabel(conversation.mode_hint || conversation.session_metadata?.mode)}
                </div>
              </div>
              <div className="mt-2 line-clamp-2 text-xs text-zinc-400">
                {conversation.last_message_preview || "No messages yet."}
              </div>
              <div className="mt-3 text-[11px] text-zinc-500">
                {formatConversationLabel(conversation.updated_at)}
              </div>
              {conversation.archived_at && (
                <div className="mt-2 text-[11px] text-amber-400">Archived</div>
              )}
            </button>
          ))}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="border-b border-zinc-700 bg-zinc-900 flex-shrink-0">
          <div className="flex items-center justify-between gap-3 px-3 py-3 md:px-4">
            <div className="flex items-center gap-2 min-w-0">
              <button
                onClick={() => setMobileDrawer((current) => current === "conversations" ? null : "conversations")}
                className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-zinc-800 bg-zinc-950 text-zinc-300 md:hidden"
                aria-label="Open conversations"
              >
                {mobileDrawer === "conversations" ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
              </button>
              <MessageSquare size={18} className="hidden text-zinc-400 md:block" />
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-zinc-100 md:text-sm">
                {activeConversationTitle}
              </span>
              {mobileHeaderBadge && (
                <span className="shrink-0 whitespace-nowrap rounded-full border border-blue-600/30 bg-blue-600/20 px-2 py-0.5 text-[11px] text-blue-300 md:text-xs">
                  <span className="md:hidden">{mobileHeaderBadge}</span>
                  <span className="hidden md:inline">Council · {activeSessionMetadata?.council_models.length}</span>
                </span>
              )}
              {activeConversationId && hasUsageSummary && sessionCostLabel && (
                <span className="shrink-0 whitespace-nowrap rounded-full border border-emerald-600/30 bg-emerald-600/15 px-2 py-0.5 text-[11px] text-emerald-300 md:text-xs">
                  Session · {sessionCostLabel} credits
                </span>
              )}
              {agentStatus && (
                <span className="hidden shrink-0 items-center gap-1 whitespace-nowrap rounded-full border border-sky-500/30 bg-sky-500/10 px-2 py-0.5 text-[11px] text-sky-200 md:inline-flex">
                  {agentStatus.active && <Loader2 size={11} className="animate-spin" />}
                  {agentStatus.title}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleNewConversation}
                className="inline-flex items-center gap-1 rounded-xl border border-zinc-800 bg-zinc-950 px-2.5 py-2 text-xs text-zinc-300 md:hidden"
              >
                <Plus size={12} />
                New
              </button>
              {activeConversationId && (
                <button
                  onClick={() => setMobileDrawer((current) => current === "details" ? null : "details")}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-zinc-800 bg-zinc-950 text-zinc-300 md:hidden"
                  aria-label="Open chat details"
                >
                  <Layers3 size={16} />
                </button>
              )}
              {hasConversationDetails && (
                <button
                  onClick={() => setShowDesktopDetails((current) => !current)}
                  className="hidden items-center gap-1 rounded-lg border border-zinc-700 px-2.5 py-1.5 text-xs text-zinc-300 lg:inline-flex"
                >
                  <Layers3 size={12} />
                  {showDesktopDetails ? "Hide Details" : "Show Details"}
                </button>
              )}
              {activeSessionMetadata && (
                <div className="hidden lg:flex items-center gap-2 text-[11px] text-zinc-400">
                  <span className="rounded-full border border-zinc-700 px-2 py-1">{formatConversationModeLabel(activeSessionMetadata.mode)}</span>
                  <span className="rounded-full border border-zinc-700 px-2 py-1">{activeSessionMetadata.single_model}</span>
                  <span className="rounded-full border border-zinc-700 px-2 py-1">{activeBranchKey === "main" ? "Main branch" : activeBranchKey.slice(0, 8)}</span>
                </div>
              )}
              {activeConversationId && (
                <button
                  onClick={() => archiveActiveConversation()}
                  className="hidden items-center gap-1 rounded-lg border border-zinc-700 px-2.5 py-1.5 text-xs text-zinc-300 md:inline-flex"
                >
                  <Archive size={12} />
                  Archive
                </button>
              )}
            </div>
          </div>

        </div>

        {mobileDrawer && (
          <div className="absolute inset-0 z-30 bg-zinc-950/80 backdrop-blur-sm md:hidden">
            <div className="flex h-full flex-col bg-zinc-950">
              <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
                <div className="text-sm font-medium text-zinc-100">
                  {mobileDrawer === "conversations" ? "Conversations" : "Chat details"}
                </div>
                <button
                  onClick={closeMobileDrawer}
                  className="rounded-xl border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-300"
                >
                  Close
                </button>
              </div>

              {mobileDrawer === "conversations" ? (
                <div className="flex-1 overflow-y-auto p-3">
                  <button
                    onClick={handleNewConversation}
                    className="mb-3 inline-flex items-center gap-2 rounded-2xl border border-zinc-800 bg-zinc-900 px-4 py-3 text-sm text-zinc-100"
                  >
                    <Plus size={14} />
                    New conversation
                  </button>
                  <div className="space-y-2">
                    {conversations.map((conversation) => (
                      <button
                        key={conversation.id}
                        onClick={() => {
                          selectConversation(conversation.id);
                          closeMobileDrawer();
                        }}
                        className={clsx(
                          "w-full rounded-2xl border px-3 py-3 text-left transition-colors",
                          activeConversationId === conversation.id
                            ? "border-blue-500/40 bg-blue-500/10"
                            : "border-zinc-800 bg-zinc-900/80"
                        )}
                      >
                        <div className="truncate text-sm font-medium text-zinc-100">
                          {conversation.title || "Untitled conversation"}
                        </div>
                        <div className="mt-2 line-clamp-2 text-xs text-zinc-400">
                          {conversation.last_message_preview || "No messages yet."}
                        </div>
                        <div className="mt-3 text-[11px] text-zinc-500">
                          {formatConversationLabel(conversation.updated_at)}
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="flex-1 space-y-4 overflow-y-auto p-4">
                  {activeConversation && activeConversation.branches.length > 1 && (
                    <section className="rounded-2xl border border-zinc-800 bg-zinc-900/80 p-4">
                      <div className="flex items-center gap-2 text-sm font-medium text-zinc-100">
                        <GitBranch size={15} className="text-zinc-500" />
                        Branches
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <button
                          onClick={() => setShowArchived(!showArchived)}
                          className="rounded-full border border-zinc-700 bg-zinc-950 px-3 py-1 text-xs text-zinc-300"
                        >
                          {showArchived ? "Hide archived" : "Show archived"}
                        </button>
                        {activeConversationId && (
                          <button
                            onClick={() => {
                              archiveActiveConversation();
                              closeMobileDrawer();
                            }}
                            className="rounded-full border border-zinc-700 bg-zinc-950 px-3 py-1 text-xs text-zinc-300"
                          >
                            Archive chat
                          </button>
                        )}
                        {activeConversation.branches.map((branch) => (
                          <button
                            key={branch.branch_key}
                            onClick={() => {
                              selectBranch(branch.branch_key);
                              closeMobileDrawer();
                            }}
                            className={clsx(
                              "rounded-full border px-3 py-1 text-xs transition-colors",
                              activeBranchKey === branch.branch_key
                                ? "border-blue-500/40 bg-blue-500/10 text-blue-300"
                                : "border-zinc-700 bg-zinc-950 text-zinc-400"
                            )}
                          >
                            {branch.label || branch.branch_key}
                          </button>
                        ))}
                      </div>
                    </section>
                  )}

                  {activeConversationId && hasUsageSummary && (
                    <section className="rounded-2xl border border-zinc-800 bg-zinc-900/80 p-4">
                      <div className="flex items-center gap-2 text-sm font-medium text-zinc-100">
                        <Layers3 size={15} className="text-zinc-500" />
                        Usage
                      </div>
                      <div className="mt-3 grid grid-cols-2 gap-2">
                        <div className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2">
                          <div className="text-[11px] uppercase tracking-wide text-zinc-500">Session total</div>
                          <div className="mt-1 text-sm text-zinc-100">{sessionCostLabel ? `${sessionCostLabel} credits` : "Unavailable"}</div>
                        </div>
                        <div className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2">
                          <div className="text-[11px] uppercase tracking-wide text-zinc-500">Requests</div>
                          <div className="mt-1 text-sm text-zinc-100">{usageSummary.requestCount}</div>
                        </div>
                        <div className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2 col-span-2">
                          <div className="text-[11px] uppercase tracking-wide text-zinc-500">Tokens</div>
                          <div className="mt-1 text-sm text-zinc-100">{sessionTokenLabel ?? "Unavailable"}</div>
                        </div>
                      </div>
                    </section>
                  )}

                  {activeConversationId && (events.length > 0 || artifacts.length > 0) && (
                    <>
                      <section className="rounded-2xl border border-zinc-800 bg-zinc-900/80 p-4">
                        <div className="flex items-center gap-2 text-sm font-medium text-zinc-100">
                          <Clock3 size={15} className="text-zinc-500" />
                          Recent Trace
                        </div>
                        <div className="mt-3 space-y-2">
                          {events.slice(-6).reverse().map((event) => (
                            <div key={event.id} className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2">
                              <div className="flex items-center justify-between gap-3">
                                <div className="text-xs text-zinc-200">{formatTraceLabel(event.event_type)}</div>
                                <div className="text-[11px] text-zinc-500">{formatConversationLabel(event.created_at)}</div>
                              </div>
                              {summarizeAgentEvent(event.event_type, event.payload_json) && (
                                <div className="mt-1 line-clamp-2 text-xs text-zinc-500">
                                  {summarizeAgentEvent(event.event_type, event.payload_json)}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      </section>

                      <section className="rounded-2xl border border-zinc-800 bg-zinc-900/80 p-4">
                        <div className="flex items-center gap-2 text-sm font-medium text-zinc-100">
                          <FolderOpen size={15} className="text-zinc-500" />
                          Workspace
                        </div>
                        {hasUsageSummary && (
                          <div className="mt-3 grid grid-cols-2 gap-2">
                            <div className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2">
                              <div className="text-[11px] uppercase tracking-wide text-zinc-500">Session total</div>
                              <div className="mt-1 text-sm text-zinc-100">{sessionCostLabel ? `${sessionCostLabel} credits` : "Unavailable"}</div>
                            </div>
                            <div className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2">
                              <div className="text-[11px] uppercase tracking-wide text-zinc-500">Requests</div>
                              <div className="mt-1 text-sm text-zinc-100">{usageSummary.requestCount}</div>
                            </div>
                          </div>
                        )}
                        <div className="mt-2 text-xs text-zinc-500">{activeConversation?.workspace?.path || "No workspace yet"}</div>
                        <div className="mt-3 space-y-2">
                          {activeConversation?.workspace?.files.length ? activeConversation.workspace.files.map((file) => (
                            <div key={file.path} className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2 text-xs text-zinc-300">
                              <div>{file.path}</div>
                              <div className="mt-1 text-zinc-500">{file.size} bytes</div>
                            </div>
                          )) : (
                            <div className="text-xs text-zinc-500">No workspace files yet.</div>
                          )}
                        </div>
                        <div className="mt-4 border-t border-zinc-800 pt-4">
                          <div className="flex items-center gap-2 text-sm font-medium text-zinc-100">
                            <FileStack size={15} className="text-zinc-500" />
                            Artifacts
                          </div>
                          <div className="mt-3 flex flex-wrap gap-2">
                            {artifacts.slice(-6).reverse().map((artifact) => (
                              <span key={artifact.id} className="rounded-full border border-zinc-700 bg-zinc-950 px-3 py-1 text-xs text-zinc-300">
                                {artifact.artifact_type}
                              </span>
                            ))}
                          </div>
                        </div>
                      </section>
                    </>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        <div className="flex-1 overflow-y-auto py-3 md:py-4">
          {loadingConversation ? (
            <div className="flex h-full items-center justify-center text-sm text-zinc-500">
              Loading conversation...
            </div>
          ) : messages.length === 0 && !activeConversationId && draftSessionMetadata ? (
            <div className="px-3 py-4 md:px-4 md:py-6">
              <div className="rounded-3xl border border-zinc-800 bg-zinc-900/80 p-5">
                <div className="flex items-center gap-2 text-zinc-100">
                  <MessageSquare size={16} className="text-zinc-500" />
                  <h2 className="text-sm font-semibold">New Chat</h2>
                </div>
                <div className="mt-4 inline-flex rounded-2xl border border-zinc-800 bg-zinc-950/60 p-1">
                  {(["single", "council"] as const).map((mode) => (
                    <button
                      key={mode}
                      onClick={() => setDraftSessionMetadata({ ...draftSessionMetadata, mode })}
                      className={clsx(
                        "rounded-xl px-4 py-2 text-sm transition-colors",
                        draftSessionMetadata.mode === mode
                          ? "bg-blue-500/10 text-blue-300"
                          : "text-zinc-400"
                      )}
                    >
                      {mode === "single" ? "Agent" : "Council"}
                    </button>
                  ))}
                </div>
                {availableModels.length > 0 && (
                  <div className="mt-3 text-xs text-zinc-500">
                    Models and other defaults come from Settings.
                  </div>
                )}
              </div>
            </div>
          ) : messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center px-8">
              <div className="w-16 h-16 rounded-2xl bg-zinc-800 flex items-center justify-center mb-4">
                <MessageSquare size={28} className="text-zinc-500" />
              </div>
              <h2 className="text-lg font-semibold text-zinc-300 mb-2">
                {activeConversationId ? "Continue this conversation" : "Start a conversation"}
              </h2>
              {activeSessionMetadata && (
                <p className="text-sm text-zinc-500 max-w-xs">
                  {councilMode
                    ? "Council mode is active for this conversation. This mode stays chat-only."
                    : "Agent mode is active for this conversation. The assistant can take multi-step actions when the request needs it."}
                </p>
              )}
            </div>
          ) : (
            <>
              {activeConversation && activeConversation.branches.length > 1 && (
                <div className="mb-4 px-3 md:px-4">
                  <div className="rounded-2xl border border-zinc-800 bg-zinc-900/80 p-4">
                    <div className="flex items-center gap-2 text-sm font-medium text-zinc-100">
                      <GitBranch size={15} className="text-zinc-500" />
                      Branches
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {activeConversation.branches.map((branch) => (
                        <button
                          key={branch.branch_key}
                          onClick={() => selectBranch(branch.branch_key)}
                          className={clsx(
                            "rounded-full border px-3 py-1 text-xs transition-colors",
                            activeBranchKey === branch.branch_key
                              ? "border-blue-500/40 bg-blue-500/10 text-blue-300"
                              : "border-zinc-700 bg-zinc-950 text-zinc-400"
                          )}
                        >
                          {branch.label || branch.branch_key}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {editingMessageId && (
                <div className="mb-4 px-3 md:px-4">
                  <div className="rounded-2xl border border-amber-500/30 bg-amber-500/10 p-4">
                    <div className="text-sm font-medium text-amber-100">Edit this user turn and resend into a new branch</div>
                    <textarea
                      value={editedContent}
                      onChange={(event) => setEditedContent(event.target.value)}
                      className="mt-3 min-h-28 w-full rounded-xl border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
                    />
                    <div className="mt-3 flex gap-2">
                      <button
                        onClick={() => handleEditSubmit()}
                        className="rounded-lg bg-amber-400 px-3 py-2 text-sm font-medium text-zinc-950"
                      >
                        Create branch
                      </button>
                      <button
                        onClick={() => {
                          setEditingMessageId(null);
                          setEditedContent("");
                        }}
                        className="rounded-lg border border-zinc-700 px-3 py-2 text-sm text-zinc-300"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {activeConversationId && showDesktopDetails && (events.length > 0 || artifacts.length > 0) && (
                <div className="mb-4 hidden gap-4 px-4 lg:grid lg:grid-cols-[1.1fr_0.9fr]">
                  <section className="rounded-2xl border border-zinc-800 bg-zinc-900/80 p-4">
                    <div className="flex items-center gap-2 text-sm font-medium text-zinc-100">
                      <Clock3 size={15} className="text-zinc-500" />
                      Recent Trace
                    </div>
                    <div className="mt-3 space-y-2">
                      {events.slice(-6).reverse().map((event) => (
                        <div key={event.id} className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2">
                          <div className="flex items-center justify-between gap-3">
                            <div className="text-xs text-zinc-200">{formatTraceLabel(event.event_type)}</div>
                            <div className="text-[11px] text-zinc-500">{formatConversationLabel(event.created_at)}</div>
                          </div>
                          {summarizeAgentEvent(event.event_type, event.payload_json) && (
                            <div className="mt-1 line-clamp-2 text-xs text-zinc-500">
                              {summarizeAgentEvent(event.event_type, event.payload_json)}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </section>

                  <section className="rounded-2xl border border-zinc-800 bg-zinc-900/80 p-4">
                    <div className="flex items-center gap-2 text-sm font-medium text-zinc-100">
                      <FolderOpen size={15} className="text-zinc-500" />
                      Workspace
                    </div>
                    {hasUsageSummary && (
                      <div className="mt-3 grid grid-cols-2 gap-2">
                        <div className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2">
                          <div className="text-[11px] uppercase tracking-wide text-zinc-500">Session total</div>
                          <div className="mt-1 text-sm text-zinc-100">{sessionCostLabel ? `${sessionCostLabel} credits` : "Unavailable"}</div>
                        </div>
                        <div className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2">
                          <div className="text-[11px] uppercase tracking-wide text-zinc-500">Requests</div>
                          <div className="mt-1 text-sm text-zinc-100">{usageSummary.requestCount}</div>
                        </div>
                      </div>
                    )}
                    <div className="mt-2 text-xs text-zinc-500">{activeConversation?.workspace?.path || "No workspace yet"}</div>
                    <div className="mt-3 space-y-2">
                      {activeConversation?.workspace?.files.length ? activeConversation.workspace.files.map((file) => (
                        <div key={file.path} className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2 text-xs text-zinc-300">
                          <div>{file.path}</div>
                          <div className="mt-1 text-zinc-500">{file.size} bytes</div>
                        </div>
                      )) : (
                        <div className="text-xs text-zinc-500">No workspace files yet.</div>
                      )}
                    </div>
                    <div className="mt-4 border-t border-zinc-800 pt-4">
                      <div className="flex items-center gap-2 text-sm font-medium text-zinc-100">
                        <FileStack size={15} className="text-zinc-500" />
                        Artifacts
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {artifacts.slice(-6).reverse().map((artifact) => (
                          <span key={artifact.id} className="rounded-full border border-zinc-700 bg-zinc-950 px-3 py-1 text-xs text-zinc-300">
                            {artifact.artifact_type}
                          </span>
                        ))}
                      </div>
                    </div>
                  </section>
                </div>
              )}
              {agentStatus && <AgentStatusCard status={agentStatus} />}
              {renderedMessages.map((msg) => (
                <ChatMessage
                  key={msg.id}
                  message={msg}
                  onEdit={msg.role === "user" && (msg.kind ?? "message") === "message" ? handleEditStart : undefined}
                />
              ))}
              <div ref={bottomRef} />
            </>
          )}
        </div>

        <ChatInput
          onSend={handleSend}
          onStop={stopStreaming}
          loading={loading}
          canStop={canStop}
          disabled={loadingConversation || !settingsLoaded || !activeSessionMetadata}
          allowAgentTools={activeSessionMetadata?.mode !== "council"}
        />
      </div>
    </div>
  );
}
