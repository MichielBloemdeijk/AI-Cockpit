"use client";

import { Archive, AudioLines, History, Plus } from "lucide-react";
import clsx from "clsx";

import type { ConversationSummary } from "../../lib/api";

import { formatConversationLabel, formatConversationModeLabel } from "./formatters";
import type { ConversationKindFilter } from "../../lib/use-conversation-list";

interface Props {
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  showArchived: boolean;
  conversationKindFilter: ConversationKindFilter;
  mobile?: boolean;
  onNewConversation: () => void;
  onSelectConversation: (conversationId: string) => void;
  onToggleShowArchived: () => void;
  onChangeKindFilter: (filter: ConversationKindFilter) => void;
}

export function ConversationListPanel({
  conversations,
  activeConversationId,
  showArchived,
  conversationKindFilter,
  mobile = false,
  onNewConversation,
  onSelectConversation,
  onToggleShowArchived,
  onChangeKindFilter,
}: Props) {
  const filters: Array<{ value: ConversationKindFilter; label: string }> = [
    { value: "all", label: "All" },
    { value: "standard", label: "Standard" },
    { value: "jarvis", label: "Jarvis" },
  ];
  const filterControls = (
    <div className="flex gap-1 rounded-xl border border-zinc-800 bg-zinc-950 p-1">
      {filters.map((filter) => (
        <button
          key={filter.value}
          onClick={() => onChangeKindFilter(filter.value)}
          className={clsx(
            "flex-1 rounded-lg px-2 py-1.5 text-[11px] transition-colors",
            conversationKindFilter === filter.value
              ? "bg-blue-600 text-white"
              : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200",
          )}
        >
          {filter.label}
        </button>
      ))}
    </div>
  );

  if (mobile) {
    return (
      <div className="flex-1 overflow-y-auto p-3">
        <button
          onClick={onNewConversation}
          className="mb-3 inline-flex items-center gap-2 rounded-2xl border border-zinc-800 bg-zinc-900 px-4 py-3 text-sm text-zinc-100"
        >
          <Plus size={14} />
          New conversation
        </button>
        <div className="mb-3 space-y-2">
          {filterControls}
          <button
            onClick={onToggleShowArchived}
            className="inline-flex items-center gap-1 rounded-lg border border-zinc-700 px-2.5 py-1.5 text-xs text-zinc-300"
          >
            <Archive size={12} />
            {showArchived ? "Hide Archived" : "Show Archived"}
          </button>
        </div>
        <div className="space-y-2">
          {conversations.map((conversation) => (
            <button
              key={conversation.id}
              onClick={() => onSelectConversation(conversation.id)}
              className={clsx(
                "w-full rounded-2xl border px-3 py-3 text-left transition-colors",
                activeConversationId === conversation.id
                  ? "border-blue-500/40 bg-blue-500/10"
                  : "border-zinc-800 bg-zinc-900/80"
              )}
            >
              <div className="truncate text-sm font-medium text-zinc-100">
                <span className="inline-flex items-center gap-1.5">
                  {conversation.conversation_kind === "jarvis_daily" && <AudioLines size={13} className="text-violet-400" />}
                  {conversation.title || "Untitled conversation"}
                </span>
              </div>
              <div className="mt-2 line-clamp-2 text-xs text-zinc-400">
                {conversation.last_message_preview || "No messages yet."}
              </div>
              <div className="mt-3 text-[11px] text-zinc-500">
                {formatConversationLabel(conversation.updated_at)}
              </div>
              {conversation.conversation_kind === "jarvis_daily" && (
                <div className="mt-2 text-[11px] text-violet-300">
                  {conversation.is_live ? "Live" : conversation.is_current_jarvis ? "Current Jarvis" : "Jarvis · Read only"}
                </div>
              )}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <aside className="hidden border-r border-zinc-800 bg-zinc-950/80 backdrop-blur-sm md:flex md:w-80 md:flex-col">
      <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
        <div className="flex items-center gap-2 text-zinc-200">
          <History size={16} className="text-zinc-500" />
          <span className="text-sm font-medium">Recent Conversations</span>
        </div>
        <button
          onClick={onToggleShowArchived}
          className="inline-flex items-center gap-1 rounded-lg border border-zinc-700 px-2.5 py-1.5 text-xs text-zinc-300 hover:border-zinc-500 hover:text-white transition-colors"
        >
          <Archive size={12} />
          {showArchived ? "Hide Archived" : "Show Archived"}
        </button>
      </div>

      <div className="px-4 pt-3">
        <button
          onClick={onNewConversation}
          className="inline-flex items-center gap-1 rounded-lg border border-zinc-700 px-2.5 py-1.5 text-xs text-zinc-300 hover:border-zinc-500 hover:text-white transition-colors"
        >
          <Plus size={12} />
          New
        </button>
      </div>

      <div className="px-4 pt-3">{filterControls}</div>

      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {conversations.map((conversation) => (
          <button
            key={conversation.id}
            onClick={() => onSelectConversation(conversation.id)}
            className={clsx(
              "w-full rounded-2xl border px-3 py-3 text-left transition-colors",
              activeConversationId === conversation.id
                ? "border-blue-500/40 bg-blue-500/10"
                : "border-zinc-800 bg-zinc-900/80 hover:border-zinc-700 hover:bg-zinc-900"
            )}
          >
            <div className="flex items-center justify-between gap-3">
              <div className="truncate text-sm font-medium text-zinc-100">
                <span className="inline-flex items-center gap-1.5">
                  {conversation.conversation_kind === "jarvis_daily" && <AudioLines size={13} className="text-violet-400" />}
                  {conversation.title || "Untitled conversation"}
                </span>
              </div>
              <div className="text-[11px] uppercase tracking-wide text-zinc-500">
                {conversation.is_live
                  ? "Live"
                  : conversation.is_current_jarvis
                    ? "Current Jarvis"
                    : conversation.conversation_kind === "jarvis_daily"
                      ? "Jarvis"
                      : formatConversationModeLabel(conversation.mode_hint || conversation.session_metadata?.mode)}
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
  );
}
