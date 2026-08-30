"use client";

import { useEffect, useRef, useState } from "react";
import { Archive, ArchiveRestore, GitBranch, Layers3, MessageSquare, PanelLeftClose, PanelLeftOpen, Plus } from "lucide-react";
import clsx from "clsx";
import { useRouter } from "next/navigation";
import { ChatActionErrorBanner } from "@/components/chat/ChatActionErrorBanner";
import { ConversationDetailsPanel } from "@/components/chat/ConversationDetailsPanel";
import { ConversationListPanel } from "@/components/chat/ConversationListPanel";
import { ChatMessage } from "@/components/ChatMessage";
import { ChatInput, type ChatComposerMode } from "@/components/ChatInput";
import { formatConversationModeLabel } from "@/components/chat/formatters";
import { useChat } from "@/lib/hooks";
import { ConversationSessionMetadata, getChatSettings, getConversation } from "@/lib/api";
import { formatUsageCost, summarizeConversationUsage } from "@/lib/usage";

function buildNewConversationMetadata(defaults: ConversationSessionMetadata): ConversationSessionMetadata {
  return { ...defaults, mode: "single" };
}

export default function ChatPage() {
  const router = useRouter();
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
    actionError,
    conversations,
    activeConversationId,
    activeBranchKey,
    activeConversation,
    events,
    artifacts,
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
    unarchiveActiveConversation,
    clearMessages,
    selectConversation,
    selectBranch,
    setConversationKindFilter,
  } = useChat();
  const messagesViewportRef = useRef<HTMLDivElement>(null);
  const deepLinkHandledRef = useRef(false);

  useEffect(() => {
    getChatSettings().then((response) => {
      setAvailableModels(response.available_models);
      setDefaultSessionMetadata(response.defaults);
      setDraftSessionMetadata(buildNewConversationMetadata(response.defaults));
      setSettingsLoaded(true);
    }).catch(() => setSettingsLoaded(true));
  }, []);

  useEffect(() => {
    const viewport = messagesViewportRef.current;
    if (!viewport) {
      return;
    }

    const frameId = window.requestAnimationFrame(() => {
      const distanceFromBottom = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
      const shouldStickToBottom = distanceFromBottom < 160 || loading || canStop;
      if (!shouldStickToBottom) {
        return;
      }
      viewport.scrollTo({
        top: viewport.scrollHeight,
        behavior: loading || canStop ? "auto" : "smooth",
      });
    });

    return () => window.cancelAnimationFrame(frameId);
  }, [messages, loading, canStop]);

  const activeSessionMetadata = activeConversation?.session_metadata ?? draftSessionMetadata;
  const councilMode = activeSessionMetadata?.mode === "council";
  const activeConversationSummary = conversations.find((conversation) => conversation.id === activeConversationId) ?? null;
  const activeConversationTitle = activeConversationSummary?.title || activeConversation?.title || "Chat";
  const mobileHeaderBadge = councilMode && activeSessionMetadata
    ? `${activeSessionMetadata.council_models.length} models`
    : null;
  const usageSummary = summarizeConversationUsage(events, artifacts);
  const sessionCostLabel = formatUsageCost(usageSummary.totalUsage?.cost);
  const hasUsageSummary = usageSummary.requestCount > 0;
  const hasConversationDetails = Boolean(activeConversationId && (events.length > 0 || artifacts.length > 0 || activeConversation?.workspace?.files.length));
  const archivedConversation = Boolean(activeConversation?.archived_at);
  const readOnlyJarvisLog = Boolean(activeConversation?.is_read_only);
  const readOnlyConversation = readOnlyJarvisLog || archivedConversation;

  const handleSend = async (payload: { mode: ChatComposerMode; value: string }) => {
    if (!activeSessionMetadata || readOnlyConversation) return;
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

  const handleSelectConversation = (conversationId: string) => {
    const selected = conversations.find((conversation) => conversation.id === conversationId);
    if (selected?.is_current_jarvis) {
      router.push("/jarvis");
      return;
    }
    selectConversation(conversationId);
  };

  const handleUnarchiveConversation = async () => {
    await unarchiveActiveConversation();
    setConversationKindFilter("all");
  };

  useEffect(() => {
    if (deepLinkHandledRef.current) return;
    const conversationId = new URLSearchParams(window.location.search).get("conversation");
    if (!conversationId) {
      deepLinkHandledRef.current = true;
      return;
    }
    deepLinkHandledRef.current = true;
    void getConversation(conversationId).then((selected) => {
      if (selected.is_current_jarvis) {
        router.push("/jarvis");
        return;
      }
      if (selected.archived_at) setConversationKindFilter("archived");
      selectConversation(conversationId);
    }).catch(() => {
      // Keep the normal Chat selection if a stale or invalid deep link cannot be resolved.
    });
  }, [router, selectConversation, setConversationKindFilter]);

  return (
    <div className="flex h-full min-h-0 bg-zinc-900">
      <ConversationListPanel
        conversations={conversations}
        activeConversationId={activeConversationId}
        conversationKindFilter={conversationKindFilter}
        onNewConversation={handleNewConversation}
        onSelectConversation={handleSelectConversation}
        onChangeKindFilter={setConversationKindFilter}
      />

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
                  onClick={() => archivedConversation ? void handleUnarchiveConversation() : archiveActiveConversation()}
                  className="hidden items-center gap-1 rounded-lg border border-zinc-700 px-2.5 py-1.5 text-xs text-zinc-300 md:inline-flex"
                >
                  {archivedConversation ? <ArchiveRestore size={12} /> : <Archive size={12} />}
                  {archivedConversation ? "Restore" : "Archive"}
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
                <ConversationListPanel
                  mobile
                  conversations={conversations}
                  activeConversationId={activeConversationId}
                  conversationKindFilter={conversationKindFilter}
                  onNewConversation={() => {
                    handleNewConversation();
                    closeMobileDrawer();
                  }}
                  onSelectConversation={(conversationId) => {
                    handleSelectConversation(conversationId);
                    closeMobileDrawer();
                  }}
                  onChangeKindFilter={setConversationKindFilter}
                />
              ) : (
                <ConversationDetailsPanel
                  mobile
                  activeConversation={activeConversation}
                  activeConversationId={activeConversationId}
                  activeBranchKey={activeBranchKey}
                  events={events}
                  artifacts={artifacts}
                  onArchiveConversation={() => {
                    archiveActiveConversation();
                    closeMobileDrawer();
                  }}
                  onUnarchiveConversation={() => {
                    void handleUnarchiveConversation();
                    closeMobileDrawer();
                  }}
                  onSelectBranch={(branchKey) => {
                    selectBranch(branchKey);
                    closeMobileDrawer();
                  }}
                />
              )}
            </div>
          </div>
        )}

        <div ref={messagesViewportRef} className="flex-1 overflow-y-auto py-3 md:py-4">
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
              {activeConversation && !readOnlyConversation && activeConversation.branches.length > 1 && (
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

              {editingMessageId && !readOnlyConversation && (
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

              {activeConversationId && showDesktopDetails && (
                <ConversationDetailsPanel
                  activeConversation={activeConversation}
                  activeConversationId={activeConversationId}
                  activeBranchKey={activeBranchKey}
                  events={events}
                  artifacts={artifacts}
                  onArchiveConversation={archiveActiveConversation}
                  onUnarchiveConversation={() => void handleUnarchiveConversation()}
                  onSelectBranch={selectBranch}
                />
              )}
              {actionError && <ChatActionErrorBanner error={actionError} onDismiss={clearActionError} />}
              {readOnlyJarvisLog && (
                <div className="mb-4 px-3 md:px-4">
                  <div className="rounded-2xl border border-violet-500/30 bg-violet-500/10 px-4 py-3 text-sm text-violet-100">
                    This Jarvis daily log is closed and read-only. The current conversation continues in the Jarvis workspace.
                  </div>
                </div>
              )}
              {archivedConversation && (
                <div className="mb-4 px-3 md:px-4">
                  <div className="rounded-2xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
                    This archived conversation is read-only here. Restore it to continue, branch, or resend.
                  </div>
                </div>
              )}
              {messages.map((msg) => (
                <ChatMessage
                  key={msg.activityId ?? msg.id}
                  message={msg}
                  onEdit={!readOnlyConversation && msg.role === "user" && (msg.kind ?? "message") === "message" ? handleEditStart : undefined}
                />
              ))}
            </>
          )}
        </div>

        {readOnlyConversation ? (
          <div className="border-t border-zinc-700 bg-zinc-900 px-4 py-3 text-center text-xs text-zinc-500">
            {archivedConversation
              ? "Archived conversations are read-only until restored."
              : "Closed Jarvis logs cannot be continued, branched, or resent."}
          </div>
        ) : (
          <ChatInput
            onSend={handleSend}
            onStop={stopStreaming}
            loading={loading}
            canStop={canStop}
            disabled={loadingConversation || !settingsLoaded || !activeSessionMetadata}
            allowAgentTools={activeSessionMetadata?.mode !== "council"}
          />
        )}
      </div>
    </div>
  );
}
