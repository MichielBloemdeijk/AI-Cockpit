// @vitest-environment jsdom

import React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentStatusCard } from "./AgentStatusCard";
import { ChatInput } from "./ChatInput";
import { ChatMessage } from "./ChatMessage";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

describe("chat UI components", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
      await Promise.resolve();
    });
    container.remove();
  });

  it("renders user branch actions and calls onEdit", async () => {
    const onEdit = vi.fn();

    await act(async () => {
      root.render(
        <ChatMessage
          message={{
            id: "message-1",
            role: "user",
            kind: "message",
            content: "Refactor this component",
            branchable: true,
          }}
          onEdit={onEdit}
        />
      );
      await Promise.resolve();
    });

    const editButton = Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Edit and branch"));
    expect(editButton).toBeTruthy();

    await act(async () => {
      editButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(onEdit).toHaveBeenCalledTimes(1);
  });

  it("renders expandable thought messages", async () => {
    await act(async () => {
      root.render(
        <ChatMessage
          message={{
            id: "thought-1",
            role: "assistant",
            kind: "thought",
            title: "Step 1",
            content: "Inspecting the existing timeline projector.",
          }}
        />
      );
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Step 1");
    const toggle = container.querySelector("button");

    await act(async () => {
      toggle?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Inspecting the existing timeline projector.");
  });

  it("parses slash commands and sends the normalized payload", async () => {
    const onSend = vi.fn();

    await act(async () => {
      root.render(<ChatInput onSend={onSend} onStop={() => {}} loading={false} />);
      await Promise.resolve();
    });

    const textarea = container.querySelector("textarea");
    expect(textarea).toBeTruthy();

    await act(async () => {
      if (textarea) {
        const setValue = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")?.set;
        setValue?.call(textarea, "/search agent loop");
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
      }
      await Promise.resolve();
    });

    const sendButton = container.querySelector("button");
    await act(async () => {
      sendButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(onSend).toHaveBeenCalledWith({ mode: "workspace_search", value: "agent loop" });
  });

  it("renders agent status cards with the provided content", async () => {
    await act(async () => {
      root.render(
        <AgentStatusCard
          status={{
            title: "Run failed",
            content: "The sandbox rejected the last tool call.",
            tone: "error",
            active: false,
          }}
        />
      );
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Run failed");
    expect(container.textContent).toContain("The sandbox rejected the last tool call.");
  });
});