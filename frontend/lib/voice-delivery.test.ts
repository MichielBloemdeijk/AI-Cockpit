import { afterEach, describe, expect, it } from "vitest";
import { claimVoiceDelivery, getVoiceDelivery, setVoiceDeliveryState } from "./voice-delivery";

const storage = new Map<string, string>();
const localStorageMock = {
  getItem: (key: string) => storage.get(key) ?? null,
  setItem: (key: string, value: string) => { storage.set(key, value); },
};

describe("voice delivery persistence", () => {
  afterEach(() => storage.clear());

  it("keeps a failed item retryable and records spoken only after success", async () => {
    Object.assign(globalThis, { localStorage: localStorageMock, indexedDB: undefined });
    expect(await getVoiceDelivery("event-1")).toBeNull();
    await setVoiceDeliveryState("event-1", "pending", "delivery-1");
    expect((await getVoiceDelivery("event-1"))?.state).toBe("pending");
    await setVoiceDeliveryState("event-1", "spoken", "delivery-1");
    expect((await getVoiceDelivery("event-1"))?.state).toBe("spoken");
  });

  it("atomically claims a delivery once across concurrent sources", async () => {
    Object.assign(globalThis, { localStorage: localStorageMock, indexedDB: undefined });
    const claims = await Promise.all([
      claimVoiceDelivery("event-race", "delivery-1"),
      claimVoiceDelivery("event-race", "delivery-1"),
    ]);
    expect(claims.filter(Boolean)).toHaveLength(1);
    expect((await getVoiceDelivery("event-race"))?.state).toBe("queued");
  });

  it("allows exactly one retry after a failed delivery returns to pending", async () => {
    Object.assign(globalThis, { localStorage: localStorageMock, indexedDB: undefined });
    expect(await claimVoiceDelivery("event-retry")).toBe(true);
    await setVoiceDeliveryState("event-retry", "pending");
    const claims = await Promise.all([claimVoiceDelivery("event-retry"), claimVoiceDelivery("event-retry")]);
    expect(claims.filter(Boolean)).toHaveLength(1);
  });
});
