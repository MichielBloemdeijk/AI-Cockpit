// @vitest-environment jsdom

import React, { act, Suspense } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";

import TaskDetailPage from "./page";
import type { TaskDetail } from "@/lib/api";

const api = vi.hoisted(() => ({
  getTask: vi.fn(),
  continueTask: vi.fn(),
  controlTask: vi.fn(),
  answerTaskQuestion: vi.fn(),
  acknowledgePushDelivery: vi.fn(),
}));

vi.mock("@/lib/api", () => api);
vi.mock("@/components/ChatMessage", () => ({ ChatMessage: () => null }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={String(href)} {...props}>{children}</a>
  ),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const retainedTask = {
  id: "task-1", goal: "Original request", status: "completed", status_reason: null,
  origin_surface: "tasks_ui", origin_conversation_id: null,
  cancellation_requested_at: null, terminal_summary: "Finished result",
  current_run_id: "run-1", control_revision: 0, pending_control_id: null,
  importance: "normal", queue_position: null,
  worker_health: { status: "offline", worker_id: null, heartbeat_at: null },
  events: [], artifacts: [], questions: [], attempts: [], operations: [], controls: [], targets: [],
  runs: [{ id: "run-1", ordinal: 1, status: "completed", terminal_summary: "Finished result" }],
  turns: [{ id: "turn-1", role: "user", content: "Original request" }],
} as unknown as TaskDetail;

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

it("shows exact connector arguments and submits an explicit human decision", async () => {
  api.getTask.mockResolvedValue({ ...retainedTask, status: "waiting_for_input", questions: [{
    id: "permission-1", state: "asked", question: "Approve mail/send?",
    permission: { connector: "mail", account: "primary", tool: "send", arguments: { to: "test@example.com", body: "Hello" } },
  }] });
  api.answerTaskQuestion.mockResolvedValue({});
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<Suspense fallback={null}><TaskDetailPage params={Promise.resolve({ taskId: "task-1" })} /></Suspense>); });
  expect(container.textContent).toContain("test@example.com");
  const approve = Array.from(container.querySelectorAll("button")).find(button => button.textContent === "Approve this action")!;
  await act(async () => { approve.click(); });
  expect(api.answerTaskQuestion).toHaveBeenCalledWith("task-1", "permission-1", "approve");
  await act(async () => { root.unmount(); });
  container.remove();
});

it("reopens retained task history without starting another execution", async () => {
  api.getTask.mockResolvedValue(retainedTask);
  const params = Promise.resolve({ taskId: "task-1" });

  for (let opening = 0; opening < 2; opening += 1) {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root: Root = createRoot(container);
    await act(async () => {
      root.render(<Suspense fallback={null}><TaskDetailPage params={params} /></Suspense>);
    });
    expect(container.textContent).toContain("Original request");
    expect(container.textContent).toContain("Finished result");
    await act(async () => { root.unmount(); });
    container.remove();
  }

  expect(api.getTask).toHaveBeenCalledTimes(2);
  expect(api.getTask).toHaveBeenCalledWith("task-1");
  expect(api.continueTask).not.toHaveBeenCalled();
  expect(api.controlTask).not.toHaveBeenCalled();
});

it("requires an audited reason before accepting a changed shared directory", async () => {
  api.getTask.mockResolvedValue({
    ...retainedTask,
    status: "interrupted",
    terminal_summary: "A shared target changed while this task did not hold its lease.",
    attempts: [{ id: "attempt-1", attempt_number: 1, terminal_reason: "resource_changed" }],
  });
  api.controlTask.mockResolvedValue({});
  vi.spyOn(window, "prompt").mockReturnValue("I reviewed the new note");
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(<Suspense fallback={null}><TaskDetailPage params={Promise.resolve({ taskId: "task-1" })} /></Suspense>);
  });
  const retry = Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Retry"));
  await act(async () => { retry?.click(); });
  expect(api.controlTask).toHaveBeenCalledWith(
    "task-1", "retry", { accept_resource_changes: true }, "run-1", 0, "I reviewed the new note",
  );
  await act(async () => { root.unmount(); });
  container.remove();
});

it("renders a typed workflow result with its retained evidence references", async () => {
  api.getTask.mockResolvedValue({
    ...retainedTask,
    artifacts: [{
      id: "artifact-1", artifact_type: "workflow_result", mime_type: "application/json",
      title: "Daily report", uri: null, content_text: null,
      content_json: { schema: "text-report.v1", content: {
        title: "Daily report", body: "The requested summary is ready.",
        source_event_sequences: [2, 4],
      } },
    }],
  });
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(<Suspense fallback={null}><TaskDetailPage params={Promise.resolve({ taskId: "task-1" })} /></Suspense>);
  });
  expect(container.textContent).toContain("Daily report");
  expect(container.textContent).toContain("The requested summary is ready.");
  expect(container.textContent).toContain("Task evidence: #2, #4");
  await act(async () => { root.unmount(); });
  container.remove();
});

it.each(["answer", "update", "new"] as const)("submits explicit %s association without guessing a result identity", async (mode) => {
  api.getTask.mockResolvedValue({ ...retainedTask, results: [
    { public_id: "result-1", url: "/results/result-1", title: "First", revision_number: 1, status: "ready" },
    { public_id: "result-2", url: "/results/result-2", title: "Second", revision_number: 7, status: "ready" },
  ] });
  api.continueTask.mockResolvedValue({});
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<Suspense fallback={null}><TaskDetailPage params={Promise.resolve({ taskId: "task-1" })} /></Suspense>); });
  expect(container.querySelector('a[href="/results/result-2"]')).not.toBeNull();
  const result = container.querySelector<HTMLSelectElement>('select[aria-label="Selected result"]')!;
  await act(async () => { result.value = "result-2"; result.dispatchEvent(new Event("change", { bubbles: true })); });
  const intent = container.querySelector<HTMLSelectElement>('select[aria-label="Follow-up result intent"]')!;
  await act(async () => { intent.value = mode; intent.dispatchEvent(new Event("change", { bubbles: true })); });
  const textarea = container.querySelector("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(textarea, "Follow-up");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  const send = Array.from(container.querySelectorAll("button")).find(button => button.textContent === "Send")!;
  await act(async () => { send.click(); });
  expect(api.continueTask).toHaveBeenCalledWith("task-1", "Follow-up", mode === "new" ? { mode: "new" }
    : mode === "answer" ? { mode: "answer", public_id: "result-2" }
      : { mode: "update", public_id: "result-2", expected_revision: 7 });
  await act(async () => { root.unmount(); });
  container.remove();
});

it("does not offer answer-only completion for a legacy publishing-only workflow", async () => {
  api.getTask.mockResolvedValue({ ...retainedTask,
    runs: [{ id: "run-1", execution_kind: "workflow", configuration: { workflow: { capabilities: ["task.result.publish@1"] } } }],
    results: [{ public_id: "result-1", title: "First", url: "/results/result-1", revision_number: 1, status: "ready" }],
  });
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<Suspense fallback={null}><TaskDetailPage params={Promise.resolve({ taskId: "task-1" })} /></Suspense>); });
  const intent = container.querySelector<HTMLSelectElement>('select[aria-label="Follow-up result intent"]')!;
  expect(intent.value).toBe("update");
  expect(intent.querySelector('option[value="answer"]')).toBeNull();
  await act(async () => { root.unmount(); });
  container.remove();
});
