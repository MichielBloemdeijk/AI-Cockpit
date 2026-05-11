import { describe, expect, it } from "vitest";

import { summarizeAgentEvent, summarizeToolCall } from "./agent-event-presenter";

describe("agent-event-presenter", () => {
  it("preserves long run goals in agent summaries", () => {
    const goal = "Create a web app that changes colors when clicked. The app will display a clickable element that cycles through a predefined palette each time the user interacts with it.";

    expect(summarizeAgentEvent("agent.run.started", { goal })).toBe(goal);
  });

  it("preserves long app initialization targets in tool summaries", () => {
    const goal = "Create a web app that changes colors when clicked. The app will display a clickable element that cycles through a predefined palette each time the user interacts with it.";

    expect(summarizeToolCall("app_initialize", { goal })).toBe(`Preparing an app workspace for "${goal}"`);
  });
});