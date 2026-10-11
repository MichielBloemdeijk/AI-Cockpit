import type { TaskSummary } from "@/lib/api";


export const ACTIVE_TASK_STATUSES = new Set<TaskSummary["status"]>([
  "queued",
  "waiting_for_worker",
  "running",
  "waiting_for_input",
  "waiting_until",
  "paused",
]);

export function isOverdueReminder(task: TaskSummary, now = Date.now()): boolean {
  const kind = task.scheduled_delivery?.kind;
  const dueAt = task.resume_at || task.waiting_until;
  if (task.status !== "waiting_until" || kind !== "reminder" || !dueAt) return false;
  const dueTime = Date.parse(dueAt);
  return Number.isFinite(dueTime) && dueTime <= now;
}

export function taskStatusLabel(task: TaskSummary, now = Date.now()): string {
  if (isOverdueReminder(task, now)) return "overdue reminder";
  return task.status.replaceAll("_", " ");
}
