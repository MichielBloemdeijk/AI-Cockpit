"use client";

import { Bot, ListTodo } from "lucide-react";

export default function BackgroundRunsPage() {
  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-zinc-700 bg-zinc-900 px-4 py-3">
        <div className="flex items-center gap-2">
          <ListTodo size={18} className="text-zinc-400" />
          <span className="text-sm font-medium text-zinc-100">Background Runs</span>
          <span className="text-xs text-zinc-500">(0)</span>
        </div>
      </div>

      <div className="shrink-0 border-b border-zinc-700 bg-zinc-900/60 px-4 py-3 text-sm text-zinc-400">
        Background runs are currently disabled. Start and steer all agent work directly from Chat.
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        <div className="flex h-full flex-col items-center justify-center px-8 text-center">
          <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-zinc-800">
            <Bot size={28} className="text-zinc-500" />
          </div>
          <h2 className="mb-2 text-lg font-semibold text-zinc-300">Background is paused</h2>
          <p className="max-w-xl text-sm text-zinc-500">
            This placeholder will stay empty while the product uses a chat-first full agent loop.
          </p>
        </div>
      </div>
    </div>
  );
}