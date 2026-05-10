"use client";

import { useState } from "react";
import { Bot, ChevronDown, ChevronRight, Loader2, Pencil, User } from "lucide-react";
import clsx from "clsx";

import type { ChatMessage } from "../../lib/chat-state-types";
import { formatTokenCount, formatUsageCost } from "../../lib/usage";

import { CouncilResponses } from "./CouncilResponses";

function splitTaskMetadata(content: string): { before: string; startLine: string; metadata: string; after: string } | null {
  const lines = content.split("\n");
  const startedIndex = lines.findIndex((line) => /started app task/i.test(line));
  if (startedIndex === -1) {
    return null;
  }

  const taskIdIndex = lines.findIndex((line, index) => index > startedIndex && /^Task id:/i.test(line.trim()));
  if (taskIdIndex === -1) {
    return null;
  }

  const isMetadataLine = (line: string) => {
    const trimmed = line.trim();
    return (
      trimmed.length === 0
      || /^Task id:/i.test(trimmed)
      || /^App root:/i.test(trimmed)
      || /^Entry page:/i.test(trimmed)
      || /^Allowed write roots:/i.test(trimmed)
    );
  };

  let endIndex = taskIdIndex;
  while (endIndex < lines.length && isMetadataLine(lines[endIndex])) {
    endIndex += 1;
  }

  const metadata = lines.slice(taskIdIndex, endIndex).join("\n").trimEnd();
  if (!metadata) {
    return null;
  }

  return {
    before: lines.slice(0, startedIndex).join("\n").trim(),
    startLine: lines[startedIndex].trim(),
    metadata,
    after: lines.slice(endIndex).join("\n").trim(),
  };
}

export function AssistantMessageRenderer({ message }: { message: ChatMessage }) {
  const [councilExpanded, setCouncilExpanded] = useState(false);
  const [taskDetailsExpanded, setTaskDetailsExpanded] = useState(false);
  const messageCost = formatUsageCost(message.usage?.cost);
  const messageTokens = formatTokenCount(message.usage?.total_tokens);
  const inlineTaskMetadata = message.content ? splitTaskMetadata(message.content) : null;

  return (
    <div className="px-3 py-1.5 md:px-4 md:py-2">
      <div className="max-w-3xl">
        <div className="flex items-start gap-2.5">
          <div className="mt-1 shrink-0 text-emerald-400">
            <Bot size={15} />
          </div>
          <div className="min-w-0 flex-1 space-y-1.5">
            <div className="whitespace-pre-wrap text-sm leading-7 text-zinc-300">
              {inlineTaskMetadata ? (
                <>
                  {inlineTaskMetadata.before && <div className="mb-1 whitespace-pre-wrap">{inlineTaskMetadata.before}</div>}
                  <button
                    onClick={() => setTaskDetailsExpanded((current) => !current)}
                    className="flex w-full items-center gap-1.5 text-left text-sm text-zinc-300 transition-colors hover:text-zinc-100"
                  >
                    {taskDetailsExpanded ? <ChevronDown size={12} className="shrink-0 text-zinc-500" /> : <ChevronRight size={12} className="shrink-0 text-zinc-500" />}
                    <span className="whitespace-pre-wrap">{inlineTaskMetadata.startLine}</span>
                  </button>
                  {taskDetailsExpanded && (
                    <pre className="mt-2 overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-900/60 p-3 text-xs leading-6 text-zinc-400">
                      {inlineTaskMetadata.metadata}
                    </pre>
                  )}
                  {inlineTaskMetadata.after && <div className="mt-2 whitespace-pre-wrap">{inlineTaskMetadata.after}</div>}
                </>
              ) : (
                <>
                  {message.content || (message.streaming && <Loader2 size={16} className="animate-spin inline" />)}
                  {message.content && message.streaming && <Loader2 size={14} className="ml-2 inline animate-spin text-zinc-500" />}
                </>
              )}
            </div>

            {!message.streaming && (messageCost || messageTokens) && (
              <div className="flex flex-wrap gap-3 text-[11px] text-zinc-500">
                {messageCost && <span>Cost {messageCost} credits</span>}
                {messageTokens && <span>{messageTokens} tokens</span>}
              </div>
            )}

            {message.councilData && (
              <CouncilResponses
                councilData={message.councilData}
                councilExpanded={councilExpanded}
                onToggle={() => setCouncilExpanded((current) => !current)}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function UserMessageRenderer({ message, onEdit }: { message: ChatMessage; onEdit?: (message: ChatMessage) => void }) {
  return (
    <div className="flex flex-row-reverse gap-2 px-3 py-2 md:gap-3 md:px-4 md:py-3">
      <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-blue-600 text-sm text-white md:h-8 md:w-8">
        <User size={16} />
      </div>

      <div className="max-w-[88%] space-y-2 md:max-w-[85%]">
        <div className="whitespace-pre-wrap break-words rounded-2xl rounded-tr-sm bg-blue-600 px-3 py-2.5 text-sm leading-relaxed text-white md:px-4 md:py-3">
          {message.content ? (
            <>
              {message.content}
              {message.streaming && <Loader2 size={14} className="ml-2 inline animate-spin align-text-bottom" />}
            </>
          ) : (
            message.streaming && <Loader2 size={16} className="animate-spin inline" />
          )}
        </div>

        {message.branchable === false && message.branchBlockReason && !message.streaming ? (
          <div className="text-xs text-zinc-500">{message.branchBlockReason}</div>
        ) : null}

        {onEdit && !message.streaming && message.branchable !== false && (
          <button
            onClick={() => onEdit(message)}
            className="inline-flex items-center gap-1 self-end text-xs text-zinc-400 transition-colors hover:text-zinc-100"
          >
            <Pencil size={12} />
            Edit and branch
          </button>
        )}
      </div>
    </div>
  );
}