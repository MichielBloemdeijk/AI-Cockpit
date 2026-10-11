// @vitest-environment jsdom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it } from "vitest";
import { resultRenderers } from "./result-renderers";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it("renders optional document sources and authenticated attachments without executing content", async () => {
  const container = document.createElement("div");
  const root = createRoot(container);
  const View = resultRenderers["document.v1"].View;
  await act(async () => root.render(<View publicId="public" content={{ title: "Document", body: "<script>secret()</script>\n\nSafe body [credential link](https://user:pass@example.com)", source_event_sequences: [],
    sources: [{ url: "https://example.com/reference", label: "Reference" }, { url: "javascript:alert(1)", label: "Unsafe" }],
    attachments: [{ artifact_id: "artifact", label: "Retained attachment" }],
  }} />));
  expect(container.textContent).toContain("Safe body");
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
  expect(container.querySelector('a[href*="user:pass"]')).toBeNull();
  expect(container.querySelector('a[href="https://example.com/reference"]')?.getAttribute("rel")).toBe("noopener noreferrer");
  expect(container.querySelector('a[href="/api/results/public/attachments/artifact"]')?.textContent).toBe("Retained attachment");
  await act(async () => root.unmount());
});
