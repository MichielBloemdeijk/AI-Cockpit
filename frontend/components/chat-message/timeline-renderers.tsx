"use client";

import { AlertTriangle, Bot, CheckCircle2, CircleHelp, Loader2, Sparkles, User } from "lucide-react";
import clsx from "clsx";

import type { ChatMessage } from "../../lib/conversations/state-types";
import { MarkdownContent } from "./MarkdownContent";

/** Rows of a task's trace (status, question, answer, summary, error). */

function toneClasses(message: ChatMessage) {
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
  switch (message.kind) {
    case "question":
      return <CircleHelp size={15} />;
    case "summary":
      return <CheckCircle2 size={15} />;
    case "error":
      return <AlertTriangle size={15} />;
    case "answer":
      return <User size={15} />;
    default:
      if (message.streaming && message.role !== "user") {
        return <Loader2 size={15} className="animate-spin" />;
      }
      return message.role === "system" ? <Sparkles size={15} /> : <Bot size={15} />;
  }
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

export function StatusMessageRenderer({ message }: { message: ChatMessage }) {
  const tones = toneClasses(message);

  return (
    <TimelineShell message={message}>
      <div className={clsx("text-sm leading-6", tones.text)}>
        {message.title && <span className="font-medium text-zinc-200">{message.title}</span>}
        {message.title && message.content && <span className="text-zinc-600">·</span>}
        {message.content && <MarkdownContent content={message.content} className={message.title ? "mt-1" : undefined} />}
        {message.badges?.map((badge) => (
          <span key={badge} className="text-xs text-zinc-500">· {badge}</span>
        ))}
      </div>
    </TimelineShell>
  );
}

export function GenericTimelineMessageRenderer({ message }: { message: ChatMessage }) {
  const tones = toneClasses(message);

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
    </TimelineShell>
  );
}
