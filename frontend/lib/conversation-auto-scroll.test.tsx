// @vitest-environment jsdom

import React, { useRef } from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useConversationAutoScroll } from "./conversation-auto-scroll";


globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function Harness({
  ready,
  conversationId,
  newestMessageId,
}: {
  ready: boolean;
  conversationId: string | null;
  newestMessageId: string | null;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useConversationAutoScroll(ref, ready, conversationId, newestMessageId);
  return <div ref={ref} data-testid="conversation" />;
}

describe("useConversationAutoScroll", () => {
  let container: HTMLDivElement;
  let root: Root;
  let scrollTo: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    scrollTo = vi.fn();
    HTMLElement.prototype.scrollTo = scrollTo;
    Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
      configurable: true,
      get: () => 640,
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
      await Promise.resolve();
    });
    container.remove();
  });

  it("starts at the newest message and smoothly follows later messages", async () => {
    await act(async () => {
      root.render(<Harness ready={false} conversationId="log-1" newestMessageId="message-1" />);
      await Promise.resolve();
    });
    expect(scrollTo).not.toHaveBeenCalled();

    await act(async () => {
      root.render(<Harness ready conversationId="log-1" newestMessageId="message-1" />);
      await Promise.resolve();
    });
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 640, behavior: "auto" });

    await act(async () => {
      root.render(<Harness ready conversationId="log-1" newestMessageId="message-2" />);
      await Promise.resolve();
    });
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 640, behavior: "smooth" });
  });

  it("positions a newly selected conversation immediately", async () => {
    await act(async () => {
      root.render(<Harness ready conversationId="log-1" newestMessageId="message-1" />);
      await Promise.resolve();
    });
    await act(async () => {
      root.render(<Harness ready conversationId="log-2" newestMessageId="message-9" />);
      await Promise.resolve();
    });

    expect(scrollTo).toHaveBeenLastCalledWith({ top: 640, behavior: "auto" });
  });
});
