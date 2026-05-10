"use client";

import { AlertTriangle, X } from "lucide-react";

import type { ChatActionError } from "@/lib/hooks";

interface Props {
  error: ChatActionError;
  onDismiss: () => void;
}

const ERROR_LABELS: Record<ChatActionError["scope"], string> = {
  archive: "Archive failed",
  branch: "Branch action failed",
  send: "Send failed",
  tool: "Tool action failed",
};

export function ChatActionErrorBanner({ error, onDismiss }: Props) {
  return (
    <div className="mb-4 px-3 md:px-4">
      <div className="flex items-start gap-3 rounded-2xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-rose-100">
        <AlertTriangle size={16} className="mt-0.5 shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="text-[11px] uppercase tracking-[0.16em]">{ERROR_LABELS[error.scope]}</div>
          <div className="mt-1 text-sm leading-6 text-rose-100/80">{error.message}</div>
        </div>
        <button
          onClick={onDismiss}
          className="rounded-lg border border-rose-400/20 px-2 py-1 text-xs text-rose-100/80 transition-colors hover:border-rose-300/40 hover:text-rose-50"
          aria-label="Dismiss error"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  );
}