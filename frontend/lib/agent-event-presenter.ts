function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function ellipsize(value: string, maxLength = 96): string {
  const trimmed = value.trim().replace(/\s+/g, " ");
  if (trimmed.length <= maxLength) {
    return trimmed;
  }
  return `${trimmed.slice(0, Math.max(0, maxLength - 1)).trimEnd()}...`;
}

function normalizeText(value: string): string {
  return value.trim().replace(/\s+/g, " ");
}

export function humanizeToolName(toolName: string): string {
  return toolName.replace(/_/g, " ");
}

function compactPath(value: string, maxLength = 88): string {
  const normalized = value.trim().replace(/\\/g, "/");
  const segments = normalized.split("/").filter(Boolean);
  if (segments.length >= 2) {
    return ellipsize(`${segments[segments.length - 2]}/${segments[segments.length - 1]}`, maxLength);
  }
  return ellipsize(segments[0] ?? normalized, maxLength);
}

function quoted(value: string | null | undefined, maxLength: number | null = 72): string | null {
  if (!value || !value.trim()) {
    return null;
  }
  const normalized = normalizeText(value);
  return `"${maxLength == null ? normalized : ellipsize(normalized, maxLength)}"`;
}

function shortOutputPreview(value: unknown, maxLength = 88): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const compact = value.trim().replace(/\s+/g, " ");
  if (!compact || compact.length > maxLength) {
    return null;
  }
  if (/^[\[{]/.test(compact) || compact.includes("function ") || compact.includes("class ")) {
    return null;
  }
  return compact;
}

export function formatAgentEventDetails(value: unknown): string | undefined {
  if (value == null) {
    return undefined;
  }
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function describeToolTarget(toolName: string, args: Record<string, unknown> | null): string | null {
  switch (toolName) {
    case "file_read":
    case "file_write":
      return typeof args?.path === "string" && args.path.trim() ? compactPath(args.path, 88) : null;
    case "workspace_search":
      return quoted(typeof args?.query === "string" ? args.query : null);
    case "shell_command":
      return quoted(typeof args?.command === "string" ? args.command : null, 88);
    case "python_execution": {
      const workingDirectory = typeof args?.working_directory === "string" ? args.working_directory.trim() : "";
      return workingDirectory ? `in ${ellipsize(workingDirectory, 72)}` : null;
    }
    case "app_initialize": {
      const goal = typeof args?.goal === "string" ? args.goal : null;
      const title = typeof args?.title === "string" ? args.title : null;
      const appSlug = typeof args?.app_slug === "string" ? args.app_slug : null;
      return quoted(goal || title || appSlug, null);
    }
    case "app_list":
      return "registered apps";
    default:
      return null;
  }
}

export function summarizeToolCall(toolName: string, argsValue: unknown): string {
  const args = asRecord(argsValue);
  const target = describeToolTarget(toolName, args);

  switch (toolName) {
    case "file_read":
      return target ?? "file";
    case "file_write":
      return target ?? "file";
    case "workspace_search":
      return target ? `Searching the workspace for ${target}` : "Searching the workspace";
    case "python_execution":
      return target ? `Running Python ${target}` : "Running Python";
    case "shell_command":
      return target ? `Running ${target}` : "Running a shell command";
    case "app_initialize":
      return target ? `Preparing an app workspace for ${target}` : "Preparing an app workspace";
    case "app_list":
      return "Checking existing app names";
    default:
      return `Running ${toolName.replace(/_/g, " ")}`;
  }
}

export function summarizeToolResult(
  toolName: string,
  argsValue: unknown,
  ok: boolean,
  outputValue: unknown,
  errorValue: unknown,
): string {
  const args = asRecord(argsValue);
  const target = describeToolTarget(toolName, args);

  if (!ok) {
    const errorPreview = shortOutputPreview(errorValue, 96);
    if (errorPreview) {
      return errorPreview;
    }
    return `Failed ${toolName.replace(/_/g, " ")}`;
  }

  const outputPreview = shortOutputPreview(outputValue, 96);

  switch (toolName) {
    case "file_read":
      return target ?? "file";
    case "file_write":
      return target ?? "file";
    case "workspace_search":
      return target ? `Searched the workspace for ${target}` : "Finished the workspace search";
    case "python_execution":
      return outputPreview ?? (target ? `Ran Python ${target}` : "Finished the Python run");
    case "shell_command":
      return outputPreview ?? (target ? `Ran ${target}` : "Finished the shell command");
    case "app_initialize":
      return outputPreview ?? (target ? `Prepared an app workspace for ${target}` : "Prepared the app workspace");
    case "app_list":
      return "Checked existing app names";
    default:
      return outputPreview ?? `Completed ${toolName.replace(/_/g, " ")}`;
  }
}

export function summarizeAgentEvent(eventType: string, payloadValue: unknown): string | undefined {
  const payload = asRecord(payloadValue);
  if (!payload) {
    return undefined;
  }

  switch (eventType) {
    case "agent.run.started":
      return typeof payload.goal === "string" && payload.goal.trim()
        ? normalizeText(payload.goal)
        : "The agent started working.";
    case "agent.plan.created":
      return typeof payload.summary === "string" && payload.summary.trim()
        ? normalizeText(payload.summary)
        : "The agent prepared a plan.";
    case "agent.thought.summary":
      return typeof payload.thought === "string" && payload.thought.trim()
        ? normalizeText(payload.thought)
        : undefined;
    case "agent.progress.summary":
      return typeof payload.summary === "string" && payload.summary.trim()
        ? normalizeText(payload.summary)
        : undefined;
    case "agent.tool.called":
      return summarizeToolCall(typeof payload.tool === "string" ? payload.tool : "tool", payload.arguments);
    case "agent.tool.completed":
      return summarizeToolResult(
        typeof payload.tool === "string" ? payload.tool : "tool",
        payload.arguments,
        payload.ok !== false,
        payload.output,
        payload.error,
      );
    case "agent.question.asked":
      return typeof payload.question === "string" && payload.question.trim()
        ? normalizeText(payload.question)
        : "The agent is waiting for input.";
    case "agent.run.completed":
      return typeof payload.summary === "string" && payload.summary.trim()
        ? normalizeText(payload.summary)
        : "The agent completed the run.";
    case "agent.run.failed":
      return typeof payload.error === "string" && payload.error.trim()
        ? normalizeText(payload.error)
        : "The agent run failed.";
    default:
      return undefined;
  }
}