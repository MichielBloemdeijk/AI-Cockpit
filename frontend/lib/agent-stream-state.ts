import type { AgentStreamUpdate, ConversationEventView } from "./api";
import { summarizeAgentEvent } from "./agent-event-presenter";
import type { AgentStatus } from "./chat-state-types";

function isLowSignalToolIntentSummary(content: string): boolean {
  return /^\s*use tool\s+[a-z0-9_:-]+\s*[.!]?\s*$/i.test(content);
}

export function buildFallbackAgentStatus(activeConversationId: string | null, runId?: string | null): AgentStatus {
  return {
    title: "In progress",
    content: !activeConversationId
      ? "Working on it. Updates will appear here as the run progresses."
      : "Working on it.",
    tone: "info",
    runId,
    active: true,
  };
}

export function buildAgentStatusFromEvent(event: ConversationEventView): AgentStatus | null {
  switch (event.event_type) {
    case "agent.plan.created":
      return {
        title: "Planning",
        content: "Plan ready. Starting execution.",
        tone: "info",
        runId: event.run_id,
        active: true,
      };
    case "agent.progress.summary": {
      const content = summarizeAgentEvent(event.event_type, event.payload_json) ?? "";
      if (!content || isLowSignalToolIntentSummary(content)) {
        return null;
      }
      return {
        title: "Progress",
        content,
        tone: "info",
        runId: event.run_id,
        active: true,
      };
    }
    case "agent.tool.called":
      return {
        title: "Using a tool",
        content: summarizeAgentEvent(event.event_type, event.payload_json) ?? "The agent is using a tool.",
        tone: "info",
        runId: event.run_id,
        active: true,
      };
    case "agent.tool.completed":
      return null;
    case "conversation.task.started":
      return {
        title: "Task started",
        content: "The agent started a longer task and is now working through it.",
        tone: "info",
        runId: event.run_id,
        active: true,
      };
    case "agent.question.asked":
      return {
        title: "Waiting for input",
        content: summarizeAgentEvent(event.event_type, event.payload_json) ?? "The agent is waiting for your input.",
        tone: "warning",
        runId: event.run_id,
        active: false,
      };
    case "agent.run.failed":
      return {
        title: "Run failed",
        content: summarizeAgentEvent(event.event_type, event.payload_json) ?? "The agent run failed.",
        tone: "error",
        runId: event.run_id,
        active: false,
      };
    default:
      return null;
  }
}

export function buildAgentStatusFromStreamUpdate(update: AgentStreamUpdate): AgentStatus | null {
  switch (update.kind) {
    case "progress": {
      const content = update.content ?? "";
      if (!content.trim() || isLowSignalToolIntentSummary(content)) {
        return null;
      }
      return {
        title: "Progress",
        content,
        tone: "info",
        runId: update.run_id,
        active: true,
      };
    }
    case "tool_started":
    case "tool_progress": {
      const content = update.content ?? "";
      if (!content.trim() || isLowSignalToolIntentSummary(content)) {
        return null;
      }
      return {
        title: "Using a tool",
        content,
        tone: "info",
        runId: update.run_id,
        active: true,
      };
    }
    default:
      return null;
  }
}

export function findLatestAgentStatus(events: ConversationEventView[]): AgentStatus | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const status = buildAgentStatusFromEvent(events[index]);
    if (status) {
      return status;
    }
  }
  return null;
}

function agentStatusPriority(status: AgentStatus): number {
  if (status.tone === "error") {
    return 5;
  }
  if (status.title === "Waiting for input") {
    return 4;
  }
  if (status.title === "Using a tool") {
    return 3;
  }
  if (status.title === "Progress") {
    return 2;
  }
  return 1;
}

export function mergeAgentStatus(current: AgentStatus | null, next: AgentStatus): AgentStatus {
  if (!current) {
    return next;
  }
  if (current.runId && next.runId && current.runId !== next.runId) {
    return next;
  }
  return agentStatusPriority(next) >= agentStatusPriority(current) ? next : current;
}
