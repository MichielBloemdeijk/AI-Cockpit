"use client";

import type { ChatMessage as ChatMsg } from "@/lib/hooks";

import { AssistantMessageRenderer, UserMessageRenderer } from "./chat-message/plain-renderers";
import { GenericTimelineMessageRenderer, StatusMessageRenderer } from "./chat-message/timeline-renderers";

interface Props {
  message: ChatMsg;
  onEdit?: (message: ChatMsg) => void;
}

export function ChatMessage({ message, onEdit }: Props) {
  switch (message.kind ?? "message") {
    case "message":
      return message.role === "user"
        ? <UserMessageRenderer message={message} onEdit={onEdit} />
        : <AssistantMessageRenderer message={message} />;
    case "status":
      return <StatusMessageRenderer message={message} />;
    default:
      return <GenericTimelineMessageRenderer message={message} />;
  }
}
