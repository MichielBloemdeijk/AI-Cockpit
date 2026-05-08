import type {
  ConversationArtifactView,
  ConversationDetail,
  ConversationEventView,
  CouncilResponse,
  UsageStats,
} from "./api";
import { formatAgentEventDetails, humanizeToolName, summarizeAgentEvent, summarizeToolCall, summarizeToolResult } from "./agent-event-presenter";
import { mergeUsageStats, readUsageStats } from "./usage";

export interface ChatHistoryMessage {
  id: string;
  runId?: string | null;
  role: "user" | "assistant" | "system";
  kind?: "message" | "status" | "plan" | "thought" | "tool_call" | "tool_result" | "question" | "answer" | "summary" | "error";
  label?: string;
  title?: string;
  content: string;
  createdAt?: string | null;
  tone?: "default" | "info" | "success" | "warning" | "error";
  badges?: string[];
  code?: string;
  sections?: Array<{ title: string; items: string[] }>;
  branchable?: boolean;
  branchBlockReason?: string;
  councilData?: CouncilResponse;
  usage?: UsageStats;
}

interface TimelineEntry extends ChatHistoryMessage {
  sortTime: number;
  sortOrder: number;
}

function isRenderableChatMessage(
  message: ConversationDetail["messages"][number],
): message is ConversationDetail["messages"][number] & { role: "user" | "assistant" } {
  return message.role === "user" || message.role === "assistant";
}

function parseUsageByEventId(events: ConversationEventView[]): Map<string, UsageStats> {
  return new Map(
    events
      .map((event) => [event.id, readUsageStats(event.payload_json?.usage)] as const)
      .filter((entry): entry is readonly [string, UsageStats] => Boolean(entry[1]))
  );
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function buildPlanSections(payload: Record<string, unknown>): Array<{ title: string; items: string[] }> {
  return [
    { title: "Steps", items: asStringArray(payload.steps) },
    { title: "Open points", items: asStringArray(payload.open_questions) },
    { title: "Assumptions", items: asStringArray(payload.assumptions) },
  ].filter((section) => section.items.length > 0);
}

function buildRunSummarySections(payload: Record<string, unknown>): Array<{ title: string; items: string[] }> {
  const runSummary = asRecord(payload.run_summary);
  if (!runSummary) {
    return [];
  }

  return [
    { title: "Files changed", items: asStringArray(runSummary.changed_files) },
    { title: "Tools used", items: asStringArray(runSummary.tools_used) },
  ].filter((section) => section.items.length > 0);
}

function toSortTime(value: string | null | undefined): number {
  if (!value) {
    return Number.MAX_SAFE_INTEGER;
  }
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? Number.MAX_SAFE_INTEGER : parsed;
}

function isLowSignalToolIntentSummary(content: string): boolean {
  return /^\s*use tool\s+[a-z0-9_:-]+\s*[.!]?\s*$/i.test(content);
}

function buildTaskStartedContent(payload: Record<string, unknown> | null): { title: string; content: string; details?: string } {
  const task = asRecord(payload?.task);
  const app = asRecord(payload?.app);
  const taskTitle = typeof task?.title === "string" ? task.title.trim() : "";
  const routePath = typeof app?.route_path === "string" ? app.route_path.trim() : "";

  const title = app ? "Started app task" : "Started agent run";
  const content = app
    ? taskTitle && routePath
      ? `${taskTitle} for ${routePath}`
      : taskTitle || routePath || "The agent switched into a longer app-building run."
    : taskTitle || "The agent switched from direct reply mode into a longer execution loop.";

  const detailLines: string[] = [];
  const taskId = typeof task?.id === "string" ? task.id.trim() : "";
  if (taskId) {
    detailLines.push(`Task id: ${taskId}`);
  }
  const appRoot = typeof app?.frontend_root === "string" ? app.frontend_root.trim() : "";
  if (appRoot) {
    detailLines.push(`App root: ${appRoot}`);
  }
  const entryPage = typeof app?.frontend_entry_path === "string" ? app.frontend_entry_path.trim() : "";
  if (entryPage) {
    detailLines.push(`Entry page: ${entryPage}`);
  }
  const writeRoots = asStringArray(app?.allowed_write_roots);
  if (writeRoots.length > 0) {
    detailLines.push(`Allowed write roots: ${writeRoots.join(", ")}`);
  }

  return {
    title,
    content,
    details: detailLines.length > 0 ? detailLines.join("\n") : undefined,
  };
}

function buildEventTimelineEntries(events: ConversationEventView[]): TimelineEntry[] {
  const entries: TimelineEntry[] = [];
  const lastToolArgumentsByKey = new Map<string, unknown>();

  events.forEach((event) => {
    const payload = asRecord(event.payload_json);
    const step = typeof payload?.step === "number" ? payload.step : null;
    const toolName = typeof payload?.tool === "string" ? payload.tool : "tool";
    const toolKey = `${step ?? "na"}:${toolName}`;

    const pushEntry = (entry: Omit<TimelineEntry, "sortTime" | "sortOrder">) => {
      entries.push({
        ...entry,
        runId: entry.runId ?? event.run_id,
        createdAt: entry.createdAt ?? event.created_at,
        sortTime: toSortTime(entry.createdAt ?? event.created_at),
        sortOrder: event.sequence,
      });
    };

    switch (event.event_type) {
      case "conversation.task.started": {
        const taskStarted = buildTaskStartedContent(payload);
        pushEntry({
          id: `event:${event.id}`,
          role: "assistant",
          kind: "status",
          label: "Agent",
          title: taskStarted.title,
          content: taskStarted.content,
          code: taskStarted.details,
          tone: "info",
        });
        return;
      }
      case "conversation.task.start_failed": {
        const taskStart = asRecord(payload?.task_start);
        const error = typeof taskStart?.error === "string" ? taskStart.error.trim() : "Task startup failed before a run was created.";
        pushEntry({
          id: `event:${event.id}`,
          role: "assistant",
          kind: "status",
          label: "Agent",
          title: "Task start failed",
          content: "No new task was created.",
          code: `Task start status: failed\nReason: ${error}`,
          tone: "warning",
        });
        return;
      }
      case "agent.run.started":
        return;
      case "agent.plan.created":
        pushEntry({
          id: `event:${event.id}`,
          role: "assistant",
          kind: "plan",
          label: "Execution plan",
          title: "Plan ready",
          content: summarizeAgentEvent(event.event_type, payload) ?? "The agent prepared a plan.",
          tone: "info",
          sections: payload ? buildPlanSections(payload) : [],
        });
        return;
      case "agent.plan.feedback.skipped":
        pushEntry({
          id: `event:${event.id}`,
          role: "system",
          kind: "status",
          label: "Plan approval",
          title: "Started immediately",
          content: "Plan feedback was skipped and execution continued without pausing.",
          tone: "info",
        });
        return;
      case "agent.thought.summary": {
        const thought = summarizeAgentEvent(event.event_type, payload) ?? "";
        if (isLowSignalToolIntentSummary(thought)) {
          return;
        }
        pushEntry({
          id: `event:${event.id}`,
          role: "assistant",
          kind: "thought",
          label: "Thinking",
          title: step ? `Step ${step}` : "Thinking",
          content: thought,
          tone: "default",
        });
        return;
      }
      case "agent.progress.summary":
        {
          const summary = summarizeAgentEvent(event.event_type, payload) ?? (typeof payload?.summary === "string" ? payload.summary : "");
          if (!summary || isLowSignalToolIntentSummary(summary)) {
            return;
          }
          pushEntry({
            id: `event:${event.id}`,
            role: "assistant",
            kind: "status",
            label: "Progress",
            title: step ? `Step ${step}` : "Progress",
            content: summary,
            tone: "info",
          });
        }
        return;
      case "agent.tool.called":
        if (payload?.arguments !== undefined) {
          lastToolArgumentsByKey.set(toolKey, payload.arguments);
        }
        {
          // Emit a placeholder row; replace any existing placeholder with the same key.
          const existingIndex = entries.findIndex((entry) => entry.id === `toolrow:${toolKey}`);
          const callEntry: TimelineEntry = {
            id: `toolrow:${toolKey}`,
            role: "assistant",
            kind: "tool_call",
            label: "Tool call",
            title: humanizeToolName(toolName),
            content: summarizeToolCall(toolName, payload?.arguments),
            runId: event.run_id,
            createdAt: event.created_at,
            tone: "info",
            badges: [step ? `step ${step}` : ""].filter(Boolean),
            code: formatAgentEventDetails(payload?.arguments),
            sortTime: toSortTime(event.created_at),
            sortOrder: event.sequence,
          };
          if (existingIndex !== -1) {
            entries[existingIndex] = callEntry;
          } else {
            entries.push(callEntry);
          }
        }
        return;
      case "agent.tool.completed": {
        const ok = payload?.ok !== false;
        const resolvedArguments = payload?.arguments ?? lastToolArgumentsByKey.get(toolKey);
        // Replace the placeholder tool_call row (same id) with the result.
        const existingIndex = entries.findIndex((e) => e.id === `toolrow:${toolKey}`);
        const resultEntry: TimelineEntry = {
          id: `toolrow:${toolKey}`,
          role: "assistant",
          kind: "tool_result",
          label: ok ? "Tool result" : "Tool failure",
          title: humanizeToolName(toolName),
          content: summarizeToolResult(toolName, resolvedArguments, ok, payload?.output, payload?.error),
          runId: event.run_id,
          createdAt: event.created_at,
          tone: ok ? "success" : "error",
          badges: [step ? `step ${step}` : ""].filter(Boolean),
          code: formatAgentEventDetails(ok ? payload?.output : payload?.error),
          sortTime: toSortTime(event.created_at),
          sortOrder: event.sequence,
        };
        if (existingIndex !== -1) {
          entries[existingIndex] = resultEntry;
        } else {
          entries.push(resultEntry);
        }
        return;
      }
      case "agent.question.asked":
        pushEntry({
          id: `event:${event.id}`,
          role: "assistant",
          kind: "question",
          label: "Question",
          content: typeof payload?.question === "string" ? payload.question : "The agent is waiting for input.",
          tone: "warning",
          badges: [typeof payload?.kind === "string" ? payload.kind : "user input"],
        });
        return;
      case "agent.question.answered":
        pushEntry({
          id: `event:${event.id}`,
          role: "user",
          kind: "answer",
          label: "Answer",
          content: typeof payload?.answer === "string" ? payload.answer : "",
          tone: "default",
          branchable: false,
        });
        return;
      case "agent.run.resumed":
        pushEntry({
          id: `event:${event.id}`,
          role: "system",
          kind: "status",
          label: "Run status",
          title: "Agent resumed",
          content: "Execution resumed after input or manual restart.",
          tone: "info",
        });
        return;
      case "agent.run.completed":
        pushEntry({
          id: `event:${event.id}`,
          role: "assistant",
          kind: "summary",
          label: "Run summary",
          title: "Execution completed",
          content: summarizeAgentEvent(event.event_type, payload) ?? "The agent completed the run.",
          tone: "success",
          sections: payload ? buildRunSummarySections(payload) : [],
        });
        return;
      case "agent.run.failed":
        pushEntry({
          id: `event:${event.id}`,
          role: "system",
          kind: "error",
          label: "Run failed",
          title: "Execution failed",
          content: summarizeAgentEvent(event.event_type, payload) ?? "The agent run failed.",
          tone: "error",
        });
        return;
      default:
        return;
    }
  });

  return entries;
}

export function mapConversationEventMessages(events: ConversationEventView[]): ChatHistoryMessage[] {
  return buildEventTimelineEntries(events)
    .sort((left, right) => {
      if (left.sortTime !== right.sortTime) {
        return left.sortTime - right.sortTime;
      }
      return left.sortOrder - right.sortOrder;
    })
    .map((entry) => {
      const { sortTime, sortOrder, ...timelineEntry } = entry;
      return timelineEntry;
    });
}

export function buildCouncilData(
  artifacts: ConversationArtifactView[]
): Map<string, CouncilResponse> {
  const modelResponsesByRun = new Map<string, CouncilResponse["model_responses"]>();
  const synthesizedByRun = new Map<string, { synthesized: string; synthesizer_model: string; synthesizer_usage?: UsageStats }>();

  for (const artifact of artifacts) {
    if (!artifact.run_id || !artifact.content_json) continue;

    if (artifact.artifact_type === "council.model.response") {
      const current = modelResponsesByRun.get(artifact.run_id) ?? [];
      current.push({
        model: String(artifact.content_json.model ?? "unknown-model"),
        content: String(artifact.content_json.content ?? ""),
        usage: readUsageStats(artifact.content_json.usage),
        error: artifact.content_json.error ? String(artifact.content_json.error) : undefined,
      });
      modelResponsesByRun.set(artifact.run_id, current);
    }

    if (artifact.artifact_type === "council.synthesis.response") {
      synthesizedByRun.set(artifact.run_id, {
        synthesized: String(artifact.content_json.content ?? ""),
        synthesizer_model: String(artifact.content_json.model ?? "unknown-model"),
        synthesizer_usage: readUsageStats(artifact.content_json.usage),
      });
    }
  }

  const councilDataByRun = new Map<string, CouncilResponse>();
  for (const [runId, modelResponses] of modelResponsesByRun.entries()) {
    const synthesis = synthesizedByRun.get(runId);
    if (!synthesis) continue;
    councilDataByRun.set(runId, {
      conversation_id: undefined,
      run_id: runId,
      model_responses: modelResponses,
      synthesized: synthesis.synthesized,
      synthesizer_model: synthesis.synthesizer_model,
      synthesizer_usage: synthesis.synthesizer_usage,
      total_usage: mergeUsageStats([
        ...modelResponses.map((response) => response.usage),
        synthesis.synthesizer_usage,
      ]),
    });
  }

  return councilDataByRun;
}

export function mapConversationMessages(
  detail: ConversationDetail,
  artifacts: ConversationArtifactView[],
  events: ConversationEventView[] = [],
): ChatHistoryMessage[] {
  const councilDataByRun = buildCouncilData(artifacts);
  const usageByEventId = parseUsageByEventId(events);

  const messageEntries: TimelineEntry[] = detail.messages
    .filter(isRenderableChatMessage)
    .map((message, index) => {
      const councilData =
        message.role === "assistant" && message.run_id
          ? councilDataByRun.get(message.run_id) ?? undefined
          : undefined;

      return {
        id: message.id,
        runId: message.run_id,
        role: message.role,
        kind: "message",
        content: message.content,
        createdAt: message.created_at,
        branchable: message.role === "user",
        councilData,
        usage:
          message.role === "assistant"
            ? councilData?.total_usage ?? (message.source_event_id ? usageByEventId.get(message.source_event_id) : undefined)
            : undefined,
        sortTime: toSortTime(message.created_at),
        sortOrder: 100000 + index,
      };
    });

  const eventEntries = buildEventTimelineEntries(events);

  return [...messageEntries, ...eventEntries]
    .sort((left, right) => {
      if (left.sortTime !== right.sortTime) {
        return left.sortTime - right.sortTime;
      }
      return left.sortOrder - right.sortOrder;
    })
    .map((entry) => {
      const { sortTime, sortOrder, ...timelineEntry } = entry;
      void sortTime;
      void sortOrder;
      return timelineEntry;
    });
}