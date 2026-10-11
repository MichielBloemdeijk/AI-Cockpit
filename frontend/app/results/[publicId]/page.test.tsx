// @vitest-environment jsdom

import React, { act, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";

import ResultPage from "./page";
import { watchResult } from "@/lib/result-live";

const api = vi.hoisted(() => ({
  checkAuth: vi.fn(),
  getResult: vi.fn(),
  acknowledgePushDelivery: vi.fn(),
}));

vi.mock("@/lib/api", () => api);
vi.mock("@/lib/result-live", () => ({ watchResult: vi.fn(() => () => {}) }));
vi.mock("next/link", () => ({
  default: ({ href, children }: React.PropsWithChildren<{ href: string }>) => <a href={href}>{children}</a>,
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

it("renders a focused document and excludes unsafe images and links", async () => {
  api.checkAuth.mockResolvedValue(true);
  api.getResult.mockResolvedValue({
    public_id: "public-1", url: "/results/public-1", app_slug: "documents",
    result_kind: "document.v1", status: "ready", task_status: "completed",
    task_status_reason: null, task_id: "task-1", active_execution_run_id: "run-1",
    revision_number: 1, title: "Screen report", summary: "Visible details", warning: null,
    content: { title: "Screen report", summary: "Visible details",
      body: "A **visible marker**. [Unsafe](javascript:alert(1)) ![remote](https://example.com/tracker.png)",
      source_event_sequences: [] },
  });
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(<Suspense fallback={null}><ResultPage params={Promise.resolve({ publicId: "public-1" })} /></Suspense>);
  });
  expect(container.textContent).toContain("Screen report");
  expect(container.textContent).toContain("visible marker");
  expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
  expect(container.querySelector("img")).toBeNull();
  expect(api.getResult).toHaveBeenCalledWith("public-1");
  await act(async () => { root.unmount(); });
  container.remove();
});

it("renders the minimal recipe through the registered result view", async () => {
  api.checkAuth.mockResolvedValue(true);
  api.getResult.mockResolvedValue({
    public_id: "recipe-1", app_slug: "recipes", result_kind: "recipe.v1", status: "ready",
    task_status: "completed", task_id: "task-1", title: "Tomato pasta", warning: "Earlier update failed",
    content: { title: "Tomato pasta", ingredients: ["100g pasta", "<script>untrusted</script>"],
      instructions: ["Boil pasta", "Combine with tomatoes"], uncertainty: "Likely tomato pasta",
      sources: [{ source_event_sequence: 4, title: "Recipe source", url: "https://example.com/recipe" }], source_event_sequences: [4] },
  });
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(<Suspense fallback={null}><ResultPage params={Promise.resolve({ publicId: "recipe-1" })} /></Suspense>);
  });
  expect(container.textContent).toContain("Recipes");
  expect(container.textContent).toContain("Ingredients");
  expect(container.textContent).toContain("Likely tomato pasta");
  expect(container.textContent).toContain("Earlier update failed");
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelectorAll("ol li")).toHaveLength(2);
  expect(container.querySelector('a[href="https://example.com/recipe"]')?.getAttribute("rel")).toBe("noopener noreferrer");
  await act(async () => { root.unmount(); });
  container.remove();
});

it("moves from building to ready over SSE, rejects stale snapshots and preserves task fallback", async () => {
  api.checkAuth.mockResolvedValue(true);
  const building = { public_id: "live", result_kind: "document.v1", title: "Pending", task_id: "task-1",
    status: "building", state_revision: 1, active_execution_run_id: "run", content: null };
  api.getResult.mockResolvedValue(building);
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => root.render(<Suspense><ResultPage params={Promise.resolve({ publicId: "live" })} /></Suspense>));
  const callbacks = vi.mocked(watchResult).mock.calls.at(-1)![1];
  expect(container.textContent).toContain("Building this result");
  await act(async () => callbacks.snapshot({ ...building, status: "ready", state_revision: 4,
    content: { title: "Pending", summary: "", body: "Live published body", source_event_sequences: [] } } as never));
  expect(container.textContent).toContain("Live published body");
  await act(async () => callbacks.snapshot(building as never));
  expect(container.textContent).toContain("Live published body");
  await act(async () => callbacks.connection("reconnecting"));
  expect(container.textContent).toContain("Reconnecting live updates");
  await act(async () => callbacks.snapshot({ ...building, state_revision: 5, result_kind: "future.v1", content: {} } as never));
  expect(container.textContent).toContain("Unsupported result type");
  expect(container.querySelector('a[href="/tasks/task-1"]')).not.toBeNull();
  await act(async () => root.unmount()); container.remove();
});

it("shows slow work after 30 seconds without treating it as a failure", async () => {
  vi.useFakeTimers();
  api.checkAuth.mockResolvedValue(true);
  api.getResult.mockResolvedValue({ public_id: "slow", result_kind: "document.v1", title: "Pending", task_id: "task-1",
    status: "updating", active_execution_run_id: "run", content: { title: "Old", summary: "", body: "Last good result", source_event_sequences: [] } });
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => root.render(<Suspense><ResultPage params={Promise.resolve({ publicId: "slow" })} /></Suspense>));
  await act(async () => vi.advanceTimersByTime(30000));
  expect(container.textContent).toContain("taking longer");
  expect(container.textContent).toContain("Last good result");
  expect(container.textContent).not.toContain("could not be completed");
  await act(async () => root.unmount()); container.remove();
});
