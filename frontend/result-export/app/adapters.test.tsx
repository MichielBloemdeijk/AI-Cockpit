// @vitest-environment jsdom

import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";

// Both shells render the same screens; only how a page finds its data differs.
const screens = vi.hoisted(() => ({
  TaskDetailScreen: vi.fn(({ taskId }: { taskId: string }) => <p>task {taskId}</p>),
  AppsScreen: vi.fn(({ appsAvailable }: { appsAvailable: boolean }) => <p>apps {String(appsAvailable)}</p>),
}));
vi.mock("@/components/screens/TaskDetailScreen", () => ({ TaskDetailScreen: screens.TaskDetailScreen }));
vi.mock("@/components/screens/AppsScreen", () => ({ AppsScreen: screens.AppsScreen }));
vi.mock("@/lib/api/auth", () => ({ checkAuth: vi.fn().mockResolvedValue(true) }));

import TaskShell from "./task-shell/page";
import StaticApps from "./workspace/apps/page";
import FullApps from "../../app/(app)/workspace/apps/page";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function render(element: React.ReactElement) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(element); });
  return { container, unmount: async () => { await act(async () => root.unmount()); container.remove(); } };
}

afterEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/");
});

it("decodes a direct task id from the static shell's path", async () => {
  window.history.replaceState(null, "", "/tasks/task%2F42");
  const view = await render(<TaskShell />);
  expect(view.container.textContent).toContain("task task/42");
  expect(screens.TaskDetailScreen.mock.calls.at(-1)?.[0]).toEqual({ taskId: "task/42" });
  await view.unmount();
});

it("makes no task request for a malformed path", async () => {
  window.history.replaceState(null, "", "/tasks/%E0%A4%A/extra");
  const view = await render(<TaskShell />);
  expect(screens.TaskDetailScreen).not.toHaveBeenCalled();
  expect(view.container.textContent).toContain("Loading task");
  await view.unmount();
});

it("offers apps only where an app host exists", async () => {
  const full = await render(<FullApps />);
  expect(full.container.textContent).toBe("apps true");
  await full.unmount();
  const companion = await render(<StaticApps />);
  expect(companion.container.textContent).toContain("apps false");
  await companion.unmount();
});
