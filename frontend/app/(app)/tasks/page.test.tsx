// @vitest-environment jsdom

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import TasksPage from "./page";

const api = vi.hoisted(() => ({
  listTasks: vi.fn(),
  getTaskWorkerHealth: vi.fn(),
  getTaskPool: vi.fn(),
  controlTaskPool: vi.fn(),
  dispatchTask: vi.fn(),
}));

vi.mock("@/lib/api", () => api);
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={String(href)} {...props}>{children}</a>
  ),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root;
let container: HTMLDivElement;

beforeEach(async () => {
  api.listTasks.mockResolvedValue([]);
  api.getTaskWorkerHealth.mockResolvedValue({ status: "online" });
  api.getTaskPool.mockResolvedValue({
    enabled: true, supervisor_generation: 1, supervisor_heartbeat_at: null,
    busy: 1, standby: 1, starting: 0, stale: 0, queue_depth: 0,
    waiting_for_worker: 0, max_busy: 8, saturated: false,
    workers: [{ id: "worker-123456", state: "busy", assignment_attempt_id: "attempt-1", heartbeat_at: "2026-09-23T00:00:00Z" }],
  });
  api.controlTaskPool.mockResolvedValue({ id: "command-1", state: "requested" });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root.render(<TasksPage />); });
});

afterEach(async () => {
  await act(async () => { root.unmount(); });
  container.remove();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

it("confirms disruptive pool controls and keeps repair directly accessible", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  const buttons = Array.from(container.querySelectorAll("button"));
  const restart = buttons.find((button) => button.textContent === "Restart pool");
  const repair = buttons.find((button) => button.textContent === "Repair capacity");
  const replace = buttons.find((button) => button.textContent === "Replace");
  expect(restart && repair && replace).toBeTruthy();

  await act(async () => { restart?.click(); });
  expect(api.controlTaskPool).not.toHaveBeenCalled();
  await act(async () => { repair?.click(); });
  expect(api.controlTaskPool).toHaveBeenCalledWith("repair_capacity", expect.any(String), undefined);
  confirm.mockReturnValue(true);
  await act(async () => { replace?.click(); });
  expect(api.controlTaskPool).toHaveBeenCalledWith("replace_worker", expect.any(String), "worker-123456");
});
