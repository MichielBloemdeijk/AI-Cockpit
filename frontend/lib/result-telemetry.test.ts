import { afterEach, expect, it, vi } from "vitest";
import { resultViewTelemetry } from "./result-telemetry";
import { postResultObservation } from "./api";

vi.mock("./api", () => ({ postResultObservation: vi.fn(async () => {}) }));
vi.mock("./uuid", () => ({ createUuid: () => "00000000-0000-4000-8000-000000000001" }));
afterEach(() => vi.clearAllMocks());

it("deduplicates render milestones but counts reconnects without content", () => {
  const report = resultViewTelemetry("public-id");
  report("first_snapshot", 1); report("first_snapshot", 2);
  report("first_ready", 1); report("first_ready", 2);
  report("sse_connect"); report("sse_reconnect"); report("sse_reconnect"); report("sse_fallback");
  expect(postResultObservation).toHaveBeenCalledTimes(6);
  const body = vi.mocked(postResultObservation).mock.calls[0][1];
  expect(Object.keys(body).sort()).toEqual(["duration_ms", "kind", "revision_number", "sequence", "view_id"]);
  expect(body.revision_number).toBe(1);
  expect(body.duration_ms).toBeGreaterThanOrEqual(0);
});

it("bounds connection reports and ignores transport rejection", async () => {
  vi.mocked(postResultObservation).mockRejectedValue(new Error("private diagnostics"));
  const report = resultViewTelemetry("public-id");
  for (let i = 0; i < 1002; ++i) report("sse_reconnect");
  await Promise.resolve();
  expect(postResultObservation).toHaveBeenCalledTimes(1000);
});
