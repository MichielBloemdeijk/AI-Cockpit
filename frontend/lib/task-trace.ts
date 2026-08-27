import type { ChatMessage } from "./chat-state-types";
import type { TaskEvent } from "./api";

function text(payload: Record<string, unknown> | null, key: string): string {
  const value = payload?.[key];
  return typeof value === "string" ? value : "";
}

export function taskEventToChatMessage(event: TaskEvent): ChatMessage {
  const eventName = event.event_type.replace(/^task\./, "").replaceAll("_", " ");
  const summary = text(event.payload, "summary");
  const question = text(event.payload, "question");
  const answer = text(event.payload, "answer");
  const failed = event.event_type === "task.failed" || event.event_type === "task.interrupted";
  const terminal = ["task.completed", "task.failed", "task.interrupted", "task.cancelled"].includes(event.event_type);

  return {
    id: `task-event:${event.id}`,
    runId: event.attempt_id,
    role: event.event_type === "task.question.answered" ? "user" : "assistant",
    kind: event.event_type === "task.requires_input"
      ? "question"
      : event.event_type === "task.question.answered"
        ? "answer"
        : terminal
          ? (failed ? "error" : "summary")
          : "status",
    title: eventName,
    label: `Task event #${event.sequence}`,
    content: question || answer || summary || text(event.payload, "goal") || eventName,
    createdAt: event.created_at,
    status: text(event.payload, "status") || null,
    tone: failed ? "error" : terminal ? "success" : event.event_type === "task.requires_input" ? "warning" : "info",
    badges: event.attempt_id ? [`attempt ${event.attempt_id.slice(0, 8)}`] : undefined,
    branchable: false,
  };
}
