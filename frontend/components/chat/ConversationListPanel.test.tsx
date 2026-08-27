// @vitest-environment jsdom

import React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { buildConversationSummary } from "../../lib/test-fixtures";
import { ConversationListPanel } from "./ConversationListPanel";


globalThis.IS_REACT_ACT_ENVIRONMENT = true;

describe("ConversationListPanel Jarvis history", () => {
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

  it("shows Jarvis lifecycle badges and emits the Jarvis filter and selection", async () => {
    const onSelectConversation = vi.fn();
    const onChangeKindFilter = vi.fn();
    const currentJarvis = buildConversationSummary("jarvis-current", {
      title: "Jarvis — 2026-08-27",
      conversation_kind: "jarvis_daily",
      lifecycle_status: "open",
      is_current_jarvis: true,
      is_live: false,
    });
    const closedJarvis = buildConversationSummary("jarvis-closed", {
      title: "Jarvis — 2026-08-26",
      conversation_kind: "jarvis_daily",
      lifecycle_status: "closed",
      is_current_jarvis: false,
      is_read_only: true,
    });

    await act(async () => {
      root.render(
        <ConversationListPanel
          mobile
          conversations={[currentJarvis, closedJarvis]}
          activeConversationId={null}
          showArchived={false}
          conversationKindFilter="all"
          onNewConversation={() => {}}
          onSelectConversation={onSelectConversation}
          onToggleShowArchived={() => {}}
          onChangeKindFilter={onChangeKindFilter}
        />,
      );
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Current Jarvis");
    expect(container.textContent).toContain("Jarvis · Read only");

    const jarvisFilter = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent === "Jarvis");
    const currentButton = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent?.includes("Jarvis — 2026-08-27"));

    await act(async () => {
      jarvisFilter?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      currentButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(onChangeKindFilter).toHaveBeenCalledWith("jarvis");
    expect(onSelectConversation).toHaveBeenCalledWith("jarvis-current");
  });
});
