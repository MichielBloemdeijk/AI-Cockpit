// @vitest-environment jsdom

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const platformMocks = vi.hoisted(() => ({ listPlatformApps: vi.fn() }));

vi.mock("@/lib/app-platform", () => platformMocks);

import AppsPage from "./page";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

describe("AppsPage refresh behavior", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    vi.clearAllMocks();
    platformMocks.listPlatformApps.mockResolvedValue([{ slug: "notes", title: "Notes", version: 2,
      description: "Short notes", collections: ["note"], functions: [], files: false, jarvis_summary: "",
      url: "/apps/notes" }]);
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
    expect(platformMocks.listPlatformApps).toHaveBeenCalledTimes(1);
    expect(container.querySelector('a[href="/apps/notes"]')?.textContent).toBe("Open app");
    expect(container.textContent).toContain("v2");

    const normalPageShow = new Event("pageshow");
    Object.defineProperty(normalPageShow, "persisted", { value: false });
    await act(async () => {
      window.dispatchEvent(normalPageShow);
    });
    expect(platformMocks.listPlatformApps).toHaveBeenCalledTimes(1);

    const restoredPageShow = new Event("pageshow");
    Object.defineProperty(restoredPageShow, "persisted", { value: true });
    await act(async () => {
      window.dispatchEvent(restoredPageShow);
    });
    expect(platformMocks.listPlatformApps).toHaveBeenCalledTimes(2);
  });
});
