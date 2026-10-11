/** Generated apps receive this storage-only client, never database access.
 * Obtain a capability from trusted host/session code. Do not persist credentials
 * in localStorage, URLs, manifests or generated source.
 */
export async function connectAppStorage(appId: string, readOnly = false, fetcher: typeof fetch = fetch) {
  // This bootstrap is owner-session code. Subsequent data calls carry only the
  // returned capability. Keep it in memory; reconnect after expiry/revocation.
  const base = process.env.NEXT_PUBLIC_API_URL?.trim().replace(/\/$/, "") || "";
  const response = await fetcher(`${base}/api/apps/${encodeURIComponent(appId)}/storage-capability`, {
    method: "POST", credentials: "include", cache: "no-store", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ read_only: readOnly }),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return appStorageClient(await response.json(), fetcher);
}

export function appStorageClient(scope: { app_slug: string; namespace: string; credential: string }, fetcher: typeof fetch = fetch) {
  const base = process.env.NEXT_PUBLIC_API_URL?.trim().replace(/\/$/, "") || "";
  const url = (key: string) => `${base}/api/app-catalog/${encodeURIComponent(scope.app_slug)}/data/${encodeURIComponent(scope.namespace)}/${encodeURIComponent(key)}`;
  const request = async (key: string, init: RequestInit) => {
    const response = await fetcher(url(key), { ...init, credentials: "omit", cache: "no-store",
      headers: { "Content-Type": "application/json", Authorization: `AppCapability ${scope.credential}` } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json() as Promise<{ value: Record<string, unknown> | null; revision: number }>;
  };
  return Object.freeze({
    read: (key: string) => request(key, { method: "GET" }),
    write: (key: string, value: Record<string, unknown>, expectedRevision: number) => request(key, {
      method: "PUT", body: JSON.stringify({ value, expected_revision: expectedRevision }),
    }),
  });
}
