import type {
  ConversationArtifactView,
  ConversationEventView,
  UsageStats,
} from "./api";

const SINGLE_RESPONSE_EVENT_TYPES = new Set([
  "conversation.assistant.message.completed",
]);

const COUNCIL_USAGE_ARTIFACT_TYPES = new Set([
  "council.model.response",
  "council.synthesis.response",
]);

function mergeNestedRecords(
  usages: UsageStats[],
  field: keyof Pick<UsageStats, "prompt_tokens_details" | "completion_tokens_details" | "cost_details" | "server_tool_use">,
): Record<string, number> | undefined {
  const merged = new Map<string, number>();

  for (const usage of usages) {
    const nested = usage[field];
    if (!nested) continue;
    for (const [key, value] of Object.entries(nested)) {
      merged.set(key, (merged.get(key) ?? 0) + value);
    }
  }

  return merged.size > 0 ? Object.fromEntries(merged) : undefined;
}

export function readUsageStats(value: unknown): UsageStats | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value as UsageStats;
}

export function mergeUsageStats(usages: Array<UsageStats | undefined>): UsageStats | undefined {
  const present = usages.filter((usage): usage is UsageStats => Boolean(usage));
  if (present.length === 0) {
    return undefined;
  }

  const merged: UsageStats = {};

  for (const field of ["prompt_tokens", "completion_tokens", "total_tokens", "cost"] as const) {
    const total = present.reduce((sum, usage) => sum + (usage[field] ?? 0), 0);
    if (total > 0) {
      merged[field] = total;
    }
  }

  for (const field of ["prompt_tokens_details", "completion_tokens_details", "cost_details", "server_tool_use"] as const) {
    const nested = mergeNestedRecords(present, field);
    if (nested) {
      merged[field] = nested;
    }
  }

  if (present.some((usage) => usage.is_byok)) {
    merged.is_byok = true;
  }

  return merged;
}

export function formatUsageCost(cost?: number): string | null {
  if (typeof cost !== "number" || Number.isNaN(cost)) {
    return null;
  }
  if (cost === 0) {
    return "0";
  }
  if (Math.abs(cost) < 0.000001) {
    return cost.toExponential(2);
  }
  if (Math.abs(cost) < 0.01) {
    return cost.toFixed(6);
  }
  if (Math.abs(cost) < 1) {
    return cost.toFixed(4);
  }
  return cost.toFixed(2);
}

export function formatTokenCount(tokens?: number): string | null {
  if (typeof tokens !== "number" || Number.isNaN(tokens)) {
    return null;
  }
  return new Intl.NumberFormat().format(tokens);
}

export function summarizeConversationUsage(
  events: ConversationEventView[],
  artifacts: ConversationArtifactView[],
): { requestCount: number; totalUsage?: UsageStats } {
  const singleRequestUsages = events
    .filter((event) => SINGLE_RESPONSE_EVENT_TYPES.has(event.event_type))
    .map((event) => readUsageStats(event.payload_json?.usage));

  const councilRequestUsages = artifacts
    .filter((artifact) => COUNCIL_USAGE_ARTIFACT_TYPES.has(artifact.artifact_type))
    .map((artifact) => readUsageStats(artifact.content_json?.usage));

  const usages = [...singleRequestUsages, ...councilRequestUsages];
  return {
    requestCount: usages.filter(Boolean).length,
    totalUsage: mergeUsageStats(usages),
  };
}