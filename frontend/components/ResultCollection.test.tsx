// @vitest-environment jsdom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import ResultCollection from "./ResultCollection";

const api = vi.hoisted(() => ({ listCollectionResults: vi.fn(), setResultSaved: vi.fn(), checkAuth: vi.fn() }));
vi.mock("@/lib/api", () => api);
vi.mock("next/link", () => ({ default: ({ href, children }: React.PropsWithChildren<{ href: string }>) => <a href={href}>{children}</a> }));
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => vi.clearAllMocks());

it.each(["documents", "recipes"])("uses common pagination and saved control for %s", async slug => {
  api.checkAuth.mockResolvedValue(true);
  const card = { public_id: "one", url: "/results/one", title: "First result", status: "ready", actions: [{ id: "set_saved", value: { saved: false }, revision: 0 }] };
  api.listCollectionResults.mockResolvedValueOnce({ items: [card], next_cursor: "next" }).mockResolvedValueOnce({ items: [{ ...card, public_id: "two", title: "Second result" }], next_cursor: null });
  api.setResultSaved.mockResolvedValue({ value: { saved: true }, revision: 1 });
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<ResultCollection slug={slug} title={slug} />); });
  expect(api.listCollectionResults).toHaveBeenCalledWith(slug, { search: "", order: "updated" });
  const more = Array.from(container.querySelectorAll("button")).find(button => button.textContent === "Load more")!;
  await act(async () => more.click());
  expect(api.listCollectionResults).toHaveBeenLastCalledWith(slug, { search: "", order: "updated", cursor: "next" });
  expect(container.textContent).toContain("Second result");
  api.listCollectionResults.mockResolvedValue({ items: [{ ...card, actions: [{ id: "set_saved", value: { saved: true }, revision: 1 }] }], next_cursor: null });
  const save = container.querySelector<HTMLButtonElement>('button[aria-pressed="false"]')!;
  await act(async () => save.click());
  expect(api.setResultSaved).toHaveBeenCalledWith("one", true, 0);
  expect(container.querySelector('button[aria-pressed="true"]')).not.toBeNull();
  await act(async () => root.unmount()); container.remove();
});
