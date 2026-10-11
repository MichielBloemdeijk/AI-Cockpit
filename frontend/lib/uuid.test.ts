import { afterEach, describe, expect, it, vi } from "vitest";

import { createUuid } from "./uuid";

afterEach(() => vi.unstubAllGlobals());

describe("createUuid", () => {
  it("uses the native secure-context implementation", () => {
    vi.stubGlobal("crypto", { randomUUID: () => "native-id" });
    expect(createUuid()).toBe("native-id");
  });

  it("generates a version 4 UUID on local HTTP origins", () => {
    vi.stubGlobal("crypto", {
      getRandomValues: (bytes: Uint8Array) => bytes.fill(0),
    });
    expect(createUuid()).toBe("00000000-0000-4000-8000-000000000000");
  });
});
