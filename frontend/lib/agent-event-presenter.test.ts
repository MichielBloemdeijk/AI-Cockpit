import { describe, expect, it } from "vitest";

import { summarizeAgentEvent, summarizeToolCall, summarizeToolResult } from "./agent-event-presenter";

describe("agent-event-presenter", () => {
  it("preserves long run goals in agent summaries", () => {
    const goal = "Create a web app that changes colors when clicked. The app will display a clickable element that cycles through a predefined palette each time the user interacts with it.";

    expect(summarizeAgentEvent("agent.run.started", { goal })).toBe(goal);
  });

  it("preserves long app initialization targets in tool summaries", () => {
    const goal = "Create a web app that changes colors when clicked. The app will display a clickable element that cycles through a predefined palette each time the user interacts with it.";

    expect(summarizeToolCall("app_initialize", { goal })).toBe(`Preparing an app workspace for "${goal}"`);
  });

  it("keeps file write summaries stable between live and persisted history", () => {
    const args = {
      path: "frontend/generated-apps/tic-tac-toe/styles.css",
      content: ".app {\n  color: red;\n}\n",
    };

    expect(summarizeToolCall("file_write", args)).toBe("Preparing to write tic-tac-toe/styles.css (3 lines)");
    expect(summarizeToolResult("file_write", args, true, "Updated tic-tac-toe/styles.css (3 lines).", null)).toBe(
      "Updated tic-tac-toe/styles.css (3 lines)",
    );
  });
});