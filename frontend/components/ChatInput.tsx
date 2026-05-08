"use client";

import { useRef, useState, KeyboardEvent } from "react";
import { Loader2, Send, Square } from "lucide-react";
import clsx from "clsx";

export type ChatComposerMode = "chat" | "workspace_search" | "python_execution";

function parseComposerInput(rawValue: string, allowAgentTools: boolean): { mode: ChatComposerMode; value: string; helperText: string | null } {
  const trimmed = rawValue.trim();
  const commandMatch = /^\/(search|python|py)\b([\s\S]*)$/i.exec(trimmed);

  if (!commandMatch) {
    return {
      mode: "chat",
      value: trimmed,
      helperText: trimmed.startsWith("/")
        ? "Unknown slash command. Use /search <query> or /python <code>."
        : null,
    };
  }

    if (!allowAgentTools) {
      return {
        mode: "chat",
        value: trimmed,
        helperText: "Council mode is chat-only. Agent slash commands are disabled here.",
      };
    }

  const [, command, remainder] = commandMatch;
  const normalizedValue = remainder.replace(/^\s+/, "").replace(/\s+$/, "");
  if (command.toLowerCase() === "search") {
    return {
      mode: "workspace_search",
      value: normalizedValue,
      helperText: "Runs a manual workspace search and records the result in the conversation trace.",
    };
  }

  return {
    mode: "python_execution",
    value: normalizedValue,
    helperText: "Runs a guarded Python snippet. Reads are allowed in the repo, writes stay inside the conversation workspace.",
  };
}

interface Props {
  onSend: (payload: { mode: ChatComposerMode; value: string }) => void;
  onStop: () => void;
  loading: boolean;
  canStop?: boolean;
  disabled?: boolean;
  allowAgentTools?: boolean;
}

export function ChatInput({ onSend, onStop, loading, canStop = false, disabled, allowAgentTools = true }: Props) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const parsedInput = parseComposerInput(value, allowAgentTools);

  const handleSend = () => {
    if (!parsedInput.value || loading) return;
    onSend({ mode: parsedInput.mode, value: parsedInput.value });
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
  const placeholder = !allowAgentTools
    ? "Ask the council..."
    : parsedInput.mode === "workspace_search"
    ? "/search find references to conversation workspace"
    : parsedInput.mode === "python_execution"
      ? "/python print('hello from the conversation workspace')"
      : "Message AI Cockpit…";

  return (
    <div className="border-t border-zinc-700 bg-zinc-900 px-3 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] pt-3 md:p-4">
      {parsedInput.helperText && (
        <div className="mb-3 text-xs text-zinc-500">
          {parsedInput.helperText}
        </div>
      )}
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
          disabled={disabled || (loading && !canStop) || (!parsedInput.value && !loading)}
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
