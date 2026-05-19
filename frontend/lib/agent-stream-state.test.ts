import { describe, expect, it } from "vitest";

import {
  buildAgentStatusFromStreamUpdate,
  mergeAgentStatus,
} from "./agent-stream-state";
import type { AgentStatus } from "./chat-state-types";

describe("agent stream state helpers", () => {
  it("suppresses low-signal progress summaries", () => {
    expect(buildAgentStatusFromStreamUpdate({
      kind: "progress",
      run_id: "run-1",
      step: 1,
      content: "Use tool file_read",
    })).toBeNull();
  });

  it("keeps a higher-priority status when merging", () => {
    const current: AgentStatus = {
      title: "Waiting for input",
      content: "Need approval",
      tone: "warning",
      runId: "run-1",
      active: false,
    };

    const next: AgentStatus = {
      title: "Using a tool",
      content: "Reading files",
      tone: "info",
      runId: "run-1",
      active: true,
    };

    expect(mergeAgentStatus(current, next)).toEqual(current);
  });

  it("maps tool stream updates to active tool status", () => {
    expect(buildAgentStatusFromStreamUpdate({
      kind: "tool_progress",
      run_id: "run-1",
      step: 1,
      tool: "file_read",
      content: "Loaded 1200 bytes from frontend/lib/hooks.ts",
    })).toEqual({
      title: "Using a tool",
      content: "Loaded 1200 bytes from frontend/lib/hooks.ts",
      tone: "info",
      runId: "run-1",
      active: true,
    });
  });
});