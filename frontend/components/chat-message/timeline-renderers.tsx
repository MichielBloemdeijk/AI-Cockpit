"use client";

import { useMemo, useState } from "react";
import { AlertTriangle, Bot, CheckCircle2, ChevronDown, ChevronRight, CircleHelp, Loader2, Sparkles, User, Wrench } from "lucide-react";
import clsx from "clsx";

import type { ChatMessage } from "../../lib/chat-state-types";

function toneClasses(message: ChatMessage) {
  if (message.kind === "tool_call" || message.kind === "tool_result") {
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
  return { icon: message.role === "system" ? "text-zinc-500" : "text-emerald-400", text: "text-zinc-200" };
}

function messageIcon(message: ChatMessage) {
  if (message.streaming && message.role !== "user") {
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
      return message.role === "system" ? <Sparkles size={15} /> : <Bot size={15} />;
  }
}

function timestampLabel(createdAt?: string | null): string | null {
  if (!createdAt) {
    return null;
  }
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
    month: "short",
    day: "numeric",
  }).format(new Date(createdAt));
}

function TimelineShell({
  message,
  children,
}: {
  message: ChatMessage;
  children: React.ReactNode;
}) {
  const tones = toneClasses(message);

  return (
    <div className="px-3 py-1 md:px-4 md:py-1.5">
      <div className="max-w-3xl">
        <div className="flex items-start gap-2.5">
          <div className={clsx("mt-1 shrink-0", tones.icon)}>{messageIcon(message)}</div>
          <div className="min-w-0 flex-1">{children}</div>
        </div>
      </div>
    </div>
  );
}

export function ThoughtMessageRenderer({ message }: { message: ChatMessage }) {
  const [expanded, setExpanded] = useState(false);
  const preview = useMemo(() => message.content.replace(/\s+/g, " ").trim(), [message.content]);
  const tones = toneClasses(message);

  return (
    <TimelineShell message={message}>
      <button
        onClick={() => setExpanded((current) => !current)}
        className={clsx(
          "flex min-w-0 w-full items-center gap-1.5 text-left text-sm leading-6 transition-colors cursor-pointer hover:text-zinc-100",
          tones.text,
        )}
      >
        {expanded ? <ChevronDown size={12} className="shrink-0 text-zinc-500" /> : <ChevronRight size={12} className="shrink-0 text-zinc-500" />}
        <span className="font-medium text-zinc-200">{message.title ?? (message.streaming ? "Thinking" : "Thought")}</span>
        {!expanded && preview && <span className="min-w-0 truncate text-zinc-500">{preview}</span>}
      </button>
      {expanded && message.content && (
        <div className="mt-1 whitespace-pre-wrap text-sm leading-6 text-zinc-300">{message.content}</div>
      )}
    </TimelineShell>
  );
}

function ToolMessageRenderer({ message }: { message: ChatMessage }) {
  const [expanded, setExpanded] = useState(false);
  const [showMore, setShowMore] = useState(false);
  const previewLimit = 1200;
  const debugContent = message.code && expanded
    ? (message.code.length > previewLimit && !showMore
      ? `${message.code.slice(0, previewLimit).trimEnd()}\n...`
      : message.code)
    : null;
  const tones = toneClasses(message);

  return (
    <TimelineShell message={message}>
      <button
        onClick={() => {
          setExpanded((current) => !current);
          if (expanded) setShowMore(false);
        }}
        className={clsx(
          "flex min-w-0 w-full items-center gap-1.5 text-left text-sm leading-6 transition-colors cursor-pointer hover:text-zinc-300",
          tones.text,
        )}
      >
        {message.title && <span className="shrink-0 font-medium text-zinc-400">{message.title}</span>}
        {message.content && <span className="text-zinc-600">-</span>}
        {message.content && <span className="min-w-0 truncate text-zinc-500">{message.content}</span>}
        {expanded ? <ChevronDown size={12} className="ml-auto shrink-0 text-zinc-600" /> : <ChevronRight size={12} className="ml-auto shrink-0 text-zinc-700" />}
      </button>
      {debugContent && (
        <div className="mt-2 rounded-xl border border-zinc-800 bg-zinc-950/80 p-3">
          <pre className="overflow-x-auto text-xs leading-6 text-zinc-300">{debugContent}</pre>
          {message.code && message.code.length > previewLimit && (
            <button
              onClick={() => setShowMore((current) => !current)}
              className="mt-2 text-xs text-zinc-500 transition-colors hover:text-zinc-300"
            >
              {showMore ? "Show less" : "Show more"}
            </button>
          )}
        </div>
      )}
    </TimelineShell>
  );
}

export function ToolCallMessageRenderer({ message }: { message: ChatMessage }) {
  return <ToolMessageRenderer message={message} />;
}

export function ToolResultMessageRenderer({ message }: { message: ChatMessage }) {
  return <ToolMessageRenderer message={message} />;
}

export function StatusMessageRenderer({ message }: { message: ChatMessage }) {
  const [expanded, setExpanded] = useState(false);
  const tones = toneClasses(message);
  const isTaskStartedStatusRow = typeof message.title === "string" && /started app task/i.test(message.title) && Boolean(message.code);

  return (
    <TimelineShell message={message}>
      {isTaskStartedStatusRow ? (
        <>
          <button
            onClick={() => setExpanded((current) => !current)}
            className={clsx(
              "flex min-w-0 w-full items-center gap-1.5 text-left text-sm leading-6 transition-colors cursor-pointer hover:text-zinc-100",
              tones.text,
            )}
          >
            {expanded ? <ChevronDown size={12} className="shrink-0 text-zinc-500" /> : <ChevronRight size={12} className="shrink-0 text-zinc-500" />}
            {message.title && <span className="font-medium text-zinc-200">{message.title}</span>}
            {message.title && message.content && <span className="text-zinc-600">·</span>}
            {message.content && <span className="whitespace-pre-wrap">{message.content}</span>}
          </button>
          {expanded && message.code && (
            <div className="mt-2 rounded-xl border border-zinc-800 bg-zinc-950/80 p-3">
              <pre className="overflow-x-auto text-xs leading-6 text-zinc-300">{message.code}</pre>
            </div>
          )}
        </>
      ) : (
        <div className={clsx("flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-sm leading-6", tones.text)}>
          {message.title && <span className="font-medium text-zinc-200">{message.title}</span>}
          {message.title && message.content && <span className="text-zinc-600">·</span>}
          {message.content && <span className="whitespace-pre-wrap">{message.content}</span>}
          {message.badges?.map((badge) => (
            <span key={badge} className="text-xs text-zinc-500">· {badge}</span>
          ))}
        </div>
      )}
    </TimelineShell>
  );
}

export function GenericTimelineMessageRenderer({ message }: { message: ChatMessage }) {
  const [debugExpanded, setDebugExpanded] = useState(false);
  const [showMore, setShowMore] = useState(false);
  const tones = toneClasses(message);
  const previewLimit = 1200;
  const debugContent = message.code && debugExpanded
    ? (message.code.length > previewLimit && !showMore
      ? `${message.code.slice(0, previewLimit).trimEnd()}\n...`
      : message.code)
    : null;
  const timestamp = timestampLabel(message.createdAt);

  return (
    <TimelineShell message={message}>
      <div className={clsx("flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-sm leading-6", tones.text)}>
        {message.title && <span className="font-medium text-zinc-200">{message.title}</span>}
        {message.title && message.content && <span className="text-zinc-600">·</span>}
        {message.content && <span className="whitespace-pre-wrap">{message.content}</span>}
        {message.badges?.map((badge) => (
          <span key={badge} className="text-xs text-zinc-500">· {badge}</span>
        ))}
      </div>

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

      {message.code && (
        <div className="mt-1.5">
          <button
            onClick={() => {
              setDebugExpanded((current) => !current);
              if (debugExpanded) setShowMore(false);
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
          {message.code && message.code.length > previewLimit && (
            <button
              onClick={() => setShowMore((current) => !current)}
              className="mt-2 text-xs text-zinc-500 transition-colors hover:text-zinc-300"
            >
              {showMore ? "Show less" : "Show more"}
            </button>
          )}
        </div>
      )}
    </TimelineShell>
  );
}