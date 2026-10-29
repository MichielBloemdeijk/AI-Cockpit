"use client";

import { useRef, useState, KeyboardEvent } from "react";
import { Loader2, Send, Square } from "lucide-react";
import clsx from "clsx";

interface Props {
  onSend: (value: string) => void;
  onStop: () => void;
  loading: boolean;
  canStop?: boolean;
  disabled?: boolean;
  councilMode?: boolean;
}

export function ChatInput({ onSend, onStop, loading, canStop = false, disabled, councilMode = false }: Props) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const trimmed = value.trim();

  const handleSend = () => {
    if (!trimmed || loading) return;
    onSend(trimmed);
    setValue("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInput = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  };
  const placeholder = councilMode ? "Ask the council..." : "Message AI Cockpit…";

  return (
    <div className="mobile-safe-chat-input border-t border-zinc-700 bg-zinc-900 px-3 pt-3 md:p-4">
      <div className="flex items-end gap-2">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onInput={handleInput}
          placeholder={placeholder}
          disabled={disabled || (loading && !canStop)}
          rows={1}
          className={clsx(
            "max-h-[200px] min-h-11 flex-1 resize-none overflow-y-auto rounded-2xl border border-zinc-600 bg-zinc-800 px-4 py-3",
            "text-sm text-zinc-100 placeholder-zinc-500 transition-colors focus:border-blue-500 focus:outline-none",
            (disabled || (loading && !canStop)) && "cursor-not-allowed opacity-50"
          )}
        />
        <button
          onClick={loading && canStop ? onStop : handleSend}
          disabled={disabled || (loading && !canStop) || (!trimmed && !loading)}
          className={clsx(
            "flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-2xl transition-colors",
            loading
              ? canStop
                ? "bg-red-600 text-white hover:bg-red-700"
                : "cursor-wait bg-zinc-700 text-zinc-300"
              : "bg-blue-600 text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40"
          )}
          title={loading ? (canStop ? "Stop" : "Working") : "Send"}
        >
          {loading ? (canStop ? <Square size={16} /> : <Loader2 size={16} className="animate-spin" />) : <Send size={16} />}
        </button>
      </div>
    </div>
  );
}
