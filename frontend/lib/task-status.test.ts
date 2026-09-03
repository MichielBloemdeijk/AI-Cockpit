import { describe, expect, it } from "vitest";

import type { TaskSummary } from "./api";
import { ACTIVE_TASK_STATUSES, isOverdueReminder, taskStatusLabel } from "./task-status";


function task(overrides: Partial<TaskSummary> = {}): TaskSummary {
  return {
    id: "task-1",
    origin_surface: "test",
    origin_conversation_id: null,
    origin_turn_id: null,
    goal: "Take medicine",
    status: "waiting_until",
    status_reason: null,
    creation_order: 1,
    current_attempt_id: null,
    cancellation_requested_at: null,
    importance: "normal",
    presentation_policy: "critical_only",
    terminal_summary: null,
    waiting_until: "2026-09-03T08:00:00Z",
    resume_at: "2026-09-03T08:00:00Z",
    wait_reason: "reminder",
    scheduled_delivery: { kind: "reminder" },
    scheduled_delivered_at: null,
    queue_position: null,
    created_at: "2026-09-03T07:00:00Z",
    updated_at: "2026-09-03T07:00:00Z",
    finished_at: null,
    ...overrides,
  };
}

describe("task status presentation", () => {
  it("keeps every durable waiting state active", () => {
    expect(ACTIVE_TASK_STATUSES.has("waiting_for_worker")).toBe(true);
    expect(ACTIVE_TASK_STATUSES.has("waiting_until")).toBe(true);
  });

  it("labels only due reminder waits as overdue", () => {
    const now = Date.parse("2026-09-03T08:01:00Z");
    expect(isOverdueReminder(task(), now)).toBe(true);
    expect(taskStatusLabel(task(), now)).toBe("overdue reminder");
    expect(isOverdueReminder(task({ scheduled_delivery: null }), now)).toBe(false);
    expect(isOverdueReminder(task({ resume_at: "2026-09-03T09:00:00Z" }), now)).toBe(false);
  });
});
