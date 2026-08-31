// @vitest-environment jsdom

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Sidebar } from "./Sidebar";

const navigation = vi.hoisted(() => ({
  pathname: "/jarvis",
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({ push: navigation.push }),
}));
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={String(href)} {...props}>{children}</a>
  ),
}));
vi.mock("@/lib/api", () => ({ logout: vi.fn().mockResolvedValue(undefined) }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

describe.each([320, 360, 390, 430])("phone navigation at %ipx", (width) => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => { root.render(<Sidebar />); });
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
  });

  it("keeps the four primary destinations touch-sized and exposes secondary navigation", async () => {
    const mobile = container.querySelector('nav[aria-label="Primary mobile navigation"]');
    expect(mobile?.textContent).toContain("Jarvis");
    expect(mobile?.textContent).toContain("Chat");
    expect(mobile?.textContent).toContain("Tasks");
    expect(mobile?.textContent).toContain("More");
    expect(mobile?.querySelectorAll(".min-h-12")).toHaveLength(4);

    const more = Array.from(mobile?.querySelectorAll("button") ?? []).find(
      (button) => button.textContent?.includes("More"),
    );
    await act(async () => { more?.click(); });

    const dialog = container.querySelector('[role="dialog"][aria-label="More navigation"]');
    expect(dialog?.textContent).toContain("Apps");
    expect(dialog?.textContent).toContain("Wiki");
    expect(dialog?.textContent).toContain("Settings");
    expect(dialog?.textContent).toContain("Logout");
    expect(more?.getAttribute("aria-expanded")).toBe("true");
  });
});
