"use client";

import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { BookOpen, Check, FileText, RefreshCw, Trash2, X } from "lucide-react";
import {
  approveKnowledgeItem,
  deleteKnowledgeItem,
  extractConversationMemoryItems,
  KnowledgeDocumentView,
  KnowledgeReviewItemView,
  listConversations,
  listKnowledgeDocuments,
  listKnowledgeReviewItems,
  rejectKnowledgeItem,
} from "@/lib/api";

export default function KnowledgePage() {
  const [conversations, setConversations] = useState<{ id: string; title: string | null }[]>([]);
  const [selectedConversationId, setSelectedConversationId] = useState<string>("");
  const [queue, setQueue] = useState<KnowledgeReviewItemView[]>([]);
  const [approvedItems, setApprovedItems] = useState<KnowledgeReviewItemView[]>([]);
  const [documents, setDocuments] = useState<KnowledgeDocumentView[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [mobileSection, setMobileSection] = useState<"review" | "library">("review");

  const refresh = async () => {
    setLoading(true);
    try {
      const [conversationList, reviewResponse, approvedResponse, documentsResponse] = await Promise.all([
        listConversations(),
        listKnowledgeReviewItems(),
        listKnowledgeReviewItems("approved"),
        listKnowledgeDocuments(),
      ]);
      setConversations(conversationList.map((conversation) => ({ id: conversation.id, title: conversation.title })));
      setSelectedConversationId((current) => current || conversationList[0]?.id || "");
      setQueue(reviewResponse.items);
      setApprovedItems(approvedResponse.items);
      setDocuments(documentsResponse.documents);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh().catch(() => setLoading(false));
  }, []);

  const selectedConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === selectedConversationId),
    [conversations, selectedConversationId],
  );

  const generateFromConversation = async () => {
    if (!selectedConversationId) return;
    setBusyId("generate");
    try {
      await extractConversationMemoryItems(selectedConversationId);
      await refresh();
    } finally {
      setBusyId(null);
    }
  };

  const handleApprove = async (memoryItemId: string) => {
    setBusyId(memoryItemId);
    try {
      await approveKnowledgeItem(memoryItemId);
      await refresh();
    } finally {
      setBusyId(null);
    }
  };

  const handleReject = async (memoryItemId: string) => {
    setBusyId(memoryItemId);
    try {
      await rejectKnowledgeItem(memoryItemId);
      await refresh();
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (memoryItemId: string) => {
    setBusyId(memoryItemId);
    try {
      await deleteKnowledgeItem(memoryItemId);
      await refresh();
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-x-hidden">
      <div className="border-b border-zinc-700 bg-zinc-900 px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <BookOpen size={18} className="text-zinc-400" />
            <span className="text-sm font-medium text-zinc-100">Knowledge Review</span>
          </div>
          <button
            onClick={() => refresh()}
            className="inline-flex items-center gap-2 rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300"
          >
            <RefreshCw size={13} />
            Refresh
          </button>
        </div>

        <div className="mt-3 flex gap-2 md:hidden">
          <button
            onClick={() => setMobileSection("review")}
            className={clsx(
              "rounded-full border px-3 py-1.5 text-xs transition-colors",
              mobileSection === "review"
                ? "border-blue-500/40 bg-blue-500/10 text-blue-300"
                : "border-zinc-700 bg-zinc-950 text-zinc-400"
            )}
          >
            Review queue
          </button>
          <button
            onClick={() => setMobileSection("library")}
            className={clsx(
              "rounded-full border px-3 py-1.5 text-xs transition-colors",
              mobileSection === "library"
                ? "border-blue-500/40 bg-blue-500/10 text-blue-300"
                : "border-zinc-700 bg-zinc-950 text-zinc-400"
            )}
          >
            Approved + files
          </button>
        </div>
      </div>

      <div className="grid min-h-0 min-w-0 flex-1 gap-4 p-3 md:p-4 lg:grid-cols-[1.1fr_0.9fr]">
        <section
          className={clsx(
            "min-h-0 min-w-0 flex-col rounded-3xl border border-zinc-800 bg-zinc-900/70",
            mobileSection === "review" ? "flex" : "hidden md:flex"
          )}
        >
          <div className="min-w-0 border-b border-zinc-800 p-4">
            <div className="text-sm font-semibold text-zinc-100">Review Queue</div>
            <p className="mt-1 text-sm text-zinc-500">Generate proposed knowledge from a conversation, then approve or reject each item before it reaches the file-backed knowledge layer.</p>
            <div className="mt-4 flex flex-col gap-2">
              <select
                value={selectedConversationId}
                onChange={(e) => setSelectedConversationId(e.target.value)}
                className="w-full min-w-0 rounded-xl border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
              >
                <option value="">Select conversation</option>
                {conversations.map((conversation) => (
                  <option key={conversation.id} value={conversation.id}>
                    {conversation.title || "Untitled conversation"}
                  </option>
                ))}
              </select>
              <button
                onClick={generateFromConversation}
                disabled={!selectedConversationId || busyId === "generate"}
                className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                {busyId === "generate" ? "Generating..." : "Generate proposals"}
              </button>
            </div>
            {selectedConversation && (
              <div className="mt-2 text-xs text-zinc-500">Source conversation: {selectedConversation.title || "Untitled conversation"}</div>
            )}
          </div>

          <div className="min-w-0 flex-1 space-y-3 overflow-y-auto p-4">
            {loading ? (
              <div className="text-sm text-zinc-500">Loading knowledge review queue...</div>
            ) : queue.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-zinc-800 p-6 text-sm text-zinc-500">No proposed items yet. Generate proposals from a conversation to start review.</div>
            ) : (
              queue.map((item) => (
                <div key={item.id} className="rounded-2xl border border-zinc-800 bg-zinc-950/60 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-zinc-100">{item.title}</div>
                      <div className="mt-1 break-words text-xs text-zinc-500">{item.kind} · {item.scope} · {item.conversation_title || item.source_conversation_id}</div>
                    </div>
                    <div className="shrink-0 text-xs text-zinc-500">{item.confidence ? `${Math.round(item.confidence * 100)}%` : "manual"}</div>
                  </div>
                  <p className="mt-3 whitespace-pre-wrap text-sm text-zinc-300">{item.content}</p>
                  <div className="mt-4 flex flex-col gap-2 sm:flex-row">
                    <button
                      onClick={() => handleApprove(item.id)}
                      disabled={busyId === item.id}
                      className="inline-flex items-center justify-center gap-2 rounded-lg bg-emerald-600 px-3 py-2 text-sm text-white disabled:opacity-50"
                    >
                      <Check size={14} />
                      Approve
                    </button>
                    <button
                      onClick={() => handleReject(item.id)}
                      disabled={busyId === item.id}
                      className="inline-flex items-center justify-center gap-2 rounded-lg border border-zinc-700 px-3 py-2 text-sm text-zinc-300 disabled:opacity-50"
                    >
                      <X size={14} />
                      Reject
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </section>

        <section
          className={clsx(
            "min-h-0 min-w-0 flex-col rounded-3xl border border-zinc-800 bg-zinc-900/70",
            mobileSection === "library" ? "flex" : "hidden md:flex"
          )}
        >
          <div className="min-w-0 border-b border-zinc-800 p-4">
            <div className="text-sm font-semibold text-zinc-100">Approved Knowledge</div>
            <p className="mt-1 text-sm text-zinc-500">Approved items can be deleted here, which removes the file-backed entry and keeps a tombstoned record in persistence.</p>
          </div>

          <div className="min-w-0 flex-1 space-y-3 overflow-y-auto p-4">
            {approvedItems.length === 0 && documents.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-zinc-800 p-6 text-sm text-zinc-500">No approved knowledge yet.</div>
            ) : null}
            {approvedItems.map((item) => (
              <div key={item.id} className="rounded-2xl border border-zinc-800 bg-zinc-950/60 p-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-zinc-100">{item.title}</div>
                    <div className="mt-1 break-words text-xs text-zinc-500">{item.kind} · {item.scope} · {item.conversation_title || item.source_conversation_id}</div>
                  </div>
                  <button
                    onClick={() => handleDelete(item.id)}
                    disabled={busyId === item.id}
                    className="inline-flex items-center justify-center gap-2 rounded-lg border border-zinc-700 px-3 py-2 text-sm text-zinc-300 disabled:opacity-50"
                  >
                    <Trash2 size={14} />
                    Delete
                  </button>
                </div>
                <p className="mt-3 whitespace-pre-wrap text-sm text-zinc-300">{item.content}</p>
              </div>
            ))}

            <div className="border-t border-zinc-800 pt-4">
              <div className="text-sm font-semibold text-zinc-100">Knowledge Files</div>
              <p className="mt-1 text-sm text-zinc-500">Approved items are written into the file-backed corpus that chat tools can inspect directly.</p>
            </div>
            {documents.map((document) => (
              <div key={document.path} className="rounded-2xl border border-zinc-800 bg-zinc-950/60 p-4">
                <div className="flex items-center gap-2 text-zinc-100">
                  <FileText size={15} className="text-zinc-500" />
                  <div className="min-w-0 text-sm font-medium">{document.title}</div>
                </div>
                <div className="mt-1 break-all text-xs text-zinc-500">{document.kind} · {document.path}</div>
                <pre className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-zinc-950 p-3 text-xs text-zinc-300">{document.content}</pre>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
