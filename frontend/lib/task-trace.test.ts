import { describe, expect, it } from "vitest";

import type { TaskEvent } from "./api";
import { taskEventToChatMessage } from "./task-trace";

function event(overrides: Partial<TaskEvent>): TaskEvent {
  return {
    id: "event-1",
    sequence: 12,
    task_id: "task-1",
    attempt_id: "attempt-12345678",
    execution_run_id: "run-1",
    native_item_id: null,
    event_type: "task.progress",
    payload: { summary: "Inspected the repository", status: "running" },
    causality: null,
    created_at: "2026-08-27T10:00:00Z",
    ...overrides,
  };
}

describe("task Agent trace projection", () => {
  it("shows native observed messages and broker calls as evidence", () => {
    const message = taskEventToChatMessage(event({
      event_type: "agent.native.item",
      payload: { item: { type: "agentMessage", text: "The file is ready." } },
    }));
    const tool = taskEventToChatMessage(event({
      event_type: "agent.native.item",
      payload: { item: { type: "mcpToolCall", server: "jarvis_task", tool: "file_write", status: "completed" } },
    }));
    expect(message.content).toBe("The file is ready.");
    expect(tool.content).toContain("jarvis_task.file_write");
  });

  it("renders representative progress, question, answer, and terminal events", () => {
    const trace = [
      event({}),
      event({ id: "event-2", sequence: 13, event_type: "task.requires_input", payload: { question: "Which branch?" } }),
      event({ id: "event-3", sequence: 14, event_type: "task.question.answered", payload: { answer: "main" } }),
      event({ id: "event-4", sequence: 15, event_type: "task.completed", payload: { summary: "Done", status: "completed" } }),
    ].map(taskEventToChatMessage);

    expect(trace.map((item) => item.kind)).toEqual(["status", "question", "answer", "summary"]);
    expect(trace[0]).toMatchObject({ content: "Inspected the repository", status: "running", tone: "info" });
    expect(trace[1]).toMatchObject({ content: "Which branch?", tone: "warning" });
    expect(trace[2]).toMatchObject({ role: "user", content: "main" });
    expect(trace[3]).toMatchObject({ content: "Done", tone: "success" });
  });

  it("renders failed terminal events as errors", () => {
    expect(taskEventToChatMessage(event({ event_type: "task.failed", payload: { summary: "Build failed" } })))
      .toMatchObject({ kind: "error", content: "Build failed", tone: "error" });
  });
});
