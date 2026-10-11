import { afterEach, describe, expect, it, vi } from "vitest";
import { loginReturnUrl, safeNextPath } from "./navigation";

afterEach(() => vi.unstubAllGlobals());

describe("login return navigation", () => {
  it.each([null, "", "https://elsewhere.test", "//elsewhere.test", "/\\elsewhere.test", "/\t/elsewhere.test", "javascript:alert(1)"])("rejects unsafe next %s", candidate => {
    expect(safeNextPath(candidate, "/tasks")).toBe("/tasks");
  });
  it("preserves the original local path, query and fragment", () => {
    const path = "/results/future-id?delivery=123&label=a%20b#body";
    expect(safeNextPath(path)).toBe(path);
    vi.stubGlobal("window", { location: { pathname: "/results/future-id", search: "?delivery=123&label=a%20b", hash: "#body" } });
    expect(new URLSearchParams(loginReturnUrl().split("?")[1]).get("next")).toBe(path);
  });
});
