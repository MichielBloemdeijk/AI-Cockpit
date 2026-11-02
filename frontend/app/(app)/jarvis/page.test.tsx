// @vitest-environment jsdom

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { JarvisStateResponse } from "@/lib/api/jarvis";
import type { TaskSummary } from "@/lib/api/tasks";

const api = vi.hoisted(() => ({
  getJarvisState: vi.fn(),
  getJarvisLogEvents: vi.fn(),
  getJarvisSessionEvents: vi.fn(),
  getJarvisLiveEvents: vi.fn(),
  getJarvisPreferences: vi.fn(),
  getTaskEvents: vi.fn(),
  controlTask: vi.fn(),
  answerTaskQuestion: vi.fn(),
  acceptJarvisTextTurn: vi.fn(),
  updateJarvisPreferences: vi.fn(),
  startFreshJarvisLog: vi.fn(),
  closeJarvisLiveSession: vi.fn(),
  createJarvisLiveCall: vi.fn(),
  submitJarvisLiveTelemetry: vi.fn(async () => 0),
  acknowledgePushDelivery: vi.fn(),
  jarvisLiveUrl: (cursor: number) => `/api/jarvis/live?cursor=${cursor}`,
}));
vi.mock("@/lib/api/jarvis", () => api);
vi.mock("@/lib/api/tasks", () => api);
vi.mock("@/lib/api/push", () => api);
const live = vi.hoisted(() => ({ stop: vi.fn(async () => undefined) }));
vi.mock("@/lib/jarvis/live", () => ({
  JarvisLiveController: vi.fn(function JarvisLiveController() {
    return {
      current: () => ({ status: "idle", micEnabled: false, playbackActive: false, error: null }),
      ownsSession: () => false, startWithOptions: vi.fn(), stop: live.stop, prepare: vi.fn(),
      refreshPreparation: vi.fn(), recordBackendEvent: vi.fn(), recordLiveStreamState: vi.fn(),
    };
  }),
}));
vi.mock("@/lib/jarvis/wake-protocol", () => ({ configuredJarvisWakeModel: () => null }));
vi.mock("@/components/ChatMessage", () => ({
  ChatMessage: ({ message }: { message: { content: string } }) => <p>{message.content}</p>,
}));
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={String(href)} {...props}>{children}</a>,
}));

import JarvisPage from "./page";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

class SilentEventSource {
  onopen = null; onerror = null;
  addEventListener() {}
  close() {}
}

function task(overrides: Partial<TaskSummary>): TaskSummary {
  return { id: "task-1", goal: "Summarise the report", status: "failed", status_reason: null, terminal_summary: null,
    current_run_id: "run-3", control_revision: 5, pending_control_id: null, queue_position: null,
    retry_requires_change_acknowledgement: false, ...overrides } as TaskSummary;
}

function projection(tasks: TaskSummary[]): JarvisStateResponse {
  return {
    operational_day: "2026-11-02", state_revision: 1, situation_revision: 1, live_event_cursor: 4,
    current_log: { id: "log-1", title: "Today", messages: [
      { id: "m1", role: "user", content: "Good morning", created_at: "2026-11-02T08:00:00Z" },
    ] } as unknown as JarvisStateResponse["current_log"],
    tasks, pending_questions: [], pending_presentations: [], session: null,
    worker_health: { status: "online", worker_id: "w", heartbeat_at: null }, component_health: {},
    cursors: { conversation: 0, task: 0, session: 0 }, focused_task_id: null, recent_task_refs: [],
  };
}

let container: HTMLDivElement;
let root: Root;

async function renderPage() {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root.render(<JarvisPage />); });
  await act(async () => { await Promise.resolve(); });
}

beforeEach(() => {
  vi.stubGlobal("EventSource", SilentEventSource);
  Element.prototype.scrollTo = vi.fn();
  api.getJarvisState.mockResolvedValue(projection([task({})]));
  api.getJarvisLogEvents.mockResolvedValue({ conversation_id: "log-1", latest_sequence: 0, events: [] });
  api.getTaskEvents.mockResolvedValue({ latest_sequence: 0, events: [] });
  api.getJarvisLiveEvents.mockResolvedValue({ latest_sequence: 4, events: [] });
  api.getJarvisPreferences.mockResolvedValue({ voice: "marin", developer_mode: false });
  api.controlTask.mockResolvedValue({});
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  delete (window as Window & { JarvisPhoneVoice?: unknown }).JarvisPhoneVoice;
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it("starts in a browser, shows today's log and sends revision-checked task controls", async () => {
  await renderPage();
  expect(container.textContent).toContain("Good morning");
  expect(container.textContent).toContain("Arm");
  const retry = Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Retry")!;
  await act(async () => { retry.click(); });
  expect(api.controlTask).toHaveBeenCalledWith("task-1", "retry", {}, "run-3", 5);
});

it("sends a changed-resource retry to the task page for the owner's reason", async () => {
  api.getJarvisState.mockResolvedValue(projection([task({ retry_requires_change_acknowledgement: true })]));
  await renderPage();
  expect(Array.from(container.querySelectorAll("button")).some((button) => button.textContent === "Retry")).toBe(false);
  expect(container.querySelector('a[href="/tasks/task-1"]')?.textContent).toBeTruthy();
  expect(Array.from(container.querySelectorAll("a")).some((link) => link.textContent === "Review")).toBe(true);
});

it("stops the browser's own Live transport when the page goes away", async () => {
  await renderPage();
  await act(async () => root.unmount());
  await vi.waitFor(() => expect(live.stop).toHaveBeenCalled());
});

it("leaves Android's live conversation running when the native page navigates away", async () => {
  const bridge = { onmessage: null as ((event: { data: string }) => void) | null, postMessage: vi.fn() };
  (window as Window & { JarvisPhoneVoice?: unknown }).JarvisPhoneVoice = bridge;
  await renderPage();
  await act(async () => {
    bridge.onmessage?.({ data: JSON.stringify({ type: "audio_state", mode: "live", text: "Listening" }) });
  });
  expect(container.textContent).toContain("Stop Live");
  await act(async () => root.unmount());
  const commands = bridge.postMessage.mock.calls.map(([raw]) => JSON.parse(String(raw)).command);
  expect(commands).toEqual(["get_state"]);
  expect(live.stop).not.toHaveBeenCalled();
});
