// @vitest-environment jsdom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import SettingsPage from "./page";

const api = vi.hoisted(() => ({
  getAccessSession: vi.fn(), getChatSettings: vi.fn(), getJarvisPreferences: vi.fn(),
  updateChatSettings: vi.fn(), updateJarvisPreferences: vi.fn(),
}));
const push = vi.hoisted(() => ({ inspectPushNotifications: vi.fn(), enablePushNotifications: vi.fn() }));
vi.mock("@/lib/api", () => api);
vi.mock("@/lib/push-notifications", () => push);
vi.mock("@/components/LinkPhone", () => ({ LinkPhone: () => <div>Owner pairing controls</div> }));
vi.mock("@/components/WorkerToolSettings", () => ({ WorkerToolSettings: () => <div>Owner worker tools</div> }));
vi.mock("next/link", () => ({ default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }));
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it("shows a retained phone link without requesting or displaying owner-only setup", async () => {
  api.getAccessSession.mockResolvedValue({ authenticated: true, scope: "device", device: { id: "phone", label: "Sander's phone", paired: true } });
  api.getJarvisPreferences.mockResolvedValue({ voice: "marin", concision: "concise", developer_mode: false });
  const container = document.createElement("div");
  const root = createRoot(container);
  try {
    await act(async () => { root.render(<SettingsPage />); });
    expect(container.textContent).toContain("This phone is linked");
    expect(container.textContent).toContain("Sander's phone");
    expect(container.querySelector('a[href="jarvis://setup"]')).not.toBeNull();
    expect(container.textContent).not.toContain("Owner pairing controls");
    expect(container.textContent).not.toContain("Owner worker tools");
    expect(api.getChatSettings).not.toHaveBeenCalled();
    expect(push.inspectPushNotifications).not.toHaveBeenCalled();
  } finally { await act(async () => root.unmount()); }
});
