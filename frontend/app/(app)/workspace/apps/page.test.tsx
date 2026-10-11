// @vitest-environment jsdom

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  createGeneratedApp: vi.fn(),
  listGeneratedApps: vi.fn(),
  listAppCatalog: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

import AppsPage from "./page";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

describe("AppsPage refresh behavior", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    vi.clearAllMocks();
    apiMocks.listGeneratedApps.mockResolvedValue([]);
    apiMocks.listAppCatalog.mockResolvedValue([{ id: "documents", slug: "documents", title: "Documents", kind: "collection", route_path: "/collections/documents" },
      { id: "recipes", slug: "recipes", title: "Recipes", kind: "collection", route_path: "/collections/recipes" }]);
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    await act(async () => {
      root.render(<AppsPage />);
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it("refreshes on mount and only refreshes pageshow restores from cache", async () => {
    expect(apiMocks.listGeneratedApps).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("Maintained collections");
    expect(container.querySelector('a[href="/collections/recipes"]')).not.toBeNull();

    const normalPageShow = new Event("pageshow");
    Object.defineProperty(normalPageShow, "persisted", { value: false });
    await act(async () => {
      window.dispatchEvent(normalPageShow);
    });
    expect(apiMocks.listGeneratedApps).toHaveBeenCalledTimes(1);

    const restoredPageShow = new Event("pageshow");
    Object.defineProperty(restoredPageShow, "persisted", { value: true });
    await act(async () => {
      window.dispatchEvent(restoredPageShow);
    });
    expect(apiMocks.listGeneratedApps).toHaveBeenCalledTimes(2);
  });
});
