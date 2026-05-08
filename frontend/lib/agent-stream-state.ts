import type { AgentStreamUpdate, ConversationEventView } from "./api";
import { summarizeAgentEvent } from "./agent-event-presenter";
import type { AgentStatus, ChatMessage } from "./chat-state-types";

const LIVE_AGENT_MESSAGE_PREFIX = "live-agent";

function isLowSignalToolIntentSummary(content: string): boolean {
  return /^\s*use tool\s+[a-z0-9_:-]+\s*[.!]?\s*$/i.test(content);
}

function isLiveAgentStreamMessage(message: ChatMessage): boolean {
  return message.id.startsWith(`${LIVE_AGENT_MESSAGE_PREFIX}:`);
}

function buildLiveAgentThoughtMessageId(runId: string, step: number): string {
  return `${LIVE_AGENT_MESSAGE_PREFIX}:thought:${runId}:${step}`;
}

function compareLiveAgentMessages(left: ChatMessage, right: ChatMessage): number {
  const stepDiff = (left.step ?? 0) - (right.step ?? 0);
  if (stepDiff !== 0) {
    return stepDiff;
  }
  const leftRank = left.kind === "status" ? 0 : 1;
  const rightRank = right.kind === "status" ? 0 : 1;
  return leftRank - rightRank;
}

function insertBeforeAssistant(
  messages: ChatMessage[],
  additions: ChatMessage[],
  assistantMessageId: string | null,
): ChatMessage[] {
  if (!additions.length) {
    return messages;
  }

  const assistantIndex = assistantMessageId
    ? messages.findIndex((message) => message.id === assistantMessageId)
    : -1;

  if (assistantIndex === -1) {
    return [...messages, ...additions];
  }

  return [
    ...messages.slice(0, assistantIndex),
    ...additions,
    ...messages.slice(assistantIndex),
  ];
}

export function clearLiveAgentStreamMessages(currentMessages: ChatMessage[], runId?: string | null): ChatMessage[] {
  return currentMessages.filter((message) => !(
    isLiveAgentStreamMessage(message)
    && (!runId || message.runId === runId)
  ));
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
      return {
        title: "Continuing",
        content: "Finished the last tool call. Continuing with the run.",
        tone: "info",
        runId: event.run_id,
        active: true,
      };
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
  if (update.kind !== "progress") {
    return null;
  }
  const content = update.content ?? "";
  if (!content.trim() || isLowSignalToolIntentSummary(content)) {
    return null;
  }
  return {
    title: update.step ? `Step ${update.step}` : "Progress",
    content,
    tone: "info",
    runId: update.run_id,
    active: true,
  };
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
  if (status.title === "Progress" || status.title.startsWith("Step ")) {
    return 3;
  }
  if (status.title === "Using a tool") {
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

export function upsertLiveAgentStreamMessages(
  currentMessages: ChatMessage[],
  update: AgentStreamUpdate,
  assistantMessageId: string | null,
): ChatMessage[] {
  const runId = update.run_id;
  const stepTitle = update.step ? `Step ${update.step}` : "Progress";
  const progressId = `${LIVE_AGENT_MESSAGE_PREFIX}:progress:${runId}:${update.step}`;
  const thoughtId = buildLiveAgentThoughtMessageId(runId, update.step);
  const liveMessagesById = new Map(
    currentMessages
      .filter((message) => message.runId === runId && isLiveAgentStreamMessage(message))
      .map((message) => [message.id, message] as const)
  );
  const existingProgress = liveMessagesById.get(progressId);
  const existingThought = liveMessagesById.get(thoughtId);

  if (update.kind === "thought_delta") {
    const delta = update.delta ?? "";
    liveMessagesById.set(thoughtId, {
      id: thoughtId,
      runId,
      step: update.step,
      role: "assistant",
      kind: "thought",
      title: stepTitle,
      label: "Thinking",
      content: `${existingThought?.content ?? ""}${delta}`,
      tone: "default",
      streaming: true,
      branchable: false,
    });
  }

  if (update.kind === "progress") {
    const content = update.content ?? existingProgress?.content ?? "";
    if (isLowSignalToolIntentSummary(content)) {
      liveMessagesById.delete(progressId);
    } else {
      liveMessagesById.set(progressId, {
        id: progressId,
        runId,
        step: update.step,
        role: "assistant",
        kind: "status",
        title: stepTitle,
        label: "Progress",
        content,
        tone: "info",
        streaming: false,
        branchable: false,
      });
    }
  }

  if (update.kind === "thought_done") {
    const content = update.content ?? existingThought?.content ?? "";
    if (isLowSignalToolIntentSummary(content)) {
      liveMessagesById.delete(thoughtId);
    } else {
      liveMessagesById.set(thoughtId, {
        id: thoughtId,
        runId,
        step: update.step,
        role: "assistant",
        kind: "thought",
        title: stepTitle,
        label: "Thinking",
        content,
        tone: "default",
        streaming: false,
        branchable: false,
      });
    }
  }

  const preservedMessages = currentMessages.filter((message) => !(
    message.runId === runId && isLiveAgentStreamMessage(message)
  ));
  const orderedLiveMessages = Array.from(liveMessagesById.values()).sort(compareLiveAgentMessages);
  return insertBeforeAssistant(preservedMessages, orderedLiveMessages, assistantMessageId);
}

export function mergeLoadedMessages(currentMessages: ChatMessage[], loadedMessages: ChatMessage[]): ChatMessage[] {
  const streamingMessages = currentMessages.filter((message) => message.streaming);
  if (streamingMessages.length === 0) {
    return loadedMessages;
  }

  if (loadedMessages.length === 0) {
    return currentMessages;
  }

  const hasPersistedAssistantMessage = loadedMessages.some((message) => message.role === "assistant");
  if (hasPersistedAssistantMessage) {
    return loadedMessages;
  }

  const loadedMessageKeys = new Set(loadedMessages.map((message) => `${message.role}:${message.content}`));
  const pendingMessages = streamingMessages.filter((message) => !loadedMessageKeys.has(`${message.role}:${message.content}`));
  return pendingMessages.length > 0 ? [...loadedMessages, ...pendingMessages] : loadedMessages;
}

export function mergeStreamEventMessages(
  currentMessages: ChatMessage[],
  eventMessages: ChatMessage[],
  runId: string,
  assistantMessageId: string | null,
): ChatMessage[] {
  const preservedMessages = currentMessages.filter((message) => !(
    message.runId === runId && (message.kind ?? "message") !== "message" && !isLiveAgentStreamMessage(message)
  ));

  if (!eventMessages.length) {
    return preservedMessages;
  }

  const assistantIndex = assistantMessageId
    ? preservedMessages.findIndex((message) => message.id === assistantMessageId)
    : -1;

  if (assistantIndex === -1) {
    return [...preservedMessages, ...eventMessages];
  }

  return [
    ...preservedMessages.slice(0, assistantIndex),
    ...eventMessages,
    ...preservedMessages.slice(assistantIndex),
  ];
}