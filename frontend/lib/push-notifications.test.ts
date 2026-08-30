import { describe, expect, it, vi } from "vitest";

import { decodeVapidPublicKey } from "./push-notifications";


describe("Web Push configuration", () => {
  it("decodes an unpadded URL-safe VAPID public key", () => {
    vi.stubGlobal("atob", (value: string) => Buffer.from(value, "base64").toString("binary"));
    expect(Array.from(decodeVapidPublicKey("AQID-_8"))).toEqual([1, 2, 3, 251, 255]);
    vi.unstubAllGlobals();
  });
});
