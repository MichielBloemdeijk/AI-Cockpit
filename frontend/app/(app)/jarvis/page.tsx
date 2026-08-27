"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AudioLines, History, Loader2, Plus, Send } from "lucide-react";

import { ChatMessage } from "@/components/ChatMessage";
import {
  acceptJarvisTextTurn,
  getJarvisState,
  startFreshJarvisLog,
  type JarvisStateResponse,
} from "@/lib/api";


export default function JarvisPage() {
  const [state, setState] = useState<JarvisStateResponse | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const nextState = await getJarvisState();
    setState(nextState);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void getJarvisState()
      .then((nextState) => {
        if (!cancelled) setState(nextState);
      })
      .catch((cause) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "Unable to load Jarvis");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSend = async () => {
    const content = draft.trim();
    if (!content || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await acceptJarvisTextTurn(content, crypto.randomUUID());
      setDraft("");
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Jarvis could not accept the turn");
    } finally {
      setSubmitting(false);
    }
  };

  const handleFreshStart = async () => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await startFreshJarvisLog(crypto.randomUUID());
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Jarvis could not start a fresh log");
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-zinc-400">
        <Loader2 className="animate-spin" size={22} />
      </div>
    );
  }

  const currentLog = state?.current_log ?? null;

  return (
    <div className="flex h-full min-h-0 flex-col bg-zinc-900">
      <header className="flex items-center justify-between gap-4 border-b border-zinc-700 px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-zinc-100">
            <AudioLines size={18} className="text-violet-400" />
            <h1 className="truncate text-sm font-semibold">{currentLog?.title || "Jarvis"}</h1>
            {currentLog && (
              <span className="rounded-full border border-violet-500/30 bg-violet-500/10 px-2 py-0.5 text-[11px] text-violet-300">
                {currentLog.is_live ? "Live" : "Current Jarvis"}
              </span>
            )}
          </div>
          <div className="mt-1 text-xs text-zinc-500">
            Operational day {state?.operational_day || "—"} · typed bridge
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/chat"
            className="inline-flex items-center gap-1 rounded-xl border border-zinc-700 px-3 py-2 text-xs text-zinc-300"
          >
            <History size={13} />
            History
          </Link>
          <button
            onClick={handleFreshStart}
            disabled={submitting}
            className="inline-flex items-center gap-1 rounded-xl border border-zinc-700 px-3 py-2 text-xs text-zinc-300 disabled:opacity-50"
          >
            <Plus size={13} />
            Fresh log
          </button>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto px-3 py-5 md:px-8">
        <div className="mx-auto max-w-3xl space-y-3">
          {!currentLog?.messages.length ? (
            <div className="flex min-h-[48vh] flex-col items-center justify-center text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-violet-500/20 bg-violet-500/10">
                <AudioLines size={28} className="text-violet-400" />
              </div>
              <h2 className="mt-4 text-lg font-semibold text-zinc-200">Jarvis is ready</h2>
              <p className="mt-2 max-w-sm text-sm text-zinc-500">
                The first accepted turn creates today&apos;s durable log. Opening this page alone creates nothing.
              </p>
            </div>
          ) : (
            currentLog.messages.map((message) => (
              <ChatMessage
                key={message.id}
                message={{
                  id: message.id,
                  role: message.role,
                  kind: "message",
                  label: message.author_label || undefined,
                  content: message.content,
                  createdAt: message.created_at,
                  branchable: false,
                  badges: message.communication_state && message.communication_state !== "completed"
                    ? [message.communication_state]
                    : undefined,
                }}
              />
            ))
          )}
          {error && (
            <div className="rounded-2xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
              {error}
            </div>
          )}
        </div>
      </main>

      <div className="border-t border-zinc-700 bg-zinc-900 px-3 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] pt-3 md:p-4">
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void handleSend();
              }
            }}
            placeholder="Talk to Jarvis…"
            disabled={submitting}
            rows={1}
            className="max-h-48 min-h-11 flex-1 resize-none rounded-2xl border border-zinc-600 bg-zinc-800 px-4 py-3 text-sm text-zinc-100 placeholder-zinc-500 focus:border-violet-500 focus:outline-none disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={!draft.trim() || submitting}
            aria-label="Send to Jarvis"
            className="flex h-11 w-11 items-center justify-center rounded-2xl bg-violet-600 text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {submitting ? <Loader2 className="animate-spin" size={17} /> : <Send size={17} />}
          </button>
        </div>
      </div>
    </div>
  );
}
