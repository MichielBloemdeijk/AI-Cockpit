import { expect, it, vi } from "vitest";
import { appStorageClient, connectAppStorage } from "./app-storage";

it("uses a scoped credential without cookies, and preserves optimistic revisions", async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ value: { x: 1 }, revision: 2 }) });
  const storage = appStorageClient({ app_slug: "one", namespace: "state", credential: "restricted" }, fetcher);
  await storage.write("a/b", { x: 1 }, 1);
  expect(fetcher).toHaveBeenCalledWith("/api/app-catalog/one/data/state/a%2Fb", expect.objectContaining({
    credentials: "omit", method: "PUT", headers: { "Content-Type": "application/json", Authorization: "AppCapability restricted" },
    body: JSON.stringify({ value: { x: 1 }, expected_revision: 1 }),
  }));
  expect(await storage.read("one")).toEqual({ value: { x: 1 }, revision: 2 });
});

it("bootstraps by generated app identity and uses the actual catalog scope", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ app_slug: "generated-id", namespace: "state", credential: "scoped" }) })
    .mockResolvedValue({ ok: true, json: async () => ({ value: null, revision: 0 }) });
  const storage = await connectAppStorage("id", true, fetcher);
  await storage.read("settings");
  expect(fetcher.mock.calls[0][0]).toBe("/api/apps/id/storage-capability");
  expect(fetcher.mock.calls[0][1]).toMatchObject({ credentials: "include", body: '{"read_only":true}' });
  expect(fetcher.mock.calls[1][0]).toBe("/api/app-catalog/generated-id/data/state/settings");
  expect(fetcher.mock.calls[1][1]).toMatchObject({ credentials: "omit", headers: { Authorization: "AppCapability scoped" } });
});
