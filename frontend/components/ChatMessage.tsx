"use client";

import type { ChatMessage as ChatMsg } from "@/lib/hooks";

import { AssistantMessageRenderer, UserMessageRenderer } from "./chat-message/plain-renderers";
import {
  BackgroundTaskMessageRenderer,
  GenericTimelineMessageRenderer,
  StatusMessageRenderer,
  ThoughtMessageRenderer,
  ToolActivityMessageRenderer,
} from "./chat-message/timeline-renderers";

interface Props {
  message: ChatMsg;
  onEdit?: (message: ChatMsg) => void;
}

export function ChatMessage({ message, onEdit }: Props) {
  const isUser = message.role === "user";
  const isPlainMessage = (message.kind ?? "message") === "message";
  if (isPlainMessage) {
    return isUser
      ? <UserMessageRenderer message={message} onEdit={onEdit} />
      : <AssistantMessageRenderer message={message} />;
  }

  switch (message.kind) {
    case "task":
      return <BackgroundTaskMessageRenderer message={message} />;
    case "thought":
      return <ThoughtMessageRenderer message={message} />;
    case "tool_call":
    case "tool_result":
      return <ToolActivityMessageRenderer message={message} />;
    case "status":
      return <StatusMessageRenderer message={message} />;
    default:
      return <GenericTimelineMessageRenderer message={message} />;
  }
}
