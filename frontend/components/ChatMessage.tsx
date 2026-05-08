"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Bot, User, Loader2, Pencil, Sparkles, Wrench, CircleHelp, CheckCircle2, AlertTriangle } from "lucide-react";
import clsx from "clsx";
import type { ChatMessage as ChatMsg } from "@/lib/hooks";
import { formatTokenCount, formatUsageCost } from "@/lib/usage";

interface Props {
  message: ChatMsg;
  onEdit?: (message: ChatMsg) => void;
}

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

export function ChatMessage({ message, onEdit }: Props) {
  const [councilExpanded, setCouncilExpanded] = useState(false);
  const [debugExpanded, setDebugExpanded] = useState(false);
  const [debugShowMore, setDebugShowMore] = useState(false);
  const [taskDetailsExpanded, setTaskDetailsExpanded] = useState(false);
  const [thoughtExpanded, setThoughtExpanded] = useState(false);
  const isUser = message.role === "user";
  const isSystem = message.role === "system";
  const isPlainMessage = (message.kind ?? "message") === "message";
  const messageCost = formatUsageCost(message.usage?.cost);
  const messageTokens = formatTokenCount(message.usage?.total_tokens);
  const councilTotalCost = formatUsageCost(message.councilData?.total_usage?.cost);
  const debugPreviewLimit = 1200;
  const hasLongDebug = (message.code?.length ?? 0) > debugPreviewLimit;
  const debugContent = debugExpanded && message.code
    ? hasLongDebug && !debugShowMore
      ? `${message.code.slice(0, debugPreviewLimit).trimEnd()}\n...`
      : message.code
    : null;
  const thoughtPreview = message.kind === "thought"
    ? message.content.replace(/\s+/g, " ").trim()
    : "";
  const isCompactToolRow = message.kind === "tool_call" || message.kind === "tool_result";
  const isTaskStartedStatusRow = message.kind === "status"
    && typeof message.title === "string"
    && /started app task/i.test(message.title)
    && Boolean(message.code);
  const inlineTaskMetadata = !isUser && isPlainMessage && message.content
    ? splitTaskMetadata(message.content)
    : null;

  const timestamp = message.createdAt
    ? new Intl.DateTimeFormat(undefined, {
        hour: "numeric",
        minute: "2-digit",
        month: "short",
        day: "numeric",
      }).format(new Date(message.createdAt))
    : null;

  const streamToneClasses = (() => {
    if (isCompactToolRow) {
      return { icon: "text-zinc-500", text: "text-zinc-400" };
    }
    if (message.tone === "success") {
      return { icon: "text-emerald-500", text: "text-zinc-300" };
    }
    if (message.tone === "warning") {
      return { icon: "text-amber-400", text: "text-zinc-200" };
    }
    if (message.tone === "error") {
      return { icon: "text-rose-400", text: "text-zinc-200" };
    }
    if (message.tone === "info") {
      return { icon: "text-sky-400", text: "text-zinc-300" };
    }
    return { icon: isSystem ? "text-zinc-500" : "text-emerald-400", text: "text-zinc-200" };
  })();

  const icon = (() => {
    if (message.streaming && !isUser) {
      return <Loader2 size={15} className="animate-spin" />;
    }
    switch (message.kind) {
      case "question":
        return <CircleHelp size={15} />;
      case "tool_call":
      case "tool_result":
        return <Wrench size={15} />;
      case "summary":
        return <CheckCircle2 size={15} />;
      case "error":
        return <AlertTriangle size={15} />;
      case "answer":
        return <User size={15} />;
      default:
        return isSystem ? <Sparkles size={15} /> : isUser ? <User size={15} /> : <Bot size={15} />;
    }
  })();

  if (!isPlainMessage) {
    return (
      <div className="px-3 py-1 md:px-4 md:py-1.5">
        <div className="max-w-3xl">
          <div className="flex items-start gap-2.5">
            <div className={clsx("mt-1 shrink-0", streamToneClasses.icon)}>{icon}</div>
            <div className="min-w-0 flex-1">
              {(message.title || message.content || (message.badges?.length && !isCompactToolRow)) && (
                isCompactToolRow && message.code ? (
                  <button
                    onClick={() => {
                      setDebugExpanded((current) => !current);
                      if (debugExpanded) setDebugShowMore(false);
                    }}
                    className={clsx(
                      "flex min-w-0 w-full items-center gap-1.5 text-left text-sm leading-6 transition-colors",
                      streamToneClasses.text,
                      "cursor-pointer hover:text-zinc-300",
                    )}
                  >
                    {message.title && <span className="shrink-0 font-medium text-zinc-400">{message.title}</span>}
                    {message.content && <span className="text-zinc-600">-</span>}
                    {message.content && <span className="min-w-0 truncate text-zinc-500">{message.content}</span>}
                    {debugExpanded ? <ChevronDown size={12} className="ml-auto shrink-0 text-zinc-600" /> : <ChevronRight size={12} className="ml-auto shrink-0 text-zinc-700" />}
                  </button>
                ) : message.kind === "thought" ? (
                  <div>
                    <button
                      onClick={() => setThoughtExpanded((current) => !current)}
                      className={clsx(
                        "flex min-w-0 w-full items-center gap-1.5 text-left text-sm leading-6 transition-colors",
                        streamToneClasses.text,
                        "cursor-pointer hover:text-zinc-100",
                      )}
                    >
                      {thoughtExpanded ? <ChevronDown size={12} className="shrink-0 text-zinc-500" /> : <ChevronRight size={12} className="shrink-0 text-zinc-500" />}
                      <span className="font-medium text-zinc-200">{message.title ?? (message.streaming ? "Thinking" : "Thought")}</span>
                      {!thoughtExpanded && thoughtPreview && <span className="min-w-0 truncate text-zinc-500">{thoughtPreview}</span>}
                    </button>
                    {thoughtExpanded && message.content && (
                      <div className="mt-1 whitespace-pre-wrap text-sm leading-6 text-zinc-300">{message.content}</div>
                    )}
                  </div>
                ) : isTaskStartedStatusRow ? (
                  <button
                    onClick={() => {
                      setDebugExpanded((current) => !current);
                      if (debugExpanded) setDebugShowMore(false);
                    }}
                    className={clsx(
                      "flex min-w-0 w-full items-center gap-1.5 text-left text-sm leading-6 transition-colors",
                      streamToneClasses.text,
                      "cursor-pointer hover:text-zinc-100",
                    )}
                  >
                    {debugExpanded ? <ChevronDown size={12} className="shrink-0 text-zinc-500" /> : <ChevronRight size={12} className="shrink-0 text-zinc-500" />}
                    {message.title && <span className="font-medium text-zinc-200">{message.title}</span>}
                    {message.title && message.content && <span className="text-zinc-600">·</span>}
                    {message.content && <span className="whitespace-pre-wrap">{message.content}</span>}
                  </button>
                ) : (
                  <div className={clsx(
                    "text-sm leading-6",
                    isCompactToolRow ? "flex min-w-0 items-center gap-1.5" : "flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5",
                    streamToneClasses.text,
                  )}>
                    {isCompactToolRow ? (
                      <>
                        {message.title && <span className="shrink-0 font-medium text-zinc-400">{message.title}</span>}
                        {message.content && <span className="text-zinc-600">-</span>}
                        {message.content && <span className="min-w-0 truncate text-zinc-500">{message.content}</span>}
                      </>
                    ) : (
                      <>
                        {message.title && <span className="font-medium text-zinc-200">{message.title}</span>}
                        {message.title && message.content && <span className="text-zinc-600">·</span>}
                        {message.content && <span className="whitespace-pre-wrap">{message.content}</span>}
                        {message.badges?.map((badge) => (
                          <span key={badge} className="text-xs text-zinc-500">· {badge}</span>
                        ))}
                      </>
                    )}
                  </div>
                )
              )}

              {message.sections?.length ? (
                <div className="mt-1.5 space-y-2 border-l border-zinc-800/80 pl-3">
                  {message.sections.map((section) => (
                    <div key={section.title}>
                      <div className="text-[11px] uppercase tracking-[0.16em] text-zinc-500">{section.title}</div>
                      <ul className="mt-1 space-y-0.5 text-sm leading-6 text-zinc-300">
                        {section.items.map((entry, index) => (
                          <li key={`${section.title}-${index}`}>{entry}</li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              ) : null}

              {!isCompactToolRow && message.code && !isTaskStartedStatusRow && (
                <div className="mt-1.5">
                  <button
                    onClick={() => {
                      setDebugExpanded((current) => !current);
                      if (debugExpanded) setDebugShowMore(false);
                    }}
                    className="inline-flex items-center gap-1 text-xs text-zinc-500 transition-colors hover:text-zinc-300"
                  >
                    {debugExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    Debug
                  </button>
                </div>
              )}

              {debugContent && (
                <div className="mt-2 rounded-xl border border-zinc-800 bg-zinc-950/80 p-3">
                  {timestamp && <div className="mb-2 text-[11px] text-zinc-500">{timestamp}</div>}
                  <pre className="overflow-x-auto text-xs leading-6 text-zinc-300">{debugContent}</pre>
                  {hasLongDebug && (
                    <button
                      onClick={() => setDebugShowMore((current) => !current)}
                      className="mt-2 text-xs text-zinc-500 transition-colors hover:text-zinc-300"
                    >
                      {debugShowMore ? "Show less" : "Show more"}
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (!isUser) {
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
                <div className="w-full">
                  <button
                    onClick={() => setCouncilExpanded((v) => !v)}
                    className="flex items-center gap-1 text-xs text-zinc-500 transition-colors hover:text-zinc-300"
                  >
                    {councilExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    {message.councilData.model_responses.length} model responses{councilTotalCost ? ` · ${councilTotalCost} credits total` : ""}
                  </button>

                  {councilExpanded && (
                    <div className="mt-2 space-y-2 border-l border-zinc-800/80 pl-3">
                      {message.councilData.model_responses.map((r) => (
                        <div key={r.model}>
                          <div className="text-xs font-mono text-zinc-500">{r.model}</div>
                          {(r.usage?.cost || r.usage?.total_tokens) && (
                            <div className="mt-1 flex flex-wrap gap-3 text-[11px] text-zinc-500">
                              {typeof r.usage?.cost === "number" && <span>Cost {formatUsageCost(r.usage.cost)} credits</span>}
                              {typeof r.usage?.total_tokens === "number" && <span>{formatTokenCount(r.usage.total_tokens)} tokens</span>}
                            </div>
                          )}
                          {r.error ? (
                            <div className="mt-1 text-xs text-red-400">{r.error}</div>
                          ) : (
                            <div className="mt-1 whitespace-pre-wrap text-xs text-zinc-300">{r.content}</div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={clsx("flex gap-2 px-3 py-2 md:gap-3 md:px-4 md:py-3", isUser ? "flex-row-reverse" : "flex-row")}>
      {/* Avatar */}
      <div
        className={clsx(
          "flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full text-sm text-white md:h-8 md:w-8",
          isUser ? "bg-blue-600" : "bg-emerald-600"
        )}
      >
        {isUser ? <User size={16} /> : <Bot size={16} />}
      </div>

      {/* Bubble */}
      <div className={clsx("max-w-[88%] space-y-2 md:max-w-[85%]", isUser ? "items-end" : "items-start")}>
        <div
          className={clsx(
            "whitespace-pre-wrap break-words rounded-2xl px-3 py-2.5 text-sm leading-relaxed md:px-4 md:py-3",
            isUser
              ? "bg-blue-600 text-white rounded-tr-sm"
              : "bg-zinc-800 text-zinc-100 rounded-tl-sm"
          )}
        >
          {message.content ? (
            <>
              {message.content}
              {message.streaming && <Loader2 size={14} className="ml-2 inline animate-spin align-text-bottom" />}
            </>
          ) : (
            message.streaming && <Loader2 size={16} className="animate-spin inline" />
          )}
        </div>

        {isUser && onEdit && !message.streaming && message.branchable !== false && (
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
